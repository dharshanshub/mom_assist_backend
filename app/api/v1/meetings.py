from __future__ import annotations

import io
import uuid
from pathlib import Path

import httpx
import structlog
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.exceptions import BadRequestError, NotFoundError, UpstreamServiceError
from app.core.logging import get_correlation_id
from app.schemas.meeting import IndexRequest, IndexResponse, UploadResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/meetings", tags=["meetings"])

_DOC_DIR = Path(__file__).resolve().parents[3] / "data" / "documents"
_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB


# ── Serve a MOM document PDF ──────────────────────────────────────────────────

@router.get("/{meeting_id}/document", response_model=None)
async def get_document(meeting_id: str, request: Request) -> Response | FileResponse:
    """Serve a meeting's source MOM PDF.

    Always returns the raw PDF bytes so the frontend can fetch with an
    Authorization header and create a blob URL — avoids CORS issues with
    direct browser requests to blob storage.

    Priority:
      1. Azure Blob Storage → proxy bytes fetched via SAS URL
      2. Local disk fallback (dev / legacy seeded meetings)
      3. 404 if neither source has the file
    """
    request_id = get_correlation_id()

    if not all(c.isalnum() or c in "_-" for c in meeting_id):
        logger.warning("document_invalid_id", meeting_id=meeting_id, request_id=request_id)
        raise BadRequestError("Invalid meeting ID")

    blob_name = f"{meeting_id}.pdf"
    blob_service = request.app.state.blob_service

    # 1 — Try Blob Storage (Azure): fetch bytes server-side and proxy to client
    if blob_service.available:
        try:
            sas_url = await blob_service.get_sas_url(blob_name, expiry_minutes=60)
            if sas_url:
                async with httpx.AsyncClient(timeout=30) as http:
                    blob_resp = await http.get(sas_url)
                    blob_resp.raise_for_status()
                logger.info("document_served_blob", meeting_id=meeting_id, request_id=request_id)
                return Response(
                    content=blob_resp.content,
                    media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename={blob_name}", "Cache-Control": "private, max-age=3600"},
                )
        except UpstreamServiceError as exc:
            logger.warning("document_blob_fallback", reason=str(exc), meeting_id=meeting_id)
        except httpx.HTTPError as exc:
            logger.warning("document_blob_fetch_failed", reason=str(exc), meeting_id=meeting_id)

    # 2 — Fall back to local disk (dev environment / legacy seeded meetings)
    pdf_path = _DOC_DIR / blob_name
    if pdf_path.exists():
        logger.info("document_served_local", meeting_id=meeting_id, request_id=request_id)
        return FileResponse(
            str(pdf_path),
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename={blob_name}", "Cache-Control": "private, max-age=3600"},
        )

    logger.info("document_not_found", meeting_id=meeting_id, request_id=request_id)
    raise NotFoundError(f"Document not found for meeting '{meeting_id}'")


