from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.core.exceptions import UpstreamServiceError
from app.schemas.search import MeetingMatch, SearchRequest, SearchResponse
from app.services.embeddings import OpenAIEmbedder
from app.services.llm import OpenAILLM
from app.services.query_understanding import QueryUnderstandingService
from app.services.vector_store import PineconeStore

logger = structlog.get_logger(__name__)

_ANSWER_SYSTEM = """You are an assistant that answers questions about a company's meeting minutes (MOMs).

Given a user's question and a ranked list of matching meeting records, write a concise 2-3 sentence answer.

Rules:
- Reference specific meetings by title and date
- Surface concrete decisions and action items relevant to the question
- Be precise and factual
- Do NOT invent information beyond what is provided
"""

_FALLBACK_ANSWER = (
    "Found matching meetings based on your query. "
    "Please review the records below for details."
)

_NO_RESULTS_ANSWER = (
    "No matching meeting minutes were found for your query. "
    "Try broadening the search or removing specific filters."
)


class SearchService:
    def __init__(
        self,
        embedder: OpenAIEmbedder,
        vector_store: PineconeStore,
        query_understanding: QueryUnderstandingService,
        llm: OpenAILLM,
        top_k: int = 5,
    ) -> None:
        self._embedder = embedder
        self._store = vector_store
        self._query_understanding = query_understanding
        self._llm = llm
        self._top_k = top_k

    async def search(self, request: SearchRequest, request_id: str) -> SearchResponse:
        top_k = request.top_k or self._top_k

        # ── 1. Parse NL query into structured fields ───────────────────────
        try:
            parsed = await self._query_understanding.parse(request.query)
        except Exception as exc:
            logger.error("query_parse_failed", error=str(exc), request_id=request_id)
            raise UpstreamServiceError("openai", f"Query parsing failed: {exc}") from exc

        # ── 2. Guard: empty semantic text means nothing to search for ──────
        if not parsed.semantic_text.strip():
            logger.info("query_empty_semantic_text", query=request.query, request_id=request_id)
            return SearchResponse(
                query=request.query,
                answer="I can help you find meeting minutes. Try asking about a topic, decision, or action item — for example: \"what did we decide about the Q3 budget\" or \"action items from last week's client call\".",
                meetings=[],
                request_id=request_id,
            )

        # ── 3. Embed the semantic text ─────────────────────────────────────
        try:
            vector = await self._embedder.embed(parsed.semantic_text)
        except UpstreamServiceError:
            raise
        except Exception as exc:
            logger.error("query_embed_failed", error=str(exc), request_id=request_id)
            raise UpstreamServiceError("openai", f"Query embedding failed: {exc}") from exc

        # ── 4. Pinecone query (no metadata filter — semantic search only) ─────
        logger.info(
            "search_pinecone_query",
            semantic_text=parsed.semantic_text,
            vector_dim=len(vector),
            top_k_requested=top_k * 5,
            request_id=request_id,
        )

        # Fetch more than top_k so deduplication still returns enough results
        try:
            raw_matches = await self._store.query(
                vector=vector,
                top_k=top_k * 5,
                filter=None,
            )
        except UpstreamServiceError:
            raise
        except Exception as exc:
            logger.error("pinecone_query_error", error=str(exc), request_id=request_id)
            raise UpstreamServiceError("pinecone", f"Vector search failed: {exc}") from exc

        # ── 5. Deduplicate — keep the best-scoring chunk per document ──────
        best: dict[str, dict] = {}
        for match in raw_matches:
            doc_name = match["metadata"].get("document_name", match["id"])
            mid = Path(doc_name).stem if doc_name else match["id"]
            if mid not in best or match["score"] > best[mid]["score"]:
                best[mid] = match

        top_matches = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:top_k]

        logger.info(
            "retrieval_done",
            raw_hits=len(raw_matches),
            unique_meetings=len(best),
            returned=len(top_matches),
            request_id=request_id,
        )

        # ── 6. Build MeetingMatch objects ───────────────────────────────────
        meetings = [_to_meeting_match(match) for match in top_matches]

        # ── 7. Generate natural-language answer (non-critical — falls back) ─
        answer = await self._generate_answer(request.query, meetings)

        return SearchResponse(
            query=request.query,
            answer=answer,
            meetings=meetings,
            request_id=request_id,
        )

    async def retrieve(self, query: str, top_k: int | None, request_id: str) -> list[MeetingMatch]:
        """Run retrieval only — parse query, embed, search Pinecone, deduplicate.

        Used by the agent which generates its own answer via streaming.
        Returns an empty list when the query has no usable semantic intent.
        """
        effective_top_k = top_k or self._top_k

        try:
            parsed = await self._query_understanding.parse(query)
        except Exception as exc:
            logger.error("query_parse_failed", error=str(exc), request_id=request_id)
            raise UpstreamServiceError("openai", f"Query parsing failed: {exc}") from exc

        if not parsed.semantic_text.strip():
            return []

        try:
            vector = await self._embedder.embed(parsed.semantic_text)
        except UpstreamServiceError:
            raise
        except Exception as exc:
            logger.error("query_embed_failed", error=str(exc), request_id=request_id)
            raise UpstreamServiceError("openai", f"Query embedding failed: {exc}") from exc

        try:
            raw_matches = await self._store.query(
                vector=vector,
                top_k=effective_top_k * 5,
                filter=None,
            )
        except UpstreamServiceError:
            raise
        except Exception as exc:
            logger.error("pinecone_query_error", error=str(exc), request_id=request_id)
            raise UpstreamServiceError("pinecone", f"Vector search failed: {exc}") from exc

        best: dict[str, dict] = {}
        for match in raw_matches:
            doc_name = match["metadata"].get("document_name", match["id"])
            mid = Path(doc_name).stem if doc_name else match["id"]
            if mid not in best or match["score"] > best[mid]["score"]:
                best[mid] = match

        top_matches = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:effective_top_k]
        meetings = [_to_meeting_match(match) for match in top_matches]

        logger.info(
            "retrieve_done",
            query=query,
            returned=len(meetings),
            request_id=request_id,
        )
        return meetings

    async def _generate_answer(self, query: str, meetings: list[MeetingMatch]) -> str:
        """Generate a user-facing summary. Falls back to a static message on failure."""
        if not meetings:
            return _NO_RESULTS_ANSWER

        meeting_lines = "\n".join(
            f"{i+1}. {m.title} | {m.date} | {m.meeting_type} | Organizer: {m.organizer} | "
            f"Projects: {'; '.join(m.topics[:5])} | "
            f"Decisions: {'; '.join(m.decisions[:3])} | "
            f"Action items: {'; '.join(m.action_items[:3])} | "
            f"Relevant excerpt: {m.chunk_text[:300]} | "
            f"Match score: {m.score:.0%}"
            for i, m in enumerate(meetings)
        )
        user_prompt = (
            f"User question: {query}\n\n"
            f"Top matching meetings:\n{meeting_lines}"
        )

        try:
            return await self._llm.complete(system=_ANSWER_SYSTEM, user=user_prompt)
        except Exception as exc:
            # Answer generation is best-effort; meetings are already retrieved
            logger.warning("answer_generation_failed", error=str(exc))
            return _FALLBACK_ANSWER


