from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MEETING_TYPE = Literal[
    "Portfolio Review",
    "Board Meeting",
    "Planning Session",
    "Programme Retrospective",
    "Client Steering Committee",
    "All-Hands Update",
    "Cross-Functional Sync",
    "Other",
]


class ExtractedProject(BaseModel):
    """One project / initiative discussed in the meeting."""
    project_name: str = Field(..., description="Name of the project or initiative")
    project_id: str = Field(default="", description="Project ID e.g. PRJ-0101")
    status: str = Field(default="", description="Current project status")
    recommendation: str = Field(default="", description="Recommendation made for this project")
    decision: str = Field(default="", description="Decision reached e.g. APPROVED, DEFERRED")
    budget_allocation: str = Field(default="", description="Budget figure if mentioned")
    action_items: list[str] = Field(default_factory=list, description="Action items for this project")


class ExtractedMeeting(BaseModel):
    """LLM-extracted meeting minutes structured around individual projects."""
    meeting_type: MEETING_TYPE = Field(default="Other", description="Meeting category")
    date: str = Field(..., description="Meeting date in YYYY-MM-DD format")
    time: str = Field(default="", description="Meeting time in HH:MM 24-hour format")
    organizer: str = Field(default="", description="Full name of chairperson or organizer")
    location: str = Field(default="", description="Room name, video platform, or location")
    projects: list[ExtractedProject] = Field(
        default_factory=list,
        description="All projects / initiatives discussed in the meeting",
    )


class UploadResponse(BaseModel):
    """Returned after MOM document upload + LLM extraction."""
    meeting_id: str = Field(..., description="Generated ID — used as Pinecone + blob key")
    blob_filename: str = Field(..., description="Blob name e.g. 'uploaded_abc123.pdf'")
    extracted: ExtractedMeeting
    raw_text: str


class IndexRequest(BaseModel):
    """Sent by the frontend after the user reviews and confirms the extracted minutes."""
    meeting_id: str
    blob_filename: str
    meeting: ExtractedMeeting
    raw_text: str


class IndexResponse(BaseModel):
    meeting_id: str
    chunks_indexed: int
    message: str
