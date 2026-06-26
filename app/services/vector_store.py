from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.core.exceptions import UpstreamServiceError

logger = structlog.get_logger(__name__)

# Pinecone recommends a maximum of 100 vectors per upsert call
UPSERT_BATCH_SIZE = 100


class PineconeStore:
    """Wraps a Pinecone Index; all blocking SDK calls run in a thread pool."""

    def __init__(self, index: Any) -> None:
        self._index = index

    async def upsert(self, vectors: list[dict[str, Any]]) -> None:
        """Upsert vectors in batches of UPSERT_BATCH_SIZE.

        Raises:
            UpstreamServiceError: if Pinecone returns an error on any batch.
        """
        if not vectors:
            logger.debug("upsert_skipped", reason="empty_vectors")
            return
        if self._index is None:
            logger.warning(
                "pinecone_index_none",
                action="upsert_skipped",
                hint="Pinecone failed to connect at startup",
            )
            raise UpstreamServiceError("pinecone", "Pinecone index is not initialised")

        total = len(vectors)
        upserted = 0
        for i in range(0, total, UPSERT_BATCH_SIZE):
            batch = vectors[i : i + UPSERT_BATCH_SIZE]
            try:
                await asyncio.to_thread(self._index.upsert, vectors=batch)
                upserted += len(batch)
                logger.debug(
                    "upserted_batch",
                    batch_size=len(batch),
                    offset=i,
                    total=total,
                )
            except Exception as exc:
                logger.error(
                    "pinecone_upsert_failed",
                    offset=i,
                    batch_size=len(batch),
                    error=str(exc),
                )
                raise UpstreamServiceError("pinecone", f"Upsert batch failed at offset {i}: {exc}") from exc

        logger.info("upsert_complete", total_upserted=upserted)

    async def query(
        self,
        vector: list[float],
        top_k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query the index for nearest neighbours.

        Returns an empty list (graceful degradation) when Pinecone is unavailable.
        Raises UpstreamServiceError on unexpected API errors.
        """
        if self._index is None:
            logger.warning(
                "pinecone_index_none",
                action="query_skipped",
                hint="Pinecone failed to connect at startup — check PINECONE_API_KEY and PINECONE_INDEX_NAME",
            )
            return []

        logger.debug(
            "pinecone_query",
            vector_dim=len(vector),
            top_k=top_k,
            filter=filter,
        )

        try:
            result = await asyncio.to_thread(
                self._index.query,
                vector=vector,
                top_k=top_k,
                filter=filter,
                include_metadata=True,
            )
        except Exception as exc:
            logger.error("pinecone_query_failed", error=str(exc), top_k=top_k)
            raise UpstreamServiceError("pinecone", f"Query failed: {exc}") from exc

        matches = [
            {"id": m.id, "score": m.score, "metadata": m.metadata}
            for m in result.matches
        ]
        logger.info(
            "pinecone_query_done",
            raw_matches=len(matches),
            top_score=round(matches[0]["score"], 4) if matches else None,
        )
        return matches

    async def describe_stats(self) -> dict[str, Any]:
        """Return index stats. Returns empty dict if Pinecone is unavailable."""
        if self._index is None:
            return {}
        try:
            stats = await asyncio.to_thread(self._index.describe_index_stats)
            return stats.to_dict() if hasattr(stats, "to_dict") else dict(stats)
        except Exception as exc:
            logger.warning("pinecone_describe_stats_failed", error=str(exc))
            return {}

    async def list_meetings(self) -> list[dict[str, Any]]:
        """Return one metadata record per unique meeting.

        Two tiers, merged transparently:

        Tier 1 (fast): Meetings indexed after the meeting-vector change have a
          dedicated "meeting_{meeting_id}" vector.  list(prefix="meeting_") +
          fetch() returns their metadata with minimal URL overhead.

        Tier 2 (legacy): Older meetings only have chunk vectors.  We use
          query(filter={"chunk_index": 0}) to find one chunk per meeting — the
          same API path that search uses, so it is always available.  Metadata
          is returned inline; no extra fetch() call is needed.
        """
        if self._index is None:
            return []

        _FETCH_BATCH = 20  # ~20 × 35-char IDs ≈ 700 chars — well under URL limit
        _EMBED_DIM = 1536  # OpenAI text-embedding-3-small

        # ── Tier 1: meeting_ vectors ─────────────────────────────────────────
        def _to_str(item: Any) -> str:
            return item if isinstance(item, str) else str(getattr(item, "id", item))

        def _list_meeting_ids() -> list[str]:
            ids: list[str] = []
            try:
                for batch in self._index.list(prefix="meeting_"):
                    if isinstance(batch, list):
                        ids.extend(_to_str(x) for x in batch)
                    else:
                        ids.append(_to_str(batch))
            except Exception as exc:
                logger.warning("pinecone_list_meeting_failed", error=str(exc))
            return ids

        meeting_ids = await asyncio.to_thread(_list_meeting_ids)
        recorded_mids: set[str] = {vid[len("meeting_"):] for vid in meeting_ids}

        records: list[dict[str, Any]] = []

        for i in range(0, len(meeting_ids), _FETCH_BATCH):
            batch = meeting_ids[i : i + _FETCH_BATCH]
            try:
                result = await asyncio.to_thread(self._index.fetch, ids=batch)
                vectors = getattr(result, "vectors", {}) or {}
                for vid in batch:
                    mid = vid[len("meeting_"):]
                    meta: dict[str, Any] = dict(getattr(vectors.get(vid), "metadata", {}) or {}) if vid in vectors else {}
                    meta.setdefault("meeting_id", mid)
                    records.append(meta)
            except Exception as exc:
                logger.error("pinecone_fetch_meeting_failed", offset=i, error=str(exc))
                raise UpstreamServiceError("pinecone", f"fetch() failed at offset {i}: {exc}") from exc

        # ── Tier 2: legacy meetings via query() ─────────────────────────────
        # list(prefix="") is unreliable in Pinecone SDK v7 on serverless indexes.
        # query() with a chunk_index=0 filter is the same path search uses and is
        # always reliable.  Metadata is returned inline, so no fetch() is needed.
        legacy_added = 0
        try:
            uniform = 1.0 / (_EMBED_DIM ** 0.5)
            dummy_vector = [uniform] * _EMBED_DIM
            result = await asyncio.to_thread(
                self._index.query,
                vector=dummy_vector,
                top_k=10000,
                filter={"chunk_index": {"$eq": 0}},
                include_metadata=True,
            )
            for match in (result.matches or []):
                meta = dict(match.metadata or {})
                mid = meta.get("meeting_id", "")
                if mid and mid not in recorded_mids:
                    meta.setdefault("meeting_id", mid)
                    records.append(meta)
                    recorded_mids.add(mid)
                    legacy_added += 1
        except Exception as exc:
            logger.warning("pinecone_legacy_query_failed", error=str(exc))

        logger.info(
            "list_meetings_done",
            tier1_meeting=len(meeting_ids),
            tier2_legacy=legacy_added,
            total=len(records),
        )
        return records

    async def list_meetings_page(
        self,
        cursor: str | None,
        limit: int,
        search: str | None = None,
        meeting_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return one page of meeting metadata.

        Source of truth: query(filter={chunk_index: 0}) + integer offset cursor.
        Every meeting has exactly one chunk_index=0 chunk carrying full metadata,
        so this captures the entire pool — both legacy meetings (chunk vectors
        only) and new ones (which additionally have a meeting_ vector). meeting_
        vectors carry no chunk_index, so they are never double-counted.

        When search or meeting_type is provided, skips pagination and returns all
        matching records across the full pool:
          - meeting_type: passed as a Pinecone metadata $eq filter (server-side, fast).
          - search: substring match on title/organizer applied in memory after the
            Pinecone query (Pinecone has no substring operator).

        Args:
            cursor:       Integer offset string. None = first page.
            limit:        Records per page (1–100). Ignored when search/meeting_type is active.
            search:       Substring to match against title or organizer (case-insensitive).
            meeting_type: Exact meeting_type value to filter by (e.g. "Planning").

        Returns:
            (records, next_cursor) — next_cursor is None on the last page or when filtering.
        """
        if self._index is None:
            return [], None

        _EMBED_DIM = 1536

        # ── Filter mode: query all meetings with optional Pinecone metadata filter ──
        if search or meeting_type:
            search_lower = search.strip().lower() if search else None
            pinecone_filter: dict[str, Any] = {"chunk_index": {"$eq": 0}}
            if meeting_type:
                pinecone_filter["meeting_type"] = {"$eq": meeting_type}
            try:
                uniform = 1.0 / (_EMBED_DIM ** 0.5)
                dummy = [uniform] * _EMBED_DIM
                query_result = await asyncio.to_thread(
                    self._index.query,
                    vector=dummy,
                    top_k=10000,
                    filter=pinecone_filter,
                    include_metadata=True,
                )
                all_records = [dict(m.metadata or {}) for m in (query_result.matches or [])]
            except Exception as exc:
                logger.error("kb_filter_query_failed", search=search, meeting_type=meeting_type, error=str(exc))
                raise UpstreamServiceError("pinecone", f"Filter query failed: {exc}") from exc

            if search_lower:
                matches = [
                    r for r in all_records
                    if search_lower in (r.get("title") or "").lower()
                    or search_lower in (r.get("organizer") or "").lower()
                ]
            else:
                matches = all_records

            logger.info("list_meetings_filter_done", search=search, meeting_type=meeting_type, matched=len(matches), scanned=len(all_records))
            return matches, None

        # ── Unfiltered browse: query(chunk_index=0) is the single source of truth ──
        offset = int(cursor) if cursor and cursor.isdigit() else 0

        try:
            uniform = 1.0 / (_EMBED_DIM ** 0.5)
            dummy = [uniform] * _EMBED_DIM
            query_result = await asyncio.to_thread(
                self._index.query,
                vector=dummy,
                top_k=10000,
                filter={"chunk_index": {"$eq": 0}},
                include_metadata=True,
            )
            all_records = [dict(m.metadata or {}) for m in (query_result.matches or [])]
        except Exception as exc:
            logger.error("kb_meetings_query_failed", error=str(exc))
            raise UpstreamServiceError("pinecone", f"Meetings query failed: {exc}") from exc

        # Deterministic order so offset pagination is stable across page requests
        # (the dummy-vector query order is not guaranteed stable).  Newest first.
        all_records.sort(
            key=lambda r: (
                str(r.get("indexed_at") or r.get("date") or ""),
                str(r.get("meeting_id") or ""),
            ),
            reverse=True,
        )

        page = all_records[offset : offset + limit]
        next_cur = str(offset + limit) if offset + limit < len(all_records) else None

        logger.info(
            "list_meetings_page_done",
            returned=len(page),
            has_next=next_cur is not None,
            method="query_chunk0",
            total_found=len(all_records),
        )
        return page, next_cur

    async def get_stats_sample(
        self,
        max_samples: int = 10000,
    ) -> tuple[list[dict[str, Any]], int]:
        """Collect metadata for stats aggregation and count total meeting vectors.

        Two parallel Pinecone operations:
          1. query(filter={chunk_index:0}, top_k=max_samples) — returns metadata
             for type, topics, attendee, and last_added_at aggregation.  At
             >max_samples meetings the stats are computed from a representative
             sample; the sample size is surfaced in the response so the UI can note it.
          2. list(prefix="meeting_") — counts IDs only (no metadata, minimal bandwidth).
             Accurate total regardless of sample size.

        Returns:
            (metadata_sample, total_meeting_count)
        """
        if self._index is None:
            return [], 0

        _EMBED_DIM = 1536

        # Run both operations concurrently
        async def _query_metadata() -> list[dict[str, Any]]:
            try:
                uniform = 1.0 / (_EMBED_DIM ** 0.5)
                dummy = [uniform] * _EMBED_DIM
                result = await asyncio.to_thread(
                    self._index.query,
                    vector=dummy,
                    top_k=max_samples,
                    filter={"chunk_index": {"$eq": 0}},
                    include_metadata=True,
                )
                return [dict(m.metadata or {}) for m in (result.matches or [])]
            except Exception as exc:
                logger.warning("stats_metadata_query_failed", error=str(exc))
                return []

        def _count_meeting_ids() -> int:
            count = 0
            try:
                for batch in self._index.list(prefix="meeting_"):
                    count += len(batch) if isinstance(batch, list) else 1
            except Exception as exc:
                logger.warning("stats_count_meetings_failed", error=str(exc))
            return count

        metadata_task = asyncio.create_task(_query_metadata())
        meeting_count = await asyncio.to_thread(_count_meeting_ids)
        metadata_sample = await metadata_task

        # total = unique meeting count.  meeting_ vectors exist ONLY for meetings
        # added via the new upload flow; legacy meetings have chunk vectors only.
        # The chunk_index=0 sample holds one record per meeting (a superset of
        # meeting_), so its distinct meeting_id count is the reliable total.
        distinct_in_sample = len({
            r.get("meeting_id") for r in metadata_sample if r.get("meeting_id")
        })
        total = max(meeting_count, distinct_in_sample)

        logger.info(
            "stats_sample_done",
            sampled=len(metadata_sample),
            meeting_vectors=meeting_count,
            total_meetings=total,
        )
        return metadata_sample, total

    async def upsert_meeting_vector(
        self,
        meeting_id: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        """Upsert a single meeting_ vector for a meeting (used by backfill)."""
        vector = {
            "id":       f"meeting_{meeting_id}",
            "values":   embedding,
            "metadata": {**metadata, "chunk_type": "meeting", "meeting_id": meeting_id},
        }
        await self.upsert([vector])

    async def delete_by_meeting_id(self, meeting_id: str) -> None:
        """Delete all vectors for a meeting using a metadata filter.

        Raises:
            UpstreamServiceError: if Pinecone is not initialised or the delete fails.
        """
        if self._index is None:
            raise UpstreamServiceError("pinecone", "Pinecone index is not initialised")

        try:
            await asyncio.to_thread(
                self._index.delete,
                filter={"meeting_id": {"$eq": meeting_id}},
            )
            logger.info("pinecone_deleted_meeting", meeting_id=meeting_id)
        except Exception as exc:
            logger.error("pinecone_delete_failed", meeting_id=meeting_id, error=str(exc))
            raise UpstreamServiceError("pinecone", f"Delete failed for '{meeting_id}': {exc}") from exc

    async def list_all_project_ids(self) -> list[str]:
        """Return sorted list of all distinct project IDs across the index."""
        if self._index is None:
            return []
        _EMBED_DIM = 1536
        uniform = 1.0 / (_EMBED_DIM ** 0.5)
        dummy = [uniform] * _EMBED_DIM
        try:
            result = await asyncio.to_thread(
                self._index.query,
                vector=dummy,
                top_k=10000,
                include_metadata=True,
            )
            seen: set[str] = set()
            for match in (result.matches or []):
                ids = (match.metadata or {}).get("project_ids", [])
                for pid in (ids or []):
                    if pid:
                        seen.add(pid)
            return sorted(seen)
        except Exception as exc:
            logger.warning("list_all_project_ids_failed", error=str(exc))
            return []

    async def delete_by_document_name(self, document_name: str) -> None:
        """Delete all Pinecone chunks whose document_name metadata matches.

        Raises:
            UpstreamServiceError: if Pinecone is not initialised or the delete fails.
        """
        if self._index is None:
            raise UpstreamServiceError("pinecone", "Pinecone index is not initialised")
        try:
            await asyncio.to_thread(
                self._index.delete,
                filter={"document_name": {"$eq": document_name}},
            )
            logger.info("pinecone_deleted_document", document_name=document_name)
        except Exception as exc:
            logger.error("pinecone_delete_document_failed", document_name=document_name, error=str(exc))
            raise UpstreamServiceError("pinecone", f"Delete failed for '{document_name}': {exc}") from exc

    async def fetch_by_project_id(self, project_id: str) -> list[dict[str, Any]]:
        """Return all chunk vectors that mention a specific project_id.

        Uses Pinecone list-filter: {"project_ids": {"$in": [project_id]}}
        so every chunk from every meeting that discussed this project is returned.
        """
        if self._index is None:
            return []

        _EMBED_DIM = 1536
        uniform = 1.0 / (_EMBED_DIM ** 0.5)
        dummy = [uniform] * _EMBED_DIM

        try:
            result = await asyncio.to_thread(
                self._index.query,
                vector=dummy,
                top_k=500,
                filter={"project_ids": {"$in": [project_id]}},
                include_metadata=True,
            )
            matches = [
                {"id": m.id, "metadata": dict(m.metadata or {})}
                for m in (result.matches or [])
            ]
            logger.info("fetch_by_project_id_done", project_id=project_id, matches=len(matches))
            return matches
        except Exception as exc:
            logger.error("fetch_by_project_id_failed", project_id=project_id, error=str(exc))
            raise UpstreamServiceError("pinecone", f"Project fetch failed: {exc}") from exc

    async def ping(self) -> bool:
        """Health check — raises RuntimeError if Pinecone is not connected."""
        if self._index is None:
            raise RuntimeError("Pinecone index not initialised")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._index.describe_index_stats),
                timeout=10.0,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Pinecone ping timed out after 10 s") from exc
        except Exception as exc:
            raise RuntimeError(f"Pinecone ping failed: {exc}") from exc
        return True