def _to_meeting_match(match: dict) -> MeetingMatch:
    meta = match["metadata"]

    # Parse the structured JSON stored alongside each chunk
    try:
        analysis = json.loads(meta.get("mom_analysis_data", "{}") or "{}")
    except json.JSONDecodeError:
        analysis = {}

    projects = analysis.get("projects", [])

    meeting_type = analysis.get("meeting_type", "Meeting")
    organizer    = analysis.get("organizer", "")
    meeting_date = meta.get("meeting_date", "")
    meeting_time = meta.get("meeting_time", "")

    # Human-readable card title
    title = f"MoM – {meeting_date}  {meeting_time}".strip(" –")

    # Topics = project names discussed in the meeting
    topics = [p["project_name"] for p in projects if p.get("project_name")]

    # Decisions across all projects in this meeting
    decisions = [p["decision"] for p in projects if p.get("decision")]

    # Action items flattened across all projects
    action_items: list[str] = []
    for p in projects:
        items = p.get("action_items", [])
        if isinstance(items, list):
            action_items.extend(items)
        elif isinstance(items, str) and items:
            action_items.append(items)

    doc_name  = meta.get("document_name", match["id"])
    meeting_id = Path(doc_name).stem if doc_name else match["id"]

    return MeetingMatch(
        id=meeting_id,
        title=title,
        meeting_type=meeting_type,
        date=meeting_date,
        organizer=organizer,
        attendees=[],          # not stored in JSON — attendees live in the PDF text
        topics=topics,
        decisions=decisions,
        action_items=action_items,
        score=round(float(match["score"]), 4),
        blob_filename=doc_name or None,
        chunk_text=meta.get("mom_meeting_text_chunk", ""),
    )
