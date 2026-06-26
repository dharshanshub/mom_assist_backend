from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.logging import get_correlation_id
from app.schemas.knowledge_base import (
    KnowledgeBaseStats,
    MeetingRecord,
    MeetingsPageResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

_MEETING_TYPES = [
    "Standup", "Planning", "Review", "Retrospective",
    "Client", "Board", "All-Hands", "One-on-One", "Other",
]
_STATS_TTL_SECONDS = 300  # 5-minute cache


# ── Shared helpers ────────────────────────────────────────────────────────────

def _split(raw, sep: str = ",") -> list[str]:
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(sep) if s.strip()]
    return list(raw) if raw else []


def _parse_record(raw: dict) -> MeetingRecord:
    indexed_at = raw.get("indexed_at") or raw.get("date") or ""
    return MeetingRecord(
        meeting_id=raw.get("meeting_id", ""),
        title=raw.get("title", "Untitled meeting"),
        meeting_type=raw.get("meeting_type", "Other"),
        date=raw.get("date", ""),
        organizer=raw.get("organizer", ""),
        attendees=_split(raw.get("attendees", "")),
        topics=_split(raw.get("topics", "")),
        decisions=_split(raw.get("decisions", ""), sep="||"),
        action_items=_split(raw.get("action_items", ""), sep="||"),
        blob_filename=raw.get("blob_filename") or None,
        indexed_at=indexed_at,
    )


def _compute_stats(metadata_sample: list[dict], total: int) -> KnowledgeBaseStats:
    records = [_parse_record(r) for r in metadata_sample]
    sampled = len(records)

    # Meeting-type distribution — fixed order, omit zeros
    type_counts = Counter(r.meeting_type for r in records if r.meeting_type)
    type_distribution = {
        t: type_counts[t]
        for t in _MEETING_TYPES
        if type_counts[t] > 0
    }

    # Average attendee count
    attendee_counts = [len(r.attendees) for r in records]
    avg_attendees = round(sum(attendee_counts) / len(attendee_counts), 1) if attendee_counts else 0.0

    # Meetings by month (YYYY-MM) from the meeting date
    month_counts: Counter[str] = Counter()
    for r in records:
        if r.date and len(r.date) >= 7:
            month_counts[r.date[:7]] += 1
    meetings_by_month = dict(sorted(month_counts.items()))

    # Last indexed date
    dated = [r.indexed_at for r in records if r.indexed_at]
    last_added_at = max(dated) if dated else None

    # Top topics across the pool
    all_topics: list[str] = []
    for r in records:
        all_topics.extend(r.topics)
    top_topics = [t for t, _ in Counter(all_topics).most_common(10)]

    # Top organizers
    all_organizers = [r.organizer for r in records if r.organizer]
    top_organizers = [o for o, _ in Counter(all_organizers).most_common(6)]

    # Top attendees
    all_attendees: list[str] = []
    for r in records:
        all_attendees.extend(r.attendees)
    top_attendees = [a for a, _ in Counter(all_attendees).most_common(10)]

    return KnowledgeBaseStats(
        total_meetings=total,
        type_distribution=type_distribution,
        avg_attendees=avg_attendees,
        last_added_at=last_added_at,
        top_topics=top_topics,
        meetings_by_month=meetings_by_month,
        top_organizers=top_organizers,
        top_attendees=top_attendees,
        is_sampled=sampled < total,
    )


# ── Stats cache helpers ───────────────────────────────────────────────────────

def _stats_cache(request: Request) -> dict:
    """Lazy-init the stats cache on app.state."""
    if not hasattr(request.app.state, "kb_stats_cache"):
        request.app.state.kb_stats_cache = {
            "stats": None,
            "total": -1,
            "cached_at": None,
        }
    return request.app.state.kb_stats_cache


def _cache_is_fresh(cache: dict) -> bool:
    if cache["cached_at"] is None or cache["stats"] is None:
        return False
    age = (datetime.now(timezone.utc) - cache["cached_at"]).total_seconds()
    return age < _STATS_TTL_SECONDS


