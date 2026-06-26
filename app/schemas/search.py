from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    """A single turn in the conversation history sent from the frontend."""
    role: Literal["user", "assistant"]
    content: str


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, description="Natural-language question about the meeting minutes")
    top_k: int | None = Field(None, ge=1, le=20, description="Override default result count")
    # Last N conversation turns for context — agent uses these to answer follow-ups
    messages: list[ConversationMessage] = Field(default_factory=list, description="Conversation history (up to 30 turns)")


class ProjectRef(BaseModel):
    """A project's real identifier + name, surfaced from the meeting's analysis JSON."""
    project_id: str = ""
    project_name: str = ""


class MeetingMatch(BaseModel):
    id: str
    title: str
    meeting_type: str
    date: str
    organizer: str
    attendees: list[str]
    topics: list[str]
    decisions: list[str]
    action_items: list[str]
    # Real project id↔name pairs from the knowledge base — never fabricate these.
    projects: list[ProjectRef] = Field(default_factory=list)
    score: float = Field(..., description="Similarity score [0, 1]")
    summary: str | None = None
    blob_filename: str | None = None
    # Matched chunk text — passed to the LLM for richer answers, not rendered on card
    chunk_text: str = ""


class SearchResponse(BaseModel):
    query: str
    answer: str
    meetings: list[MeetingMatch]
    request_id: str
