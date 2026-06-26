from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import structlog
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import ValidationError

from app.core.exceptions import BadRequestError, UpstreamServiceError
from app.schemas.meeting import ExtractedMeeting, ExtractedProject, IndexResponse
from app.services.embeddings import OpenAIEmbedder
from app.services.llm import OpenAILLM
from app.services.vector_store import PineconeStore

logger = structlog.get_logger(__name__)

_EXTRACT_SYSTEM = """You are an expert parser for corporate Minutes of Meeting (MOM) documents.

Extract the meeting header and every PROJECT discussed. Return ONLY a valid JSON object — no markdown, no explanation.

## What counts as a PROJECT
A project is a numbered agenda item describing a named initiative, programme, or workstream under review.
Strong signals that a subsection IS a project:
- Has a named initiative (e.g. "Cloud Migration", "CRM Implementation", "Customer Portal Redesign")
- Has a project ID (e.g. PRJ-0101, P-001, TKT-123, PROJ-007)
- Has a status (e.g. Active, On Hold, Completed, In Discovery, At Risk)
- Has a decision (e.g. APPROVED, DEFERRED, ON HOLD, REJECTED, FAST-TRACKED)
- Has action items assigned to named people

## What does NOT count as a project
NEVER treat these section headings as projects, even if they are numbered:
- General Discussion, AOB, Any Other Business, Conclusion, Summary
- Next Meeting, Date of Next Meeting, Sign-off, Approval of Minutes
- Attendees, Apologies, Matters Arising, Introductions
- Actions Log, Risk Register Review (unless it names a specific initiative)

When in doubt, include it — the user will delete it if it is wrong.

## JSON format
{
  "meeting_type": "One of: Portfolio Review, Board Meeting, Planning Session, Programme Retrospective, Client Steering Committee, All-Hands Update, Cross-Functional Sync, Other",
  "date": "YYYY-MM-DD — use today if not stated",
  "time": "HH:MM 24-hour format — empty string if not stated",
  "organizer": "Full name of the chairperson or meeting organizer — empty string if not stated",
  "location": "Room name or video platform — empty string if not stated",
  "projects": [
    {
      "project_name": "Exact initiative name as written — NOT a heading like 'Project Update'",
      "project_id": "Project ID if stated, else empty string",
      "status": "Status as stated (e.g. Active - On Track, On Hold, Completed) — empty string if not stated",
      "recommendation": "Recommendation made for this project — empty string if not stated",
      "decision": "Full decision text (e.g. APPROVED - subject to security audit sign-off) — empty string if not stated",
      "budget_allocation": "Budget figure if stated — empty string if not stated",
      "action_items": ["Owner Name: task description - Due: date", "..."]
    }
  ]
}

## Rules
- project_name must be the actual initiative name, never a generic heading
- Extract ALL projects found, even if some fields are missing
- action_items: format as 'Owner: task - Due: date' where information is available
- meeting_type: match to the nearest option from the allowed list
- date: strictly YYYY-MM-DD
"""

_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=150,
    separators=["\n\n", "\n", ". ", " ", ""],
)

_MAX_DOC_CHARS = 8000
_LIST_SEP = "||"


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


