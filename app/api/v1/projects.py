from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.exceptions import UpstreamServiceError
from app.core.logging import get_correlation_id

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

# ── Simple in-memory cache for project ID list ────────────────────────────────
_pid_cache: list[str] = []
_pid_cache_at: float = 0.0
_PID_CACHE_TTL = 300.0  # 5 minutes


async def _get_project_ids(vector_store) -> list[str]:
    global _pid_cache, _pid_cache_at
    if _pid_cache and (time.monotonic() - _pid_cache_at) < _PID_CACHE_TTL:
        return _pid_cache
    ids = await vector_store.list_all_project_ids()
    _pid_cache = ids
    _pid_cache_at = time.monotonic()
    logger.info("project_id_cache_refreshed", count=len(ids))
    return ids


# ── Dashboard cache ───────────────────────────────────────────────────────────
_meetings_cache: list[dict] = []
_meetings_cache_at: float = 0.0
_MEETINGS_CACHE_TTL = 300.0


async def _get_all_meetings(vector_store) -> list[dict]:
    """Return one parsed metadata dict per unique meeting, cached 5 min."""
    global _meetings_cache, _meetings_cache_at
    if _meetings_cache and (time.monotonic() - _meetings_cache_at) < _MEETINGS_CACHE_TTL:
        return _meetings_cache

    import asyncio as _asyncio
    import math
    dim = 1536
    dummy = [1.0 / math.sqrt(dim)] * dim
    try:
        raw = await _asyncio.to_thread(
            vector_store._index.query,
            vector=dummy,
            top_k=10000,
            include_metadata=True,
        )
        matches = raw.matches or []
    except Exception as exc:
        logger.warning("dashboard_query_failed", error=str(exc))
        return []

    seen: dict[str, dict] = {}
    for m in matches:
        meta = dict(m.metadata or {})
        doc = meta.get("document_name", "")
        if doc and doc not in seen:
            seen[doc] = meta

    _meetings_cache = list(seen.values())
    _meetings_cache_at = time.monotonic()
    logger.info("meetings_cache_refreshed", count=len(_meetings_cache))
    return _meetings_cache


def _parse_budget(s: str) -> float | None:
    """Extract a numeric value from strings like '₹50 Lakhs', '$1.2M', '100000'."""
    if not s:
        return None
    cleaned = re.sub(r"[₹$€£¥,\s]", "", s)
    m = re.search(r"([\d]+(?:\.[\d]+)?)\s*(k|lakh|lac|lakhs|lacs|m|million|crore|crores|b|billion)?",
                  cleaned, re.IGNORECASE)
    if not m:
        return None
    num = float(m.group(1))
    mult = (m.group(2) or "").lower()
    if mult == "k":                             num *= 1_000
    elif mult in ("lakh", "lac", "lakhs", "lacs"): num *= 100_000
    elif mult in ("m", "million"):              num *= 1_000_000
    elif mult in ("crore", "crores"):           num *= 10_000_000
    elif mult in ("b", "billion"):              num *= 1_000_000_000
    return num


def _categorise_status(status: str) -> str:
    s = status.lower()
    if any(k in s for k in ("complet", "closed", "done")):        return "completed"
    if any(k in s for k in ("hold", "defer", "paused", "suspend")): return "on_hold"
    if any(k in s for k in ("risk", "delay", "behind", "issue")):  return "at_risk"
    if any(k in s for k in ("active", "progress", "track", "ongoing")): return "active"
    return "other"