async def _refresh_stats(request: Request) -> KnowledgeBaseStats:
    """Full stats computation — runs on cache miss, result stored in app.state."""
    vector_store = request.app.state.vector_store
    metadata_sample, total = await vector_store.get_stats_sample()
    stats = _compute_stats(metadata_sample, total)

    cache = _stats_cache(request)
    cache["stats"] = stats
    cache["total"] = total
    cache["cached_at"] = datetime.now(timezone.utc)

    logger.info("kb_stats_cache_refreshed", total=total, sampled=len(metadata_sample))
    return stats


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/stats", response_model=KnowledgeBaseStats)
async def get_stats(request: Request) -> KnowledgeBaseStats:
    """Pool-level stats for the knowledge base dashboard.

    Cached in memory for 5 minutes.  The first request after startup (or after
    the TTL expires) triggers a full computation — subsequent calls are instant.

    Stats are derived from a query sample of up to 10 000 meetings; at larger
    pool sizes type/topics/attendees are approximate and is_sampled=True.
    total_meetings is always exact (counted from meeting_ vector IDs).
    """
    cache = _stats_cache(request)

    if _cache_is_fresh(cache):
        logger.debug("kb_stats_cache_hit")
        return cache["stats"]

    logger.info("kb_stats_cache_miss", reason="stale_or_empty")
    return await _refresh_stats(request)


@router.get("/meetings", response_model=MeetingsPageResponse)
async def list_meetings_page(
    request: Request,
    cursor: str | None = Query(default=None, description="Pagination token from previous response"),
    limit: int = Query(default=20, ge=1, le=100, description="Records per page"),
    search: str | None = Query(default=None, max_length=200, description="Filter all meetings by title or organizer (substring)"),
    meeting_type: str | None = Query(default=None, description="Filter all meetings by exact meeting type"),
) -> MeetingsPageResponse:
    """Meeting list — paginated browse or full-pool filtered search.

    Without search/meeting_type: cursor-paginated, O(1) per page.
    With search or meeting_type: scans all meetings server-side, returns every match,
    no pagination (next_cursor=null).  Both can be combined.

    total comes from the stats cache (instant if /stats was called first).
    If stats have not been computed yet it returns -1.
    """
    request_id = get_correlation_id()
    vector_store = request.app.state.vector_store

    if meeting_type and meeting_type not in _MEETING_TYPES:
        raise BadRequestError(f"Invalid meeting_type value: {meeting_type!r}")

    raw_records, next_cursor = await vector_store.list_meetings_page(
        cursor=cursor,
        limit=limit,
        search=search.strip() if search else None,
        meeting_type=meeting_type,
    )

    meetings = [_parse_record(r) for r in raw_records]

    # Use cached total if available — avoids an extra full scan per page request
    cache = _stats_cache(request)
    total = cache["total"] if _cache_is_fresh(cache) else -1

    logger.info(
        "kb_meetings_page_done",
        returned=len(meetings),
        has_next=next_cursor is not None,
        total_cached=total,
        request_id=request_id,
    )
    return MeetingsPageResponse(
        meetings=meetings,
        next_cursor=next_cursor,
        total=total,
    )


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(meeting_id: str, request: Request) -> Response:
    """Remove a meeting from Pinecone and Azure Blob Storage.

    Also invalidates the stats cache so the next /stats call reflects the deletion.
    """
    if not all(c.isalnum() or c in "_-" for c in meeting_id):
        raise BadRequestError("Invalid meeting ID")

    request_id = get_correlation_id()
    logger.info("knowledge_base_delete_request", meeting_id=meeting_id, request_id=request_id)

    vector_store = request.app.state.vector_store
    blob_service = request.app.state.blob_service

    await vector_store.delete_by_meeting_id(meeting_id)

    blob_name = f"{meeting_id}.pdf"
    await blob_service.delete(blob_name)

    # Invalidate stats cache — total_meetings has changed
    cache = _stats_cache(request)
    cache["cached_at"] = None

    logger.info("knowledge_base_delete_done", meeting_id=meeting_id, request_id=request_id)
    return Response(status_code=204)


# ── Document URL ──────────────────────────────────────────────────────────────

class DocumentUrlResponse(BaseModel):
    url: str
    expires_in: int