class MinutesExtractionService:
    def __init__(
        self,
        llm: OpenAILLM,
        embedder: OpenAIEmbedder,
        vector_store: PineconeStore,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._store = vector_store

    async def extract(self, raw_text: str) -> ExtractedMeeting:
        """Send MOM text to LLM and parse into ExtractedMeeting."""
        if not raw_text.strip():
            raise BadRequestError("Document text is empty — nothing to extract")

        logger.info("extraction_start", text_chars=len(raw_text))

        try:
            response = await self._llm.complete(
                system=_EXTRACT_SYSTEM,
                user=f"Minutes of meeting text:\n\n{raw_text[:_MAX_DOC_CHARS]}",
            )
        except UpstreamServiceError:
            raise
        except Exception as exc:
            logger.error("extraction_llm_failed", error=str(exc))
            raise UpstreamServiceError("openai", f"LLM call failed: {exc}") from exc

        cleaned = _strip_fences(response)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("extraction_json_parse_failed", error=str(exc), raw_preview=cleaned[:300])
            raise BadRequestError(f"LLM returned invalid JSON: {exc}") from exc

        # Coerce projects — handle missing keys gracefully
        raw_projects = data.get("projects", [])
        projects = []
        for p in raw_projects:
            if isinstance(p, dict) and p.get("project_name", "").strip():
                projects.append(ExtractedProject(
                    project_name=p.get("project_name", ""),
                    project_id=p.get("project_id", ""),
                    status=p.get("status", ""),
                    recommendation=p.get("recommendation", ""),
                    decision=p.get("decision", ""),
                    budget_allocation=p.get("budget_allocation", ""),
                    action_items=p.get("action_items", []) if isinstance(p.get("action_items"), list) else [],
                ))
        data["projects"] = projects

        try:
            meeting = ExtractedMeeting(**{k: v for k, v in data.items() if k != "projects"}, projects=projects)
        except ValidationError as exc:
            logger.error("extraction_schema_mismatch", errors=exc.errors())
            raise BadRequestError(f"Schema mismatch: {exc.error_count()} field error(s)") from exc

        logger.info(
            "extraction_done",
            meeting_type=meeting.meeting_type,
            date=meeting.date,
            organizer=meeting.organizer,
            projects_found=len(meeting.projects),
        )
        return meeting

    async def index(
        self,
        meeting_id: str,
        blob_filename: str,
        meeting: ExtractedMeeting,
        raw_text: str,
    ) -> IndexResponse:
        """Chunk, embed, and upsert a validated meeting into Pinecone.

        Metadata format matches the seeded data format so search_service.py
        _to_meeting_match() works identically for both uploaded and seeded docs.
        """
        indexed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "indexing_start",
            meeting_id=meeting_id,
            blob_filename=blob_filename,
            projects=len(meeting.projects),
            raw_text_chars=len(raw_text),
        )

        chunks = _SPLITTER.split_text(raw_text)
        if not chunks:
            raise BadRequestError("Document text produced no chunks — cannot index")

        try:
            embeddings = await self._embedder.embed_batch(chunks)
        except UpstreamServiceError:
            raise
        except Exception as exc:
            logger.error("indexing_embed_failed", error=str(exc), meeting_id=meeting_id)
            raise UpstreamServiceError("openai", f"Embedding failed: {exc}") from exc

        if len(embeddings) != len(chunks):
            raise UpstreamServiceError(
                "openai",
                f"Embedding count mismatch: expected {len(chunks)}, got {len(embeddings)}",
            )

        # Build mom_analysis_data JSON — same structure as seeded JSON templates
        analysis_data = json.dumps({
            "meeting_type": meeting.meeting_type,
            "date": meeting.date,
            "time": meeting.time,
            "organizer": meeting.organizer,
            "location": meeting.location,
            "projects": [
                {
                    "project_name":      p.project_name,
                    "project_id":        p.project_id,
                    "status":            p.status,
                    "recommendation":    p.recommendation,
                    "decision":          p.decision,
                    "budget_allocation": p.budget_allocation,
                    "action_items":      p.action_items,
                }
                for p in meeting.projects
            ],
        }, ensure_ascii=False, separators=(",", ":"))

        # Project IDs for direct Pinecone filtering
        project_ids = [p.project_id for p in meeting.projects if p.project_id]

        # Chunk vectors — same metadata format as seed_index.py
        vectors: list[dict[str, Any]] = [
            {
                "id": f"{meeting_id}_c{idx:04d}",
                "values": embedding,
                "metadata": {
                    "document_name":          blob_filename,
                    "meeting_date":           meeting.date,
                    "meeting_time":           meeting.time,
                    "page_number":            1,
                    "mom_meeting_text_chunk": chunk,
                    "mom_analysis_data":      analysis_data,
                    "project_ids":            project_ids,
                },
            }
            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings), 1)
        ]

        try:
            await self._store.upsert(vectors)
        except UpstreamServiceError:
            raise
        except Exception as exc:
            logger.error("indexing_upsert_failed", error=str(exc), meeting_id=meeting_id)
            raise UpstreamServiceError("pinecone", f"Upsert failed: {exc}") from exc

        # Dashboard vector — uses the legacy meeting_ prefix so list_meetings_page works
        all_decisions = [p.decision for p in meeting.projects if p.decision]
        all_actions: list[str] = []
        for p in meeting.projects:
            all_actions.extend(p.action_items)
        topics = [p.project_name for p in meeting.projects]

        dashboard_meta: dict[str, Any] = {
            "meeting_id":   meeting_id,
            "chunk_type":   "meeting",
            "blob_filename": blob_filename,
            "title":        f"MoM - {meeting.date} {meeting.time}".strip(),
            "meeting_type": meeting.meeting_type,
            "date":         meeting.date,
            "organizer":    meeting.organizer,
            "attendees":    "",
            "topics":       ",".join(topics),
            "decisions":    _LIST_SEP.join(all_decisions),
            "action_items": _LIST_SEP.join(all_actions),
            "indexed_at":   indexed_at,
        }
        try:
            await self._store.upsert([{
                "id":       f"meeting_{meeting_id}",
                "values":   embeddings[0],
                "metadata": dashboard_meta,
            }])
        except Exception as exc:
            logger.warning("meeting_vector_upsert_failed", meeting_id=meeting_id, error=str(exc))

        logger.info("indexing_done", meeting_id=meeting_id, chunks_indexed=len(vectors))
        return IndexResponse(
            meeting_id=meeting_id,
            chunks_indexed=len(vectors),
            message=f"Meeting on {meeting.date} indexed successfully with {len(meeting.projects)} project(s)",
        )