@router.get("/dashboard-stats")
async def get_dashboard_stats(request: Request) -> dict:
    """Aggregate project + meeting metrics from all indexed chunks."""
    vector_store = request.app.state.vector_store
    meetings = await _get_all_meetings(vector_store)

    today = date.today()
    stale_cutoff = (today - timedelta(days=90)).isoformat()

    # ── Per-project trackers ──────────────────────────────────────────────────
    proj_appearances: dict[str, int] = {}
    proj_name_map: dict[str, str] = {}
    proj_latest: dict[str, tuple[str, str]] = {}           # pid -> (date, status)
    proj_first_date: dict[str, str] = {}                   # pid -> earliest date
    proj_latest_decision: dict[str, tuple[str, str]] = {}  # pid -> (date, decision)
    proj_statuses: dict[str, set[str]] = {}                # pid -> all unique statuses seen
    proj_budgets: dict[str, list[str]] = {}                # pid -> all non-empty budget strings
    proj_action_count: dict[str, int] = {}                 # pid -> total action items

    # ── Per-meeting trackers ──────────────────────────────────────────────────
    total_decisions = 0
    total_action_items = 0
    type_counter: dict[str, int] = {}
    meetings_by_month: dict[str, int] = {}
    recent: list[dict] = []

    for meta in meetings:
        analysis_str = meta.get("mom_analysis_data", "")
        if not analysis_str:
            continue
        try:
            obj = json.loads(analysis_str)
        except Exception:
            continue

        mtype = obj.get("meeting_type", "Other") or "Other"
        mdate = meta.get("meeting_date", "")
        type_counter[mtype] = type_counter.get(mtype, 0) + 1

        if mdate and len(mdate) >= 7:
            ym = mdate[:7]
            meetings_by_month[ym] = meetings_by_month.get(ym, 0) + 1

        proj_count = 0
        for p in obj.get("projects", []):
            pid = p.get("project_id", "").strip()
            if not pid:
                continue
            pname = p.get("project_name", pid)
            proj_appearances[pid] = proj_appearances.get(pid, 0) + 1
            proj_name_map[pid] = pname

            # Date range
            if not proj_first_date.get(pid) or (mdate and mdate < proj_first_date[pid]):
                proj_first_date[pid] = mdate

            # Decision
            decision = p.get("decision", "").strip()
            if decision:
                total_decisions += 1
                cur_d = proj_latest_decision.get(pid)
                if not cur_d or mdate > cur_d[0]:
                    proj_latest_decision[pid] = (mdate, decision)

            # Status + churn tracking
            status = p.get("status", "")
            cur = proj_latest.get(pid)
            if not cur or mdate > cur[0]:
                proj_latest[pid] = (mdate, status)
            if status:
                if pid not in proj_statuses:
                    proj_statuses[pid] = set()
                proj_statuses[pid].add(status)

            # Budget
            budget = p.get("budget_allocation", "").strip()
            if budget:
                proj_budgets.setdefault(pid, []).append(budget)

            # Action items
            ais = p.get("action_items", [])
            proj_action_count[pid] = proj_action_count.get(pid, 0) + len(ais)
            total_action_items += len(ais)

            proj_count += 1

        recent.append({
            "document_name": meta.get("document_name", ""),
            "meeting_date":  mdate,
            "meeting_time":  meta.get("meeting_time", ""),
            "meeting_type":  mtype,
            "project_count": proj_count,
        })

    # ── Status buckets ────────────────────────────────────────────────────────
    status_counts = {"active": 0, "on_hold": 0, "completed": 0, "at_risk": 0, "other": 0}
    for pid, (_, status) in proj_latest.items():
        bucket = _categorise_status(status)
        status_counts[bucket] += 1

    # ── Derived health metrics ────────────────────────────────────────────────
    stale_count = 0
    churn_count = 0
    no_decision_count = 0
    total_budget_raw = 0.0
    budget_by_status: dict[str, float] = {"active": 0.0, "on_hold": 0.0, "completed": 0.0, "at_risk": 0.0}
    total_age_days = 0
    projects_with_age = 0

    for pid in proj_appearances:
        latest_date, status = proj_latest.get(pid, ("", ""))
        first_date = proj_first_date.get(pid, latest_date)
        category = _categorise_status(status)

        if latest_date and latest_date < stale_cutoff:
            stale_count += 1

        unique_statuses = len(proj_statuses.get(pid, set()))
        if unique_statuses >= 2:
            churn_count += 1

        has_decision = bool(proj_latest_decision.get(pid))
        if not has_decision and proj_appearances[pid] >= 2:
            no_decision_count += 1

        for b in proj_budgets.get(pid, []):
            val = _parse_budget(b)
            if val:
                total_budget_raw += val
                if category in budget_by_status:
                    budget_by_status[category] += val

        if first_date and latest_date and first_date != latest_date:
            try:
                fd = date.fromisoformat(first_date)
                ld = date.fromisoformat(latest_date)
                age_days = (ld - fd).days
                if age_days > 0:
                    total_age_days += age_days
                    projects_with_age += 1
            except ValueError:
                pass

    avg_project_age_days = round(total_age_days / projects_with_age) if projects_with_age else 0
    action_item_density = round(total_action_items / len(meetings), 1) if meetings else 0.0

    top_projects = sorted(proj_appearances.items(), key=lambda x: x[1], reverse=True)[:8]
    recent.sort(key=lambda r: r["meeting_date"], reverse=True)

    # ── Full project list for drilldown tiles ─────────────────────────────────
    all_projects = sorted(
        [
            {
                "project_id":         pid,
                "project_name":       proj_name_map.get(pid, pid),
                "status":             proj_latest.get(pid, ("", ""))[1],
                "category":           _categorise_status(proj_latest.get(pid, ("", ""))[1]),
                "latest_date":        proj_latest.get(pid, ("", ""))[0],
                "first_date":         proj_first_date.get(pid, ""),
                "latest_decision":    proj_latest_decision.get(pid, ("", ""))[1],
                "appearance_count":   proj_appearances.get(pid, 0),
                "is_stale":           bool(proj_latest.get(pid, ("", ""))[0] and
                                          proj_latest.get(pid, ("", ""))[0] < stale_cutoff),
                "status_change_count": len(proj_statuses.get(pid, set())),
                "has_decision":       bool(proj_latest_decision.get(pid)),
                "latest_budget":      proj_budgets.get(pid, [""])[-1] if proj_budgets.get(pid) else "",
                "total_action_items": proj_action_count.get(pid, 0),
            }
            for pid in proj_appearances
        ],
        key=lambda p: p["latest_date"],
        reverse=True,
    )

    return {
        # Existing portfolio tiles
        "total_projects":    len(proj_appearances),
        "active_projects":   status_counts["active"],
        "on_hold_projects":  status_counts["on_hold"],
        "completed_projects": status_counts["completed"],
        "at_risk_projects":  status_counts["at_risk"],
        "total_meetings":    len(meetings),
        "total_decisions":   total_decisions,
        # New health metrics
        "stale_projects":        stale_count,
        "churn_projects":        churn_count,
        "no_decision_projects":  no_decision_count,
        "avg_project_age_days":  avg_project_age_days,
        "action_item_density":   action_item_density,
        "total_budget_raw":      round(total_budget_raw),
        "budget_by_status":      {k: round(v) for k, v in budget_by_status.items()},
        # Charts
        "meeting_type_distribution": type_counter,
        "meetings_by_month":     dict(sorted(meetings_by_month.items())),
        "top_projects": [
            {"project_id": pid, "project_name": proj_name_map.get(pid, pid), "count": cnt}
            for pid, cnt in top_projects
        ],
        "recent_meetings": recent[:5],
        "all_projects":    all_projects,
    }