@router.get("/{meeting_id}/document", response_model=DocumentUrlResponse)
async def get_document_url(meeting_id: str, request: Request) -> DocumentUrlResponse:
    """Generate a 15-minute SAS URL for viewing a meeting's source PDF inline."""
    if not all(c.isalnum() or c in "_-" for c in meeting_id):
        raise BadRequestError("Invalid meeting ID")

    blob_service = request.app.state.blob_service

    if not blob_service.available:
        raise NotFoundError("Blob Storage is not configured — no PDF available")

    url = await blob_service.get_sas_url(f"{meeting_id}.pdf", expiry_minutes=15)
    if url is None:
        raise NotFoundError("No PDF found for this meeting")

    logger.info("document_url_generated", meeting_id=meeting_id)
    return DocumentUrlResponse(url=url, expires_in=900)


# ── Backfill ──────────────────────────────────────────────────────────────────

class BackfillResponse(BaseModel):
    created: int
    skipped: int
    failed: int


@router.post("/backfill", response_model=BackfillResponse)
async def backfill_meeting_vectors(request: Request) -> BackfillResponse:
    """One-time operation: create meeting_ vectors for legacy meetings.

    Legacy meetings (e.g. seeded data) have chunk vectors but no meeting_ vector,
    so they won't appear in the paginated meetings list.

    Strategy:
      1. list(prefix="meeting_") — collect already-recorded meeting IDs.
      2. query(filter={"chunk_index": 0}, include_values=True) — find one chunk per
         legacy meeting with its embedding attached.
      3. Upsert meeting_{meeting_id} vectors in batches of 100.

    Safe to call multiple times — already-recorded meetings are skipped.
    After completion, invalidates the stats cache.
    """
    request_id = get_correlation_id()
    logger.info("backfill_start", request_id=request_id)

    vector_store = request.app.state.vector_store
    index = vector_store._index

    if index is None:
        raise BadRequestError("Pinecone is not connected")

    _EMBED_DIM = 1536
    _UPSERT_BATCH = 100

    def _to_str(item) -> str:
        return item if isinstance(item, str) else str(getattr(item, "id", item))

    def _list_meeting_ids_sync() -> list[str]:
        ids: list[str] = []
        try:
            for batch in index.list(prefix="meeting_"):
                if isinstance(batch, list):
                    ids.extend(_to_str(x) for x in batch)
                else:
                    ids.append(_to_str(batch))
        except Exception as exc:
            logger.warning("backfill_list_meeting_failed", error=str(exc))
        return ids

    meeting_ids = await asyncio.to_thread(_list_meeting_ids_sync)
    recorded_mids: set[str] = {vid[len("meeting_"):] for vid in meeting_ids}

    try:
        uniform = 1.0 / (_EMBED_DIM ** 0.5)
        dummy = [uniform] * _EMBED_DIM
        query_result = await asyncio.to_thread(
            index.query,
            vector=dummy,
            top_k=10000,
            filter={"chunk_index": {"$eq": 0}},
            include_metadata=True,
            include_values=True,
        )
    except Exception as exc:
        logger.error("backfill_query_failed", error=str(exc), request_id=request_id)
        raise BadRequestError(f"Pinecone query failed during backfill: {exc}") from exc

    to_create: list[dict] = []
    already_recorded = 0

    for match in (query_result.matches or []):
        meta = dict(match.metadata or {})
        mid = meta.get("meeting_id", "")
        values = getattr(match, "values", None) or []
        if not mid:
            continue
        if mid in recorded_mids:
            already_recorded += 1
            continue
        if not values:
            logger.warning("backfill_no_embedding", meeting_id=mid)
            continue
        to_create.append({
            "id":       f"meeting_{mid}",
            "values":   values,
            "metadata": {**meta, "chunk_type": "meeting", "meeting_id": mid},
        })

    logger.info(
        "backfill_meetings",
        to_create=len(to_create),
        already_recorded=already_recorded,
        request_id=request_id,
    )

    created = failed = 0
    skipped = already_recorded

    for i in range(0, len(to_create), _UPSERT_BATCH):
        batch = to_create[i : i + _UPSERT_BATCH]
        try:
            await asyncio.to_thread(index.upsert, vectors=batch)
            created += len(batch)
            logger.info("backfill_batch_upserted", count=len(batch), offset=i)
        except Exception as exc:
            logger.error("backfill_upsert_failed", offset=i, error=str(exc))
            failed += len(batch)

    # Invalidate stats cache — meeting count has changed
    cache = _stats_cache(request)
    cache["cached_at"] = None

    logger.info(
        "backfill_done",
        created=created, skipped=skipped, failed=failed,
        request_id=request_id,
    )
    return BackfillResponse(created=created, skipped=skipped, failed=failed)
