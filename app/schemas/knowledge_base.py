from __future__ import annotations

from pydantic import BaseModel


class MeetingRecord(BaseModel):
    meeting_id: str
    title: str
    meeting_type: str
    date: str
    organizer: str
    attendees: list[str]
    topics: list[str]
    decisions: list[str]
    action_items: list[str]
    blob_filename: str | None
    indexed_at: str


class KnowledgeBaseStats(BaseModel):
    total_meetings: int
    type_distribution: dict[str, int]
    avg_attendees: float
    last_added_at: str | None
    top_topics: list[str]
    # Meetings bucketed by month e.g. {"2026-05": 12, "2026-06": 34}
    meetings_by_month: dict[str, int]
    # Most frequent organizers and attendees — useful for ownership/coverage questions
    top_organizers: list[str]
    top_attendees: list[str]
    # True when pool > 10 000 and stats are based on a sample
    is_sampled: bool = False


class MeetingsPageResponse(BaseModel):
    meetings: list[MeetingRecord]
    next_cursor: str | None
    # Total meeting count from stats cache; -1 = not yet computed
    total: int


class KnowledgeBaseResponse(BaseModel):
    stats: KnowledgeBaseStats
    meetings: list[MeetingRecord]