@router.get("/meetings")
async def list_project_meetings(
    request: Request,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Paginated meeting file list. q filters by filename or project name."""
    vector_store = request.app.state.vector_store
    meetings = await _get_all_meetings(vector_store)

    # Build enriched list
    rows: list[dict] = []
    q_lower = q.strip().lower()
    for meta in meetings:
        doc = meta.get("document_name", "")
        mdate = meta.get("meeting_date", "")
        mtime = meta.get("meeting_time", "")
        analysis_str = meta.get("mom_analysis_data", "")
        mtype = ""; organizer = ""; proj_names: list[str] = []
        if analysis_str:
            try:
                obj = json.loads(analysis_str)
                mtype = obj.get("meeting_type", "")
                organizer = obj.get("organizer", "")
                proj_names = [p.get("project_name", "") for p in obj.get("projects", []) if p.get("project_name")]
            except Exception:
                pass

        if q_lower:
            haystack = f"{doc} {' '.join(proj_names)} {organizer}".lower()
            if q_lower not in haystack:
                continue

        rows.append({
            "document_name": doc,
            "meeting_id":    doc.replace(".pdf", ""),
            "meeting_date":  mdate,
            "meeting_time":  mtime,
            "meeting_type":  mtype,
            "organizer":     organizer,
            "project_names": proj_names,
        })

    rows.sort(key=lambda r: r["meeting_date"], reverse=True)
    total = len(rows)
    offset = (page - 1) * limit
    page_rows = rows[offset: offset + limit]

    return {
        "meetings":    page_rows,
        "total":       total,
        "page":        page,
        "total_pages": max(1, (total + limit - 1) // limit),
    }


@router.delete("/meetings/{document_name:path}")
async def delete_meeting_file(document_name: str, request: Request) -> dict:
    """Permanently delete a meeting file from Pinecone and Azure Blob Storage.

    Deletes:
      1. All Pinecone chunk vectors whose document_name metadata matches.
      2. The PDF blob from Azure Blob Storage (best-effort — does not fail if missing).
    """
    if not document_name.endswith(".pdf") or "/" in document_name or ".." in document_name:
        raise ValueError(f"Invalid document name: {document_name}")

    global _meetings_cache, _meetings_cache_at, _pid_cache, _pid_cache_at

    vector_store = request.app.state.vector_store
    blob_service  = request.app.state.blob_service

    logger.info("delete_meeting_file_start", document_name=document_name)

    # 1. Delete all Pinecone chunks for this document
    await vector_store.delete_by_document_name(document_name)

    # 2. Delete blob (best-effort)
    blob_deleted = False
    try:
        blob_deleted = await blob_service.delete(document_name)
    except Exception as exc:
        logger.warning("blob_delete_failed_nonfatal", document_name=document_name, error=str(exc))

    # 3. Invalidate caches so the next dashboard/meeting list is fresh
    _meetings_cache = []
    _meetings_cache_at = 0.0
    _pid_cache = []
    _pid_cache_at = 0.0

    logger.info(
        "delete_meeting_file_done",
        document_name=document_name,
        blob_deleted=blob_deleted,
    )
    return {
        "document_name": document_name,
        "pinecone_deleted": True,
        "blob_deleted": blob_deleted,
    }


@router.get("/suggest")
async def suggest_project_ids(
    request: Request,
    q: str = Query(default="", max_length=50),
) -> list[str]:
    """Return project IDs matching the typed prefix (case-insensitive, max 10)."""
    vector_store = request.app.state.vector_store
    all_ids = await _get_project_ids(vector_store)
    q_upper = q.strip().upper()
    if not q_upper:
        return all_ids[:10]
    matches = [pid for pid in all_ids if q_upper in pid]
    return matches[:10]

# ── Prompt ────────────────────────────────────────────────────────────────────

_HISTORY_SYSTEM = """You are a senior programme director reviewing the complete meeting history of a specific project for a board-level audience.

You have been given every recorded appearance of this project across portfolio review meetings, in strict chronological order. Each entry contains: meeting date, meeting type, project status at that point, formal decision taken, recommendation, budget allocation, and action items assigned.

ANALYTICAL TASK — answer all of these in your write-up:
1. What was the project's original mandate and starting position?
2. What formal decisions were made at each stage, and what reasoning is evident from the data?
3. How did the status, budget, or strategic direction shift across meetings — and what drove those shifts?
4. What patterns emerge in the action items (completed, repeated, escalated, dropped)?
5. Where were the critical inflection points — approvals, reversals, holds, escalations, fast-tracks?
6. What is the current standing based on the most recent recorded meeting?

WRITING RULES:
- Write in confident, analytical British English suitable for a board briefing pack
- Every claim must be grounded in the meeting data provided — do not invent or speculate
- Explicitly flag contradictions and reversals (e.g. "Approved in March, the project was placed on hold in June following a budget review...")
- Use **bold** for key decisions and status changes
- Be direct and concise — no padding phrases like "It is worth noting", "This highlights that", "It is important to consider"
- If only one meeting record exists, note that the history is limited and focus on what can be determined

REQUIRED FORMAT — follow this structure exactly:

## {project_name} ({project_id}) — Project History

### Executive Summary
[2–3 sentences: what this project is, its full arc from first to latest appearance, and current trajectory]

### Meeting-by-Meeting Timeline

#### {YYYY-MM-DD} | {Meeting Type} | {Organizer if known}
**Status:** {status or "Not recorded"}
**Decision:** {decision or "No formal decision recorded"}
**Budget:** {budget_allocation or "Not stated"}
**Recommendation:** {recommendation — omit this line entirely if empty}
**Actions Assigned:**
- {action item}
[omit the Actions section entirely if no action items were recorded]

[Repeat for each meeting entry, strictly oldest first]

### Decision Analysis
[2–4 paragraphs. Analyse the decision pattern across meetings: what changed, why it changed, key turning points, any concerns or contradictions visible in the record. Be analytical, not descriptive.]

### Current Standing
**Latest Status:** {most recent status}
**Outstanding Actions:** [bullet list of unresolved action items from the most recent meeting, or "None recorded"]
**Trajectory Assessment:** [1–2 sentences assessing whether the project is on track, at risk, stalled, or completed based purely on the available data]
"""


def _build_context(project_id: str, project_name: str, records: list[dict]) -> str:
    lines = [
        f"PROJECT ID: {project_id}",
        f"PROJECT NAME: {project_name}",
        f"TOTAL MEETING APPEARANCES: {len(records)}",
        "",
        "=== CHRONOLOGICAL MEETING RECORDS ===",
        "",
    ]
    for i, r in enumerate(records, 1):
        lines.append(f"--- Record {i} of {len(records)} ---")
        lines.append(f"Date:         {r['meeting_date']}")
        lines.append(f"Time:         {r['meeting_time'] or 'Not recorded'}")
        lines.append(f"Meeting Type: {r['meeting_type'] or 'Not recorded'}")
        lines.append(f"Organizer:    {r['organizer'] or 'Not recorded'}")
        lines.append(f"Status:       {r['status'] or 'Not recorded'}")
        lines.append(f"Decision:     {r['decision'] or 'No formal decision recorded'}")
        lines.append(f"Budget:       {r['budget_allocation'] or 'Not stated'}")
        lines.append(f"Recommendation: {r['recommendation'] or 'Not stated'}")
        if r["action_items"]:
            lines.append("Action Items:")
            for ai in r["action_items"]:
                lines.append(f"  - {ai}")
        else:
            lines.append("Action Items: None recorded")
        lines.append("")
    return "\n".join(lines)


# ── Endpoint ──────────────────────────────────────────────────────────────────

class ProjectHistoryRequest(BaseModel):
    project_id: str


@router.post("/history/stream")
async def project_history_stream(
    body: ProjectHistoryRequest,
    request: Request,
) -> StreamingResponse:
    """Stream a chronological AI narrative for a project's full meeting history.

    SSE event types:
      data: {"type": "records", "data": [...]}   — timeline entries (emitted first)
      data: {"type": "delta",   "content": "..."}— streamed narrative token
      data: {"type": "done"}                      — stream complete
      data: {"type": "error",   "message": "..."}— terminal error
    """
    request_id = get_correlation_id()
    project_id = body.project_id.strip().upper()

    if not project_id:
        raise ValueError("project_id must not be empty")

    logger.info("project_history_request", project_id=project_id, request_id=request_id)

    vector_store = request.app.state.vector_store
    openai_client = request.app.state.openai_client
    settings = get_settings()

    async def generate():
        # ── 1. Fetch all chunks mentioning this project ────────────────────
        try:
            chunks = await vector_store.fetch_by_project_id(project_id)
        except UpstreamServiceError as exc:
            yield f'data: {json.dumps({"type": "error", "message": str(exc)})}\n\n'
            return

        if not chunks:
            yield f'data: {json.dumps({"type": "error", "message": f"No records found for project {project_id}. Make sure the project ID is correct (e.g. PRJ-0113)."})}\n\n'
            return

        # ── 2. Group by document — one record per meeting ──────────────────
        seen_docs: dict[str, dict] = {}
        for chunk in chunks:
            meta = chunk["metadata"]
            doc = meta.get("document_name", "")
            if doc and doc not in seen_docs:
                seen_docs[doc] = meta

        # ── 3. Extract this project's data from each meeting ───────────────
        records: list[dict] = []
        for doc_name, meta in seen_docs.items():
            analysis_str = meta.get("mom_analysis_data", "")
            project_data: dict = {}
            meeting_info: dict = {}
            if analysis_str:
                try:
                    analysis_obj = json.loads(analysis_str)
                    meeting_info = {
                        "meeting_type": analysis_obj.get("meeting_type", ""),
                        "organizer":    analysis_obj.get("organizer", ""),
                    }
                    for p in analysis_obj.get("projects", []):
                        if p.get("project_id", "").strip().upper() == project_id:
                            project_data = p
                            break
                except (json.JSONDecodeError, AttributeError):
                    pass

            records.append({
                "document_name":   doc_name,
                "meeting_date":    meta.get("meeting_date", ""),
                "meeting_time":    meta.get("meeting_time", ""),
                "meeting_type":    meeting_info.get("meeting_type", ""),
                "organizer":       meeting_info.get("organizer", ""),
                "project_name":    project_data.get("project_name", project_id),
                "project_id":      project_id,
                "status":          project_data.get("status", ""),
                "recommendation":  project_data.get("recommendation", ""),
                "decision":        project_data.get("decision", ""),
                "budget_allocation": project_data.get("budget_allocation", ""),
                "action_items":    project_data.get("action_items", []),
            })

        # Sort chronologically
        records.sort(key=lambda r: r["meeting_date"])

        # ── 4. Send records to frontend ────────────────────────────────────
        yield f'data: {json.dumps({"type": "records", "data": records})}\n\n'

        # ── 5. Build LLM context and stream narrative ──────────────────────
        project_name = next(
            (r["project_name"] for r in records if r["project_name"] and r["project_name"] != project_id),
            project_id,
        )
        context = _build_context(project_id, project_name, records)

        try:
            stream = await openai_client.chat.completions.create(
                model=settings.openai_llm_model,
                messages=[
                    {"role": "system", "content": _HISTORY_SYSTEM},
                    {"role": "user",   "content": context},
                ],
                temperature=0.2,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield f'data: {json.dumps({"type": "delta", "content": delta})}\n\n'
        except Exception as exc:
            logger.error("project_history_llm_failed", project_id=project_id, error=str(exc))
            yield f'data: {json.dumps({"type": "error", "message": f"AI narrative failed: {exc}"})}\n\n'
            return

        logger.info("project_history_done", project_id=project_id, meetings=len(records))
        yield f'data: {json.dumps({"type": "done"})}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