# ── Upload PDF → LLM extraction ───────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse)
async def upload_minutes(request: Request, file: UploadFile = File(...)) -> UploadResponse:
    """Accept a MOM PDF, save it to Blob Storage, run LLM extraction.

    The meeting_id is generated here (not at index time) so the PDF is already
    named and stored before the user reaches the human-review step.

    Raises:
        BadRequestError: for wrong type, oversized file, unreadable PDF, or empty text.
        UpstreamServiceError: if Blob upload or OpenAI extraction fails.
    """
    request_id = get_correlation_id()

    if file.content_type not in ("application/pdf", "application/octet-stream"):
        logger.warning(
            "upload_wrong_content_type",
            content_type=file.content_type,
            filename=file.filename,
            request_id=request_id,
        )
        raise BadRequestError("Only PDF files are supported")

    try:
        contents = await file.read()
    except Exception as exc:
        logger.error("upload_read_failed", error=str(exc), request_id=request_id)
        raise BadRequestError(f"Could not read uploaded file: {exc}") from exc

    if len(contents) == 0:
        raise BadRequestError("Uploaded file is empty")
    if len(contents) > _MAX_FILE_BYTES:
        raise BadRequestError(f"File too large — maximum {_MAX_FILE_BYTES // (1024 * 1024)} MB")

    # Generate meeting_id now so blob and Pinecone share the same key
    meeting_id = f"uploaded_{uuid.uuid4().hex[:10]}"
    blob_filename = f"{meeting_id}.pdf"

    logger.info(
        "upload_received",
        filename=file.filename,
        bytes=len(contents),
        meeting_id=meeting_id,
        request_id=request_id,
    )

    # Save PDF to Blob Storage (no-op locally when Azure is not configured)
    blob_service = request.app.state.blob_service
    try:
        await blob_service.upload(blob_filename, contents)
    except UpstreamServiceError:
        raise
    except Exception as exc:
        logger.error("upload_blob_failed", error=str(exc), request_id=request_id)
        raise UpstreamServiceError("azure_blob", f"Blob upload failed: {exc}") from exc

    # Extract text from PDF bytes
    try:
        reader = PdfReader(io.BytesIO(contents))
        pages = [page.extract_text() or "" for page in reader.pages]
        raw_text = "\n\n".join(pages).strip()
    except PdfReadError as exc:
        logger.error("pdf_corrupt", error=str(exc), filename=file.filename, request_id=request_id)
        raise BadRequestError(f"PDF is corrupted or password-protected: {exc}") from exc
    except Exception as exc:
        logger.error("pdf_parse_failed", error=str(exc), filename=file.filename, request_id=request_id)
        raise BadRequestError(f"Could not parse PDF: {exc}") from exc

    if not raw_text:
        raise BadRequestError(
            "PDF contains no extractable text — it may be a scanned image. "
            "Please use a text-based PDF."
        )

    logger.info(
        "pdf_parsed",
        filename=file.filename,
        chars=len(raw_text),
        pages=len(pages),
        request_id=request_id,
    )

    # LLM extraction
    extraction = request.app.state.extraction_service
    try:
        extracted = await extraction.extract(raw_text)
    except (BadRequestError, UpstreamServiceError):
        raise
    except Exception as exc:
        logger.error("extraction_unexpected", error=str(exc), request_id=request_id)
        raise UpstreamServiceError("openai", f"Unexpected extraction error: {exc}") from exc

    return UploadResponse(
        meeting_id=meeting_id,
        blob_filename=blob_filename,
        extracted=extracted,
        raw_text=raw_text,
    )


# ── Index validated meeting ───────────────────────────────────────────────────

@router.post("/index", response_model=IndexResponse)
async def index_meeting(body: IndexRequest, request: Request) -> IndexResponse:
    """Chunk, embed, and upsert a human-validated meeting record into Pinecone.

    Raises:
        BadRequestError: if raw_text is empty or produces no chunks.
        UpstreamServiceError: if OpenAI embedding or Pinecone upsert fails.
    """
    request_id = get_correlation_id()

    if not body.raw_text.strip():
        raise BadRequestError("raw_text must not be empty")

    logger.info(
        "index_request",
        meeting_id=body.meeting_id,
        blob_filename=body.blob_filename,
        date=body.meeting.date,
        request_id=request_id,
    )

    extraction = request.app.state.extraction_service
    try:
        result = await extraction.index(
            meeting_id=body.meeting_id,
            blob_filename=body.blob_filename,
            meeting=body.meeting,
            raw_text=body.raw_text,
        )
    except (BadRequestError, UpstreamServiceError):
        raise
    except Exception as exc:
        logger.error("index_unexpected", error=str(exc), request_id=request_id)
        raise UpstreamServiceError("pinecone", f"Unexpected indexing error: {exc}") from exc

    # Invalidate KB stats cache — total_meetings has increased
    if hasattr(request.app.state, "kb_stats_cache"):
        request.app.state.kb_stats_cache["cached_at"] = None

    logger.info(
        "index_done",
        meeting_id=result.meeting_id,
        chunks=result.chunks_indexed,
        date=body.meeting.date,
        request_id=request_id,
    )
    return result
