"""
Generate synthetic Minutes-of-Meeting PDF + JSON documents.

- 50 projects total (PRJ-0101 to PRJ-0150)
- 40 recurring projects: appear in multiple meetings with content continuity
- 10 one-off projects: appear in exactly one meeting
- Meetings sorted chronologically across Jan 2023 - Jun 2026
- Content continuity: early appearances = discovery/planning content,
  mid appearances = implementation/progress content,
  late appearances = testing/go-live/results content
- JSON files: unchanged from original structure

Usage (run from backend/):
    python app/scripts/generate_moms.py          # 100 documents
    python app/scripts/generate_moms.py 20       # 20 documents

Output:
    data/documents/      mom_001_20230221_0930.pdf
    data/json_templates/ mom_001_20230221_0930.json
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ── Output folders ─────────────────────────────────────────────────────────────
_ROOT      = Path(__file__).parent.parent.parent
OUTPUT_DIR = _ROOT / "data" / "documents_new"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

random.seed(42)

# ── People ─────────────────────────────────────────────────────────────────────
PEOPLE = [
    ("James Thornton",   "Chief Executive Officer"),
    ("Arjun Mehta",      "Chief Technology Officer"),
    ("Thomas Edgerton",  "Chief Financial Officer"),
    ("Sara Lopez",       "Chief Product Officer"),
    ("Oliver Grant",     "Chief Risk Officer"),
    ("Priya Nair",       "VP of Engineering"),
    ("Marcus Webb",      "VP of Sales"),
    ("Nathan Forrest",   "VP of Marketing"),
    ("Kavya Reddy",      "Head of Operations"),
    ("Ananya Singh",     "Head of HR"),
    ("Meera Patel",      "Finance Director"),
    ("Fatima Al-Hassan", "Director of Strategy"),
    ("Nisha Iyer",       "Compliance Manager"),
    ("Ethan Brooks",     "Senior Architect"),
    ("Carlos Mendez",    "DevOps Manager"),
    ("Sameer Rao",       "Engineering Manager"),
    ("Riya Joshi",       "Product Manager"),
    ("Dinesh Kumar",     "Senior Project Manager"),
    ("Sneha Gupta",      "Data Science Lead"),
    ("Vivek Nambiar",    "Infrastructure Manager"),
    ("Pooja Krishnan",   "Legal Counsel"),
    ("Aisha Mahmood",    "Customer Success Director"),
    ("Rohan Sharma",     "Business Analyst"),
    ("Lakshmi Venkat",   "QA Lead"),
    ("Diana Petrov",     "Scrum Master"),
]

# ── 50 Projects — first 40 are recurring, last 10 are one-off ─────────────────
PROJECTS = [
    # ── Recurring (PRJ-0101 to PRJ-0140) ──────────────────────────────────────
    {"id": "PRJ-0101", "name": "Cloud Infrastructure Migration",            "domain": "Engineering",    "recurring": True},
    {"id": "PRJ-0102", "name": "Customer Portal Redesign",                  "domain": "Product",        "recurring": True},
    {"id": "PRJ-0103", "name": "ERP System Upgrade",                        "domain": "Operations",     "recurring": True},
    {"id": "PRJ-0104", "name": "AI-Powered Analytics Platform",             "domain": "Data",           "recurring": True},
    {"id": "PRJ-0105", "name": "Mobile App v3.0 Launch",                    "domain": "Product",        "recurring": True},
    {"id": "PRJ-0106", "name": "Data Warehouse Modernisation",              "domain": "Data",           "recurring": True},
    {"id": "PRJ-0107", "name": "APAC Market Expansion",                     "domain": "Business",       "recurring": True},
    {"id": "PRJ-0108", "name": "Zero-Trust Network Security Overhaul",      "domain": "Engineering",    "recurring": True},
    {"id": "PRJ-0109", "name": "HR Digital Transformation",                 "domain": "HR",             "recurring": True},
    {"id": "PRJ-0110", "name": "Customer Loyalty Programme",                "domain": "Marketing",      "recurring": True},
    {"id": "PRJ-0111", "name": "SOC 2 Type II Compliance",                  "domain": "Compliance",     "recurring": True},
    {"id": "PRJ-0112", "name": "API Gateway and Microservices Rollout",     "domain": "Engineering",    "recurring": True},
    {"id": "PRJ-0113", "name": "Revenue Optimisation Initiative",           "domain": "Finance",        "recurring": True},
    {"id": "PRJ-0114", "name": "Disaster Recovery and BCP Upgrade",         "domain": "Infrastructure", "recurring": True},
    {"id": "PRJ-0115", "name": "Brand Refresh 2024",                        "domain": "Marketing",      "recurring": True},
    {"id": "PRJ-0116", "name": "Strategic Partnership - FinTech Hub",       "domain": "Business",       "recurring": True},
    {"id": "PRJ-0117", "name": "Automated Testing Framework",               "domain": "Engineering",    "recurring": True},
    {"id": "PRJ-0118", "name": "Supply Chain Digitalisation",               "domain": "Operations",     "recurring": True},
    {"id": "PRJ-0119", "name": "Employee Wellness Platform",                "domain": "HR",             "recurring": True},
    {"id": "PRJ-0120", "name": "Real-Time Fraud Detection System",          "domain": "Data",           "recurring": True},
    {"id": "PRJ-0121", "name": "Multi-Region CDN Deployment",               "domain": "Infrastructure", "recurring": True},
    {"id": "PRJ-0122", "name": "Self-Service BI Dashboard",                 "domain": "Data",           "recurring": True},
    {"id": "PRJ-0123", "name": "Cost Optimisation Programme",               "domain": "Finance",        "recurring": True},
    {"id": "PRJ-0124", "name": "ESG Reporting Framework",                   "domain": "Compliance",     "recurring": True},
    {"id": "PRJ-0125", "name": "Next-Gen CRM Implementation",               "domain": "Sales",          "recurring": True},
    {"id": "PRJ-0126", "name": "Kubernetes Platform Rollout",               "domain": "Engineering",    "recurring": True},
    {"id": "PRJ-0127", "name": "Vendor Rationalisation",                    "domain": "Operations",     "recurring": True},
    {"id": "PRJ-0128", "name": "Product Data Mesh Initiative",              "domain": "Data",           "recurring": True},
    {"id": "PRJ-0129", "name": "Digital Marketing Automation",              "domain": "Marketing",      "recurring": True},
    {"id": "PRJ-0130", "name": "Global Payroll Consolidation",              "domain": "HR",             "recurring": True},
    {"id": "PRJ-0131", "name": "Cybersecurity Incident Response Platform",  "domain": "Engineering",    "recurring": True},
    {"id": "PRJ-0132", "name": "Customer Data Platform",                    "domain": "Data",           "recurring": True},
    {"id": "PRJ-0133", "name": "Singapore Office Expansion",                "domain": "Infrastructure", "recurring": True},
    {"id": "PRJ-0134", "name": "Internal Developer Platform",               "domain": "Engineering",    "recurring": True},
    {"id": "PRJ-0135", "name": "Carbon Footprint Tracking System",          "domain": "Compliance",     "recurring": True},
    {"id": "PRJ-0136", "name": "Product-Led Growth Initiative",             "domain": "Product",        "recurring": True},
    {"id": "PRJ-0137", "name": "Regulatory Reporting Automation",           "domain": "Finance",        "recurring": True},
    {"id": "PRJ-0138", "name": "Partner Portal Launch",                     "domain": "Business",       "recurring": True},
    {"id": "PRJ-0139", "name": "AI Customer Support Bot",                   "domain": "Product",        "recurring": True},
    {"id": "PRJ-0140", "name": "Workforce Analytics Dashboard",             "domain": "HR",             "recurring": True},
    # ── One-off (PRJ-0141 to PRJ-0150) ────────────────────────────────────────
    {"id": "PRJ-0141", "name": "Legacy System Decommission",                "domain": "Engineering",    "recurring": False},
    {"id": "PRJ-0142", "name": "Sales Territory Realignment",               "domain": "Sales",          "recurring": False},
    {"id": "PRJ-0143", "name": "Procurement Policy Review",                 "domain": "Operations",     "recurring": False},
    {"id": "PRJ-0144", "name": "Customer Journey Mapping",                  "domain": "Marketing",      "recurring": False},
    {"id": "PRJ-0145", "name": "Identity and Access Management Overhaul",   "domain": "Compliance",     "recurring": False},
    {"id": "PRJ-0146", "name": "Data Lakehouse Migration",                  "domain": "Data",           "recurring": False},
    {"id": "PRJ-0147", "name": "Employee Onboarding Automation",            "domain": "HR",             "recurring": False},
    {"id": "PRJ-0148", "name": "Pricing Engine Rebuild",                    "domain": "Finance",        "recurring": False},
    {"id": "PRJ-0149", "name": "Technical Documentation Portal",            "domain": "Engineering",    "recurring": False},
    {"id": "PRJ-0150", "name": "Strategic Acquisition Integration",         "domain": "Business",       "recurring": False},
]

RECURRING_PROJECTS = [p for p in PROJECTS if p["recurring"]]
ONEOFF_PROJECTS    = [p for p in PROJECTS if not p["recurring"]]

# ── 9-step project lifecycle ───────────────────────────────────────────────────
# step_idx 0-2 = early stage, 3-5 = mid stage, 6-8 = late stage
LIFECYCLE_STEPS = [
    ("In Discovery",        "Pending Approval",          "DEFERRED",
     "Decision deferred pending completion of the feasibility study and initial risk assessment. "
     "Team to strengthen the business case and re-present at the next steering committee."),

    ("Phase 1 of 3",        "Active - On Track",         "APPROVED WITH CONDITIONS",
     "Project approved to proceed to Phase 1, subject to: security audit sign-off by the CISO "
     "and legal review of data residency requirements. Budget of {budget} confirmed."),

    ("Phase 1 of 3",        "Active - Slightly Delayed", "ON HOLD",
     "Phase 1 temporarily placed on hold due to resource conflicts with the ERP programme. "
     "A resume decision will be revisited in 8 weeks once capacity is confirmed."),

    ("Phase 2 of 3",        "Active - At Risk",          "APPROVED",
     "Following resolution of the Phase 1 blockers, project reinstated and approved to advance "
     "to Phase 2. All pre-conditions have been met. Budget of {budget} confirmed."),

    ("Phase 2 of 3",        "Active - On Track",         "APPROVED",
     "Phase 2 deliverables reviewed and approved to continue. Progress is on track and within "
     "budget. Revised budget of {budget} approved following scope clarification."),

    ("Phase 3 of 3",        "Active - On Track",         "PILOT APPROVED",
     "Phase 3 milestones met. Pilot approved for {pilot_scope}. Full rollout contingent on "
     "pilot KPIs being met within 12 weeks. Budget of {budget} released for pilot phase."),

    ("UAT",                 "Active - On Track",         "APPROVED",
     "User Acceptance Testing completed with all critical defects resolved. Project approved "
     "to proceed to Go-Live Preparation. Final budget of {budget} confirmed."),

    ("Go-Live Preparation", "Active - On Track",         "FAST-TRACKED",
     "All go-live readiness criteria satisfied. Rollback plan signed off by the CTO. "
     "Project fast-tracked to launch. Budget of {budget} confirmed."),

    ("Hypercare",           "Completed",                 "APPROVED",
     "Project formally closed following a successful hypercare period. All KPIs met. "
     "Post-implementation review scheduled for next quarter. Project transitioned to BAU."),
]

def lifecycle_tier(step_idx: int) -> str:
    if step_idx <= 2: return "early"
    if step_idx <= 5: return "mid"
    return "late"

BUDGETS = [
    "$24,000", "$48,000", "$95,000", "$120,000", "$180,000",
    "$250,000", "$380,000", "$500,000",
    "GBP 40,000", "GBP 75,000", "GBP 150,000",
    "EUR 60,000", "EUR 110,000",
]
PILOT_SCOPES = [
    "two pilot regions (North and East)",
    "a 3-month internal beta with 50 users",
    "a single-tenant deployment for one key client",
]

# ── Discussion points — 3 tiers per domain ────────────────────────────────────
# early = discovery/planning, mid = implementation/progress, late = testing/results
DISCUSSION_POINTS: dict[str, dict[str, list[str]]] = {
    "Engineering": {
        "early": [
            "Initial architecture assessment completed; three potential design patterns under evaluation.",
            "Requirements gathering underway; 14 stakeholder interviews scheduled over the next two weeks.",
            "Proof of concept scoped; team aligned on technology stack and integration approach.",
            "Vendor landscape review initiated; RFI responses from four shortlisted vendors due end of month.",
            "Risk register drafted; key risks include data migration complexity and third-party dependencies.",
        ],
        "mid": [
            "Sprint 4 completed; 58% of Phase 2 deliverables delivered against plan.",
            "Integration with the legacy system identified as a critical path item; workaround agreed.",
            "Code review process overhauled; defect escape rate reduced by 34% since last sprint.",
            "CI/CD pipeline optimised; build and deploy cycle reduced from 45 minutes to 12 minutes.",
            "Security hardening tasks completed; penetration test scheduled for next fortnight.",
        ],
        "late": [
            "Performance testing concluded; system handles 2.4x the anticipated peak load.",
            "UAT sign-off received from all four business domain owners with zero critical defects.",
            "Go-live rehearsal completed successfully; rollback procedure validated end-to-end.",
            "Post-launch monitoring shows error rate of 0.02%, well within the 0.1% threshold.",
            "Hypercare period ended; system handed to the operations team with full runbook.",
        ],
    },
    "Product": {
        "early": [
            "Discovery phase complete; user research with 48 participants surfaced five key pain points.",
            "Competitive benchmarking completed; three feature gaps identified as priority items.",
            "Product requirements document drafted and circulated for stakeholder review.",
            "Wireframes for core user journeys completed; design review scheduled for next week.",
            "Accessibility requirements defined; WCAG 2.1 AA compliance set as the minimum standard.",
        ],
        "mid": [
            "Alpha build released to internal testers; 23 bugs logged, 18 already resolved.",
            "A/B test on the onboarding flow shows a 14% improvement in activation rate.",
            "Feature prioritisation session held; two scope items deferred to Phase 2 to protect timeline.",
            "Beta cohort of 150 users onboarded; weekly feedback sessions producing actionable insights.",
            "Localisation for three new markets underway; translation vendor engaged.",
        ],
        "late": [
            "Beta NPS score of 42 achieved, exceeding the 35 target set at project kick-off.",
            "App store submission completed; review process expected to take 5-7 business days.",
            "Launch day monitoring shows zero P1 incidents; crash rate at 0.04% against 0.5% threshold.",
            "First-week retention at 68%, above the 60% success benchmark.",
            "Post-launch retrospective completed; lessons learned document shared with the product guild.",
        ],
    },
    "Data": {
        "early": [
            "Data discovery exercise completed; 47 source systems identified across the enterprise.",
            "Data quality baseline established; 12% null rate in key fields flagged for remediation.",
            "Governance framework scoped; data steward roles to be assigned to six domain owners.",
            "Cloud data platform vendor evaluation underway; three shortlisted providers being assessed.",
            "Initial data model designed; alignment confirmed with the enterprise architecture team.",
        ],
        "mid": [
            "Data pipeline for 22 of 47 source systems completed; ingestion running on schedule.",
            "Data quality score improved from 72% to 84% following upstream remediation effort.",
            "Model training pipeline automated; run time reduced from 6 hours to 38 minutes.",
            "Data catalogue populated with 1,800 assets; business glossary terms under review.",
            "Real-time streaming layer validated at 900,000 events per second; scaling plan agreed.",
        ],
        "late": [
            "All 47 source systems onboarded; data freshness SLA of 15 minutes consistently met.",
            "Model accuracy validated at 94.2% on holdout data, exceeding the 90% threshold.",
            "Business users trained; self-service query adoption up 61% in the first two weeks.",
            "Data platform uptime at 99.96% in first month of production operation.",
            "Post-implementation review completed; three optimisation recommendations logged for BAU.",
        ],
    },
    "Operations": {
        "early": [
            "As-is process mapping completed; 14 manual handoffs identified as automation candidates.",
            "Vendor shortlisting underway; four suppliers invited to submit detailed proposals.",
            "Change impact assessment completed; 320 staff members identified as directly affected.",
            "Process redesign workshops held with six operational teams over the past two weeks.",
            "Business continuity requirements gathered; RTO and RPO targets agreed with leadership.",
        ],
        "mid": [
            "Three automation workflows deployed; combined saving of 80 person-hours per week.",
            "Vendor transition plan agreed; parallel running period of 6 weeks confirmed.",
            "Change management training completed for 61% of impacted staff; remainder scheduled.",
            "SLA renegotiation with primary supplier concluded; 99.5% uptime commitment secured.",
            "Process conformance audits show 78% adherence to the new standard operating procedures.",
        ],
        "late": [
            "All 14 automation workflows live; monthly saving of 340 person-hours confirmed.",
            "Vendor cutover completed with zero service interruptions during the transition window.",
            "Staff satisfaction survey post-change shows 81% positive response rate.",
            "Operational KPIs all green for first full quarter; MTTR reduced by 22%.",
            "Process audit score of 94% achieved; two minor non-conformances being remediated.",
        ],
    },
    "Finance": {
        "early": [
            "Business case drafted; NPV of GBP 2.1M projected over a 3-year horizon.",
            "Cost-benefit analysis completed; payback period estimated at 22 months.",
            "Finance system integration requirements documented; sign-off from the CFO office pending.",
            "Budget scoping exercise completed; initial estimate of GBP 480,000 submitted for approval.",
            "Funding model agreed; 60% capex, 40% opex split confirmed with Treasury.",
        ],
        "mid": [
            "Year-to-date spend tracking at 94% of budget; underspend attributed to phased hiring.",
            "Mid-project financial review completed; revised forecast within 3% of original estimate.",
            "Procurement savings of GBP 95,000 identified through contract renegotiation.",
            "Cost allocation model updated; departmental recharges aligned to new structure.",
            "Finance system integration 70% complete; parallel running commences next month.",
        ],
        "late": [
            "Project delivered GBP 180,000 under the approved budget envelope.",
            "ROI realisation tracking shows first-year benefit of GBP 340,000 against GBP 290,000 plan.",
            "Finance sign-off on final accounts received; project formally closed from a financial perspective.",
            "Post-project audit completed; no material variances identified.",
            "Benefit realisation report submitted to the board; quarterly tracking to continue for 12 months.",
        ],
    },
    "Compliance": {
        "early": [
            "Regulatory gap analysis completed; 11 gaps identified across three compliance domains.",
            "Legal counsel engaged; external opinion on data residency obligations commissioned.",
            "Compliance requirements documented; mapped to 34 specific control objectives.",
            "Audit scope agreed with the external auditors; fieldwork dates confirmed.",
            "Policy framework drafted; 8 new policies require board ratification before implementation.",
        ],
        "mid": [
            "7 of 11 compliance gaps remediated; remaining 4 on track for closure by next review.",
            "Internal audit of controls completed; 2 medium-severity findings raised.",
            "Staff awareness training delivered to 88% of in-scope population.",
            "Third-party vendor assessments completed for all 12 critical suppliers.",
            "Policy ratification by the board completed; communication to all staff issued.",
        ],
        "late": [
            "External audit completed; zero high-severity findings; 1 medium finding accepted.",
            "Compliance certification received; validity period of 24 months commences today.",
            "All 11 original gaps confirmed closed by the independent auditor.",
            "Ongoing monitoring framework activated; quarterly compliance reporting cadence confirmed.",
            "Lessons learned shared with the broader risk and compliance community of practice.",
        ],
    },
    "HR": {
        "early": [
            "Current-state HR process assessment completed; 9 pain points prioritised for resolution.",
            "Employee survey conducted; 68% of respondents cited administrative burden as a top concern.",
            "HRIS vendor shortlist prepared; three platforms selected for proof of concept evaluation.",
            "Change impact assessment completed; all 850 employees identified as affected to some degree.",
            "HR transformation roadmap drafted; three phases planned over an 18-month delivery window.",
        ],
        "mid": [
            "HRIS configuration completed for core HR and payroll modules; testing underway.",
            "Parallel payroll run completed successfully; variances within the 0.1% acceptable threshold.",
            "Manager self-service training delivered to 120 line managers across all business units.",
            "Employee data migration validated; 99.7% record accuracy confirmed.",
            "Change champion network established; 45 volunteers trained across 12 office locations.",
        ],
        "late": [
            "HRIS go-live completed on schedule; first payroll run processed without incident.",
            "Help desk ticket volume stabilising; 73% reduction in HR admin queries week-on-week.",
            "Employee satisfaction with HR services up 18 percentage points post-implementation.",
            "Attrition rate for the quarter at 7.2%, the lowest in three years.",
            "Post-implementation review confirms all 9 original pain points have been resolved.",
        ],
    },
    "Marketing": {
        "early": [
            "Market research completed; target audience segmentation refined into five distinct personas.",
            "Brand audit findings presented; three strategic repositioning options under consideration.",
            "Campaign brief drafted and shared with the appointed creative agency.",
            "Media planning underway; channel mix to be finalised following budget confirmation.",
            "Customer journey mapping workshop completed; 6 key moments of truth identified.",
        ],
        "mid": [
            "Creative concepts reviewed; two routes selected for audience testing.",
            "Audience testing results positive; preferred route selected and production briefed.",
            "Campaign assets 80% complete; final review scheduled for next week.",
            "Marketing technology integration completed; CRM and email platform now synchronised.",
            "Soft launch to 10% of the target audience shows click-through rate of 4.1%.",
        ],
        "late": [
            "Full campaign launched; reach of 2.4 million impressions in the first week.",
            "Conversion rate of 3.8% achieved, exceeding the 3.0% target by 27%.",
            "Brand recall survey post-campaign shows 12-point improvement vs. pre-campaign baseline.",
            "Marketing ROI of 3.2x confirmed; investment case validated.",
            "Campaign retrospective completed; playbook updated with key learnings for future campaigns.",
        ],
    },
    "Infrastructure": {
        "early": [
            "Infrastructure assessment completed; current state documented across all 6 sites.",
            "Capacity planning exercise finished; 3-year demand forecast agreed with business owners.",
            "Procurement specifications drafted; RFQ issued to 5 qualified suppliers.",
            "Site survey completed; civil works and power requirements confirmed.",
            "Detailed design finalised; sign-off obtained from engineering and security teams.",
        ],
        "mid": [
            "Hardware delivery received and racked; initial configuration in progress.",
            "Network cabling completed at 4 of 6 sites; remaining 2 sites scheduled for next month.",
            "Connectivity testing completed; latency within agreed SLA parameters across all links.",
            "Security controls installed and validated by the information security team.",
            "Parallel running of old and new infrastructure commenced; switchover plan agreed.",
        ],
        "late": [
            "Full cutover completed; all workloads migrated to new infrastructure without incident.",
            "First month uptime recorded at 99.98%, exceeding the 99.9% contractual SLA.",
            "Old infrastructure decommissioned; annual savings of GBP 220,000 confirmed.",
            "DR test completed successfully; RTO of 3.2 hours achieved against 4-hour target.",
            "Post-project review completed; infrastructure team capacity freed for next programme.",
        ],
    },
    "Business": {
        "early": [
            "Market opportunity assessment completed; TAM of USD 3.8B confirmed for target segment.",
            "Due diligence initiated; legal, financial, and operational workstreams underway.",
            "Stakeholder mapping completed; 22 key decision-makers identified across the target market.",
            "Initial partnership term sheet drafted and shared with the counterparty legal team.",
            "Regulatory entry requirements mapped; 4 jurisdictions require pre-approval notification.",
        ],
        "mid": [
            "Due diligence findings presented; no material issues identified; two minor items flagged.",
            "Commercial negotiation at advanced stage; key economic terms agreed in principle.",
            "Go-to-market plan drafted; regional launch sequence confirmed with the sales leadership.",
            "Regulatory filings submitted in all 4 jurisdictions; acknowledgement received from 3.",
            "Partnership governance framework agreed; quarterly steering committee dates confirmed.",
        ],
        "late": [
            "Agreement signed; partnership formally announced via joint press release.",
            "First quarter of operation under the new agreement shows revenue uplift of 18%.",
            "Customer NPS in the new market at 44, above the 40 target set at programme inception.",
            "All 4 regulatory approvals received; full commercial operation commenced.",
            "Post-launch review completed; business case validated; expansion to Phase 2 markets approved.",
        ],
    },
    "Sales": {
        "early": [
            "Sales process review completed; 7 inefficiencies identified in the current qualification methodology.",
            "CRM audit finished; 34% of records flagged as incomplete or duplicate.",
            "Sales territory model redesigned; proposals shared with regional directors for input.",
            "Win/loss analysis of the past 12 months completed; three root causes of losses identified.",
            "Sales enablement content audit done; 60% of existing materials flagged as outdated.",
        ],
        "mid": [
            "New qualification playbook rolled out to 45 account executives; adoption at 78%.",
            "CRM data quality improved to 91% following the cleanse exercise.",
            "First cohort of 15 sales managers completed the new coaching framework training.",
            "Pipeline coverage improved from 2.4x to 3.1x target following process changes.",
            "Sales enablement library refreshed; 120 new assets published and indexed.",
        ],
        "late": [
            "Sales cycle reduced by 14 days; win rate improved from 22% to 29% quarter-on-quarter.",
            "Annual target achieved with 6 weeks remaining in the fiscal year.",
            "Customer acquisition cost reduced by 17% following the process improvements.",
            "Sales team engagement score at 8.1/10, highest recorded in the past 3 years.",
            "Programme retrospective completed; revised playbook to be adopted as the global standard.",
        ],
    },
}

RECOMMENDATIONS: dict[str, dict[str, list[str]]] = {
    "Engineering": {
        "early":  [
            "Approve the proposed architecture and proceed to Phase 1 with a fully staffed delivery team.",
            "Complete the proof of concept before committing to a full build; de-risk the approach.",
            "Engage a specialist security architect to validate the design before Phase 1 commences.",
        ],
        "mid":    [
            "Allocate two additional senior engineers to the critical path to recover the 2-week delay.",
            "Resolve the legacy system integration issue before advancing; do not carry it forward.",
            "Proceed to Phase 3 — all Phase 2 exit criteria have been met.",
        ],
        "late":   [
            "Approve go-live; all readiness checks passed and rollback plan is in place.",
            "Transition to BAU support model; hypercare resourcing can be reduced by 50%.",
            "Close the project formally and capture lessons learned before the team disperses.",
        ],
    },
    "Product": {
        "early":  [
            "Proceed to design and prototyping; user research findings are clear and actionable.",
            "Commission a usability study before finalising the information architecture.",
            "Validate the core value proposition with a smoke-test landing page before full build.",
        ],
        "mid":    [
            "Expand the beta cohort to 500 users to get statistically significant feedback.",
            "Defer the three lower-priority features to maintain the launch date.",
            "Proceed to closed beta; product quality meets the threshold for controlled testing.",
        ],
        "late":   [
            "Approve full public launch; beta results consistently exceed success benchmarks.",
            "Invest in a dedicated retention marketing programme to build on the strong Day-1 metrics.",
            "Close the project and transition ownership to the product operations team.",
        ],
    },
    "Data": {
        "early":  [
            "Approve the data platform architecture and proceed with vendor selection.",
            "Prioritise data quality remediation in the top 5 source systems before pipeline build.",
            "Appoint a dedicated data governance lead before the programme goes beyond discovery.",
        ],
        "mid":    [
            "Accelerate the remaining 25 source system pipelines; current velocity is sufficient to meet the deadline.",
            "Invest in automated data quality monitoring to prevent regression during scale-out.",
            "Proceed to UAT; data accuracy metrics are consistently above the 90% threshold.",
        ],
        "late":   [
            "Approve production go-live; all data quality and performance benchmarks have been met.",
            "Transition the platform to the data engineering BAU team with a structured handover.",
            "Formally close the project; all deliverables accepted by business stakeholders.",
        ],
    },
    "Operations": {
        "early":  [
            "Approve the process redesign and proceed to automation build.",
            "Select the preferred vendor and proceed to contract negotiation.",
            "Complete the change impact assessment before communicating to affected staff.",
        ],
        "mid":    [
            "Accelerate change management delivery; current adoption rate risks the go-live date.",
            "Proceed with the vendor transition as planned; parallel running is performing well.",
            "Approve the deployment of the remaining automation workflows.",
        ],
        "late":   [
            "Formally close the programme; all process KPIs are green.",
            "Hand over ongoing vendor management to the procurement team.",
            "Publish the updated standard operating procedures to the intranet and close the project.",
        ],
    },
    "Finance": {
        "early":  [
            "Approve the business case and release the initial project budget.",
            "Engage the finance system vendor to confirm integration feasibility before committing.",
            "Seek board approval for the capital expenditure component before Phase 1 commences.",
        ],
        "mid":    [
            "Proceed within the current budget envelope; contingency reserve is sufficient.",
            "Request a supplementary budget of GBP 35,000 to cover the scope extension.",
            "Approve the revised cost model and continue to Phase 3.",
        ],
        "late":   [
            "Formally close the project from a financial perspective; final accounts reconciled.",
            "Publish the benefit realisation report to the investment committee.",
            "Transition ongoing financial monitoring to the BAU Finance team.",
        ],
    },
    "Compliance": {
        "early":  [
            "Approve the compliance remediation plan and allocate the necessary resources.",
            "Engage external legal counsel before proceeding; regulatory interpretation is complex.",
            "Prioritise the three highest-risk gaps for immediate remediation.",
        ],
        "mid":    [
            "Proceed to the next phase of control implementation; evidence gathering is on track.",
            "Close the two medium findings before the external audit fieldwork commences.",
            "Approve the updated policy framework for board ratification.",
        ],
        "late":   [
            "Accept the external audit outcome and formally close all remediation actions.",
            "Transition ongoing compliance monitoring to the Risk and Compliance function.",
            "Formally close the project; certification received and all obligations met.",
        ],
    },
    "HR": {
        "early":  [
            "Approve the HR transformation roadmap and proceed to vendor selection.",
            "Commission a detailed change impact assessment before communicating to staff.",
            "Select the HRIS platform and proceed to contract negotiation.",
        ],
        "mid":    [
            "Accelerate manager training delivery to ensure readiness for go-live.",
            "Proceed to parallel payroll run; configuration and testing results are satisfactory.",
            "Approve the data migration plan; accuracy levels meet the go/no-go threshold.",
        ],
        "late":   [
            "Formally close the project; all deliverables accepted and KPIs met.",
            "Transition HR system support to the People Technology BAU team.",
            "Publish the post-implementation review findings to the HR leadership team.",
        ],
    },
    "Marketing": {
        "early":  [
            "Approve the brand strategy and brief the creative agency to begin concept development.",
            "Proceed with the selected media agency; terms are competitive and scope is well-defined.",
            "Validate two creative routes through audience testing before committing to production.",
        ],
        "mid":    [
            "Proceed to full production of the preferred creative route; testing results are conclusive.",
            "Approve the media plan and release the campaign budget for activation.",
            "Expand the soft launch to 25% of the audience; early indicators are positive.",
        ],
        "late":   [
            "Formally close the campaign project; results validated and within budget.",
            "Commission a follow-up brand tracking study to measure sustained impact.",
            "Update the marketing playbook with campaign learnings before the next brief.",
        ],
    },
    "Infrastructure": {
        "early":  [
            "Approve the infrastructure design and proceed to procurement.",
            "Issue the RFQ to the shortlisted suppliers and target award within 4 weeks.",
            "Confirm the civil works timeline with the facilities team before procurement commences.",
        ],
        "mid":    [
            "Proceed with site installation at the remaining two locations as planned.",
            "Approve the switchover date and communicate to all affected business units.",
            "Complete the security validation before moving workloads to the new infrastructure.",
        ],
        "late":   [
            "Proceed with full cutover; parallel running results confirm readiness.",
            "Formally decommission the legacy infrastructure; all workloads confirmed migrated.",
            "Close the project; infrastructure is stable and within agreed SLA parameters.",
        ],
    },
    "Business": {
        "early":  [
            "Approve the market entry strategy and proceed to due diligence.",
            "Engage legal counsel to begin drafting the partnership term sheet.",
            "Commission a detailed regulatory mapping exercise before committing to entry.",
        ],
        "mid":    [
            "Proceed to final negotiation; due diligence findings do not present any blockers.",
            "Submit the regulatory filings in the remaining jurisdiction without delay.",
            "Approve the go-to-market plan and release resources to begin pre-launch activities.",
        ],
        "late":   [
            "Formally close the project; partnership is live and commercial targets are being met.",
            "Commission Phase 2 market expansion scoping given the success of Phase 1.",
            "Publish the business case validation report to the board.",
        ],
    },
    "Sales": {
        "early":  [
            "Approve the sales transformation programme and proceed to playbook development.",
            "Complete the CRM data quality cleanse before rolling out the new processes.",
            "Pilot the new qualification methodology with one regional team before full rollout.",
        ],
        "mid":    [
            "Accelerate adoption of the new playbook; current 78% rate risks missing the target.",
            "Expand the coaching framework training to the remaining 30% of sales managers.",
            "Proceed to full rollout; pilot results confirm the new process improves win rates.",
        ],
        "late":   [
            "Formally close the programme; all KPIs met and improvements embedded.",
            "Adopt the new playbook as the global sales standard.",
            "Transition ongoing sales enablement to the Revenue Operations team.",
        ],
    },
}

# ── Meeting config ─────────────────────────────────────────────────────────────
MEETING_TYPES = [
    ("Portfolio Review",          0.25),
    ("Board Meeting",             0.15),
    ("Planning Session",          0.20),
    ("Programme Retrospective",   0.10),
    ("Client Steering Committee", 0.10),
    ("All-Hands Update",          0.10),
    ("Cross-Functional Sync",     0.10),
]
LOCATIONS = [
    "Board Room A", "Board Room B", "Conference Room 3",
    "Executive Suite", "Microsoft Teams (Hybrid)", "Zoom (Remote)",
    "Innovation Hub, Floor 4",
]
MEETING_TIMES = [
    "0830", "0900", "0930", "1000", "1030",
    "1100", "1400", "1430", "1500", "1530",
]

GENERAL_DISCUSSION_POOL = [
    "Reviewed the overall programme health dashboard and RAG status for all active initiatives.",
    "Discussed cross-project resource conflicts and agreed on a priority order for the quarter.",
    "The programme office confirmed that the project management tooling migration is complete.",
    "Risk register reviewed at programme level; three new risks added and two risks closed.",
    "Budget tracker updated - overall portfolio spend is within 2% of the approved baseline.",
    "Stakeholder satisfaction survey results presented; average score of 7.8 out of 10.",
    "Discussed the implications of the upcoming regulatory change on three active projects.",
    "Agreed to standardise status reporting across all projects using the new RAG template.",
    "The team confirmed readiness for the external auditor visit scheduled for next month.",
    "Programme newsletter distribution to all stakeholders confirmed for end of week.",
]
AOB_POOL = [
    "No further items were raised.",
    "A reminder was issued to submit expense claims before end of month.",
    "The programme newsletter will be distributed to all stakeholders by end of week.",
    "The offsite planning session dates will be circulated by the programme office.",
    "The team confirmed availability for the next meeting as scheduled.",
]
ACTION_VERBS = ["Submit", "Present", "Complete", "Deliver", "Finalise",
                "Circulate", "Review", "Prepare", "Publish", "Coordinate"]
ACTION_TASKS = [
    "updated project plan and revised timeline to all stakeholders",
    "risk register with latest assessment to the programme office",
    "revised budget breakdown to Finance for approval",
    "vendor shortlist and commercial terms for review",
    "go/no-go recommendation to the steering committee",
    "resource allocation plan for the next sprint",
    "dependency mapping with the platform team",
    "stakeholder communication pack",
    "legal review of the contract amendment",
    "technical architecture diagram for sign-off",
    "change management plan",
    "training schedule for all impacted teams",
    "KPI dashboard with updated metrics",
    "post-implementation review agenda",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    return (
        text
        .replace("'", "'").replace("'", "'")
        .replace('"', '"').replace('"', '"')
        .replace("…", "...").replace("—", " - ").replace("–", " - ")
        .encode("latin-1", errors="replace").decode("latin-1")
    )


def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def fmt_date(d: date) -> str:
    return d.strftime("%d %B %Y")


def future_date(d: date, weeks: int) -> str:
    return fmt_date(d + timedelta(weeks=weeks))


def get_lifecycle_step(appearance_idx: int, total_appearances: int) -> tuple:
    """Return (phase, status, decision_label, decision_text, tier)."""
    n = len(LIFECYCLE_STEPS)
    if total_appearances == 1:
        step_idx = random.randint(1, n - 2)
    else:
        step_idx = round(appearance_idx / (total_appearances - 1) * (n - 1))
        step_idx = max(0, min(step_idx, n - 1))

    phase, status, dec_label, dec_tmpl = LIFECYCLE_STEPS[step_idx]
    dec_text = dec_tmpl.format(
        budget=random.choice(BUDGETS),
        pilot_scope=random.choice(PILOT_SCOPES),
    )
    tier = lifecycle_tier(step_idx)
    return phase, status, dec_label, dec_text, tier


def make_actions(owner: str, others: list, due: str) -> list[str]:
    items = [f"{owner}: {random.choice(ACTION_VERBS)} {random.choice(ACTION_TASKS)} - Due: {due}"]
    if others:
        p2, _ = random.choice(others)
        items.append(f"{p2}: {random.choice(ACTION_VERBS)} {random.choice(ACTION_TASKS)} - Due: {due}")
    if random.random() > 0.4 and len(others) > 1:
        p3, _ = random.choice(others)
        items.append(f"{p3}: {random.choice(ACTION_VERBS)} {random.choice(ACTION_TASKS)} - Due: {due}")
    return items


# ── Schedule planner ───────────────────────────────────────────────────────────

def plan_schedules(num_meetings: int) -> tuple[list[date], dict[int, list[dict]]]:
    """
    Returns (sorted_dates, meeting_index -> list of project slots).

    Recurring projects (40): appear 2-10 times, spread chronologically.
    One-off projects (10):   appear exactly once at a random meeting.
    Each meeting gets 4-6 projects.
    """
    start_d, end_d = date(2023, 1, 1), date(2026, 6, 1)
    dates: list[date] = sorted(rand_date(start_d, end_d) for _ in range(num_meetings))

    project_indices: dict[str, list[int]] = {}

    # ── Recurring projects ────────────────────────────────────────────────────
    for proj in RECURRING_PROJECTS:
        n = random.choices(
            [2, 3, 4, 5, 6, 7, 8, 9, 10],
            weights=[8, 12, 15, 18, 15, 12, 10, 6, 4],
        )[0]
        n = min(n, num_meetings - 1)

        # Start in first 60% of timeline so there's room to progress
        max_start = max(0, int(num_meetings * 0.60) - n)
        start = random.randint(0, max_start)

        if n == 1:
            indices = [start]
        else:
            span = num_meetings - start - 1
            raw = []
            for i in range(n):
                frac = i / (n - 1)
                base = start + int(frac * span)
                jitter = random.randint(-2, 2)
                raw.append(max(start, min(num_meetings - 1, base + jitter)))
            seen: set[int] = set()
            indices = []
            for v in sorted(raw):
                while v in seen:
                    v += 1
                if v >= num_meetings:
                    break
                seen.add(v)
                indices.append(v)
            indices = indices[:n]
            while len(indices) < n:
                nxt = indices[-1] + 1
                if nxt < num_meetings:
                    indices.append(nxt)

        project_indices[proj["id"]] = indices

    # ── One-off projects ──────────────────────────────────────────────────────
    for proj in ONEOFF_PROJECTS:
        idx = random.randint(0, num_meetings - 1)
        project_indices[proj["id"]] = [idx]

    # ── Invert to meeting -> slots ────────────────────────────────────────────
    mtg_to_slots: dict[int, list[dict]] = {i: [] for i in range(num_meetings)}
    for proj in PROJECTS:
        pid = proj["id"]
        idx_list = project_indices[pid]
        total = len(idx_list)
        for app_idx, mtg_idx in enumerate(idx_list):
            mtg_to_slots[mtg_idx].append({
                "project":           proj,
                "appearance_idx":    app_idx,
                "total_appearances": total,
            })

    # ── Ensure 4-6 projects per meeting ──────────────────────────────────────
    proj_range: dict[str, tuple[int, int]] = {
        proj["id"]: (
            project_indices[proj["id"]][0],
            project_indices[proj["id"]][-1],
        )
        for proj in PROJECTS
    }

    for i in range(num_meetings):
        slots = mtg_to_slots[i]
        existing_ids = {s["project"]["id"] for s in slots}

        if len(slots) < 4:
            candidates = [
                proj for proj in RECURRING_PROJECTS
                if proj["id"] not in existing_ids
                and proj_range[proj["id"]][0] <= i <= proj_range[proj["id"]][1]
            ]
            random.shuffle(candidates)
            for proj in candidates:
                if len(slots) >= 4:
                    break
                lo, hi = proj_range[proj["id"]]
                app_idx = round((i - lo) / max(1, hi - lo) *
                                (len(project_indices[proj["id"]]) - 1))
                slots.append({
                    "project":           proj,
                    "appearance_idx":    app_idx,
                    "total_appearances": len(project_indices[proj["id"]]),
                })
            mtg_to_slots[i] = slots

        elif len(slots) > 6:
            mtg_to_slots[i] = random.sample(slots, 6)

    return dates, mtg_to_slots


# ── Build one meeting ──────────────────────────────────────────────────────────

def build_meeting(number: int, mtg_date: date, time_str: str, slots: list[dict]) -> dict:
    types, weights = zip(*MEETING_TYPES)
    meeting_type = random.choices(list(types), weights=list(weights))[0]
    next_mtg     = future_date(mtg_date, random.choice([2, 4, 6, 8]))

    org_name, org_role = random.choice(PEOPLE)
    others        = [p for p in PEOPLE if p[0] != org_name]
    attendee_list = random.sample(others, random.randint(4, 8))
    all_attendees = [(org_name, org_role)] + attendee_list

    sections = []
    for slot in slots:
        proj    = slot["project"]
        domain  = proj["domain"]
        app_idx = slot["appearance_idx"]
        total   = slot["total_appearances"]

        phase, status, dec_label, dec_text, tier = get_lifecycle_step(app_idx, total)

        disc_pool = DISCUSSION_POINTS.get(domain, DISCUSSION_POINTS["Engineering"]).get(tier, [])
        disc_pts  = random.sample(disc_pool, min(random.randint(2, 3), len(disc_pool)))

        rec_pool = RECOMMENDATIONS.get(domain, RECOMMENDATIONS["Engineering"]).get(tier, [])
        rec      = random.choice(rec_pool) if rec_pool else "Proceed as planned."

        owner_n, owner_r = random.choice(PEOPLE)
        actions = make_actions(
            owner=owner_n,
            others=random.sample(attendee_list, min(2, len(attendee_list))),
            due=next_mtg,
        )

        sections.append({
            "project":           proj,
            "appearance_idx":    app_idx,
            "total_appearances": total,
            "phase":             phase,
            "status":            status,
            "owner":             f"{owner_n}, {owner_r}",
            "discussion":        disc_pts,
            "recommendation":    rec,
            "decision_label":    dec_label,
            "decision_text":     dec_text,
            "budget":            random.choice(BUDGETS),
            "actions":           actions,
        })

    return {
        "number":       number,
        "meeting_type": meeting_type,
        "date":         mtg_date,
        "date_str":     fmt_date(mtg_date),
        "time_str":     time_str,
        "next_mtg":     next_mtg,
        "organizer":    org_name,
        "org_role":     org_role,
        "attendees":    all_attendees,
        "location":     random.choice(LOCATIONS),
        "general":      random.sample(GENERAL_DISCUSSION_POOL, 3),
        "sections":     sections,
        "aob":          random.choice(AOB_POOL),
    }


# ── PDF renderer ───────────────────────────────────────────────────────────────

W = 190

class MomPDF(FPDF):
    def _rule(self, thick: float = 0.3):
        self.set_line_width(thick)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(2)

    def h1(self, text: str):
        self.set_font("Helvetica", "B", 12)
        self.cell(0, 7, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self._rule(0.5)
        self.set_font("Helvetica", size=10)

    def h2(self, text: str):
        self.set_font("Helvetica", "B", 10)
        self.cell(0, 6, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", size=10)

    def kv(self, label: str, value: str, lw: int = 38):
        self.set_font("Helvetica", "B", 9)
        self.cell(lw, 5, clean(label + ":"), new_x=XPos.RIGHT, new_y=YPos.TOP)
        self.set_font("Helvetica", size=9)
        self.multi_cell(W - lw, 5, clean(value))

    def body(self, text: str):
        self.set_font("Helvetica", size=9)
        self.multi_cell(W, 5, clean(text))
        self.ln(1)

    def bullet(self, text: str, indent: int = 6):
        self.set_x(10 + indent)
        self.set_font("Helvetica", size=9)
        self.multi_cell(W - indent, 5, clean("- " + text))

    def italic_label(self, text: str):
        self.set_font("Helvetica", "BI", 9)
        self.cell(0, 4, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def sp(self, h: float = 3):
        self.ln(h)


def render(meeting: dict, path: Path) -> None:
    pdf = MomPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 9, "MINUTES OF MEETING", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", size=8)
    pdf.cell(0, 5, "Apex Solutions Ltd. - Confidential", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_line_width(0.6)
    pdf.line(10, pdf.get_y() + 2, 200, pdf.get_y() + 2)
    pdf.ln(6)

    pdf.kv("Meeting Type", meeting["meeting_type"])
    pdf.kv("Date",  f"{meeting['date_str']}  at  {meeting['time_str'][:2]}:{meeting['time_str'][2:]} hrs")
    pdf.kv("Location",    meeting["location"])
    pdf.kv("Chairperson", f"{meeting['organizer']} - {meeting['org_role']}")
    pdf.sp(2)

    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, "Attendees:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=9)
    for name, role in meeting["attendees"]:
        pdf.bullet(f"{name}  ({role})", indent=4)
    pdf.sp(4)
    pdf.set_line_width(0.3)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.sp(4)

    pdf.h1("1.  GENERAL DISCUSSION")
    for point in meeting["general"]:
        pdf.bullet(point)
    pdf.sp(4)

    for i, sec in enumerate(meeting["sections"], 1):
        proj  = sec["project"]
        total = sec["total_appearances"]
        app   = sec["appearance_idx"] + 1
        cont  = f"  (Review {app} of {total})" if total > 1 else ""
        pdf.h2(f"1.{i}  {proj['name']}   [Project ID: {proj['id']}]{cont}")

        pdf.kv("Status", sec["status"], lw=30)
        pdf.kv("Phase",  sec["phase"],  lw=30)
        pdf.kv("Owner",  sec["owner"],  lw=30)
        pdf.sp(2)

        pdf.italic_label("Discussion Points:")
        for pt in sec["discussion"]:
            pdf.bullet(pt)
        pdf.sp(2)

        pdf.italic_label("Recommendation:")
        pdf.body(sec["recommendation"])

        pdf.italic_label(f"Decision:  {sec['decision_label']}")
        pdf.body(sec["decision_text"])

        pdf.kv("Budget Allocation", sec["budget"])
        pdf.sp(2)

        pdf.italic_label("Action Items:")
        for act in sec["actions"]:
            pdf.bullet(act)
        pdf.sp(5)

    pdf.set_line_width(0.3)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.sp(3)
    pdf.h1("2.  ANY OTHER BUSINESS")
    pdf.body(meeting["aob"])
    pdf.sp(4)

    pdf.h1("3.  NEXT MEETING AND SIGN-OFF")
    pdf.kv("Next Meeting", meeting["next_mtg"])
    pdf.sp(3)
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 5, f"Minutes approved by:  {meeting['organizer']},  {meeting['org_role']}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.sp(2)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 4, "- End of Minutes -", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.output(str(path))


# ── JSON writer — unchanged from original ──────────────────────────────────────

def save_json(meeting: dict, path: Path) -> None:
    payload = {
        "meeting_number": meeting["number"],
        "meeting_type":   meeting["meeting_type"],
        "date":           meeting["date_str"],
        "time":           f"{meeting['time_str'][:2]}:{meeting['time_str'][2:]}",
        "organizer":      meeting["organizer"],
        "location":       meeting["location"],
        "projects": [
            {
                "project_name":      sec["project"]["name"],
                "project_id":        sec["project"]["id"],
                "status":            sec["status"],
                "recommendation":    sec["recommendation"],
                "decision":          f"{sec['decision_label']} - {sec['decision_text']}",
                "budget_allocation": sec["budget"],
                "action_items":      sec["actions"],
            }
            for sec in meeting["sections"]
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ── Main ───────────────────────────────────────────────────────────────────────

def main(n: int = 100) -> None:
    print(f"Planning schedules for {n} meetings "
          f"({len(RECURRING_PROJECTS)} recurring + {len(ONEOFF_PROJECTS)} one-off projects)...")
    sorted_dates, mtg_to_slots = plan_schedules(n)

    print(f"Generating documents\n"
          f"  PDFs -> {OUTPUT_DIR}\n")

    times = [random.choice(MEETING_TIMES) for _ in range(n)]

    for i in range(n):
        number   = i + 1
        mtg_date = sorted_dates[i]
        time_str = times[i]
        slots    = mtg_to_slots[i]

        stem = f"mom_{number:03d}_{mtg_date.strftime('%Y%m%d')}_{time_str}"
        meeting = build_meeting(number, mtg_date, time_str, slots)

        render(meeting, OUTPUT_DIR / f"{stem}.pdf")

        if number % 10 == 0 or number == n:
            print(f"  [{number:3d}/{n}]  {stem}")

    # Appearance stats
    from collections import Counter
    proj_count: dict[str, int] = {p["id"]: 0 for p in PROJECTS}
    for slots in mtg_to_slots.values():
        for s in slots:
            proj_count[s["project"]["id"]] += 1
    counts = list(proj_count.values())
    print(f"\nDone. {n} PDFs generated.")
    print(f"Project appearance stats:")
    print(f"  Projects : {len(PROJECTS)} total "
          f"({len(RECURRING_PROJECTS)} recurring, {len(ONEOFF_PROJECTS)} one-off)")
    print(f"  Min      : {min(counts)}")
    print(f"  Max      : {max(counts)}")
    print(f"  Average  : {sum(counts)/len(counts):.1f} appearances per project")
    print(f"  Total slots : {sum(counts)}")


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    main(count)
