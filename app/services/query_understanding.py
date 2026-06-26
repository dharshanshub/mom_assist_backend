from __future__ import annotations

import json
import re

import structlog
from pydantic import BaseModel

from app.services.llm import OpenAILLM

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are a meeting-minutes search query parser.

Extract structured information from a user's natural-language question about meeting minutes and return ONLY a valid JSON object — no markdown, no explanation.

JSON fields:
- semantic_text  : string  — clean version of the query for semantic embedding
- topics         : array   — specific topics/keywords mentioned
- meeting_type   : string | null — one of Standup, Planning, Review, Retrospective, Client, Board, All-Hands, One-on-One, Other if clearly implied, else null
- after_date     : string | null — minimum meeting date (YYYY-MM-DD) if a time bound is mentioned, else null

Examples:
Query: "what did we decide about the Q3 budget in planning meetings"
{"semantic_text":"Q3 budget decisions planning meeting","topics":["budget","Q3"],"meeting_type":"Planning","after_date":null}

Query: "action items from the client call last week"
{"semantic_text":"action items client call","topics":["action items"],"meeting_type":"Client","after_date":null}

Query: "what happened in standups since 2026-05-01"
{"semantic_text":"standup meeting updates","topics":[],"meeting_type":"Standup","after_date":"2026-05-01"}
"""


class ParsedQuery(BaseModel):
    semantic_text: str
    topics: list[str] = []
    meeting_type: str | None = None
    after_date: str | None = None


class QueryUnderstandingService:
    def __init__(self, llm_client: OpenAILLM) -> None:
        self._llm = llm_client

    async def parse(self, query: str) -> ParsedQuery:
        raw = await self._llm.complete(system=_SYSTEM_PROMPT, user=query)

        # Strip markdown code fences if present
        raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()

        try:
            data = json.loads(raw)
            parsed = ParsedQuery(**data)
        except Exception as exc:
            logger.warning(
                "query_parse_fallback",
                error=str(exc),
                raw=raw[:200],
            )
            # Graceful fallback: use the original query as semantic text
            parsed = ParsedQuery(semantic_text=query)

        logger.info(
            "query_parsed",
            semantic_text=parsed.semantic_text,
            topics=parsed.topics,
            meeting_type=parsed.meeting_type,
            after_date=parsed.after_date,
        )
        return parsed
