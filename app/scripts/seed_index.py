"""
Seed Pinecone with MOM PDF chunks.

Reads every PDF from  data/documents/
Matches the JSON from  data/json_templates/  (same stem)
Chunks each PDF with RecursiveCharacterTextSplitter
Embeds each chunk with text-embedding-3-small
Upserts into the Pinecone index defined in .env

Usage (run from backend/):
    python -m app.scripts.seed_index              # dry-run: shows stats, no upsert
    python -m app.scripts.seed_index --confirm    # actually upserts to Pinecone

Safety: pass --confirm explicitly. Without it the script exits before any upsert.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
from pinecone import Pinecone

# ── Paths ──────────────────────────────────────────────────────────────────────
_ROOT      = Path(__file__).parent.parent.parent
DOCS_DIR   = _ROOT / "data" / "documents"
JSON_DIR   = _ROOT / "data" / "json_templates"
ENV_FILE   = _ROOT / ".env"

# ── Config ─────────────────────────────────────────────────────────────────────
CHUNK_SIZE    = 1000
CHUNK_OVERLAP = 150
UPSERT_BATCH  = 50   # Pinecone recommends <= 100 vectors per batch
EMBED_BATCH   = 20   # OpenAI: embed N chunks per API call

# Filename pattern: mom_001_20230221_0930.pdf
_FNAME_RE = re.compile(
    r"mom_(?P<num>\d+)_(?P<date>\d{8})_(?P<time>\d{4})\.pdf",
    re.IGNORECASE,
)


def parse_filename(name: str) -> tuple[str, str] | None:
    """Return (meeting_date 'YYYY-MM-DD', meeting_time 'HH:MM') or None."""
    m = _FNAME_RE.match(name)
    if not m:
        return None
    d = m.group("date")          # 20230221
    t = m.group("time")          # 0930
    date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    time_str = f"{t[:2]}:{t[2:]}"
    return date_str, time_str


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return list of (page_number_1indexed, page_text)."""
    pages: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                pages.append((i, text))
    return pages


def build_chunks(
    pages: list[tuple[int, str]],
    splitter: RecursiveCharacterTextSplitter,
) -> list[tuple[int, str]]:
    """
    Chunk each page independently so page_number stays accurate.
    Returns list of (page_number, chunk_text).
    """
    result: list[tuple[int, str]] = []
    for page_num, text in pages:
        splits = splitter.split_text(text)
        for chunk in splits:
            chunk = chunk.strip()
            if chunk:
                result.append((page_num, chunk))
    return result


def embed_texts(client: OpenAI, texts: list[str], model: str) -> list[list[float]]:
    """Embed a batch of texts; returns list of float vectors."""
    response = client.embeddings.create(input=texts, model=model)
    return [item.embedding for item in response.data]


def build_vector_id(stem: str, chunk_idx: int) -> str:
    """e.g. mom_001_20230221_0930_c0042"""
    return f"{stem}_c{chunk_idx:04d}"


def load_json(json_path: Path) -> str:
    """Return the JSON file as a compact string, or empty string if missing."""
    if not json_path.exists():
        return ""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def run(confirm: bool) -> None:
    # ── Load env ───────────────────────────────────────────────────────────────
    if not ENV_FILE.exists():
        sys.exit(f"ERROR: .env not found at {ENV_FILE}")
    load_dotenv(ENV_FILE)

    openai_key    = os.environ.get("OPENAI_API_KEY", "")
    pinecone_key  = os.environ.get("PINECONE_API_KEY", "")
    index_name    = os.environ.get("PINECONE_INDEX_NAME", "mom-rag")
    embed_model   = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    if not openai_key or not pinecone_key:
        sys.exit("ERROR: OPENAI_API_KEY or PINECONE_API_KEY missing from .env")

    # ── Discover PDFs ──────────────────────────────────────────────────────────
    pdf_files = sorted(DOCS_DIR.glob("mom_*.pdf"))
    if not pdf_files:
        sys.exit(f"ERROR: No PDFs found in {DOCS_DIR}")

    print(f"Found {len(pdf_files)} PDFs in {DOCS_DIR}")
    print(f"Chunk size: {CHUNK_SIZE} chars  |  Overlap: {CHUNK_OVERLAP} chars")
    print()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    # ── Pre-flight: collect all vectors (dry-run always, upsert only if --confirm)
    all_vectors: list[dict] = []
    skipped = 0

    for pdf_path in pdf_files:
        parsed = parse_filename(pdf_path.name)
        if not parsed:
            print(f"  SKIP (bad filename): {pdf_path.name}")
            skipped += 1
            continue

        meeting_date, meeting_time = parsed
        stem      = pdf_path.stem                    # mom_001_20230221_0930
        json_path = JSON_DIR / f"{stem}.json"
        analysis  = load_json(json_path)

        # Extract project IDs for direct Pinecone filtering
        project_ids: list[str] = []
        if analysis:
            try:
                analysis_obj = json.loads(analysis)
                project_ids = [
                    p.get("project_id", "").strip()
                    for p in analysis_obj.get("projects", [])
                    if p.get("project_id", "").strip()
                ]
            except (json.JSONDecodeError, AttributeError):
                pass

        pages  = extract_pages(pdf_path)
        chunks = build_chunks(pages, splitter)

        for chunk_idx, (page_num, chunk_text) in enumerate(chunks, 1):
            all_vectors.append({
                "id": build_vector_id(stem, chunk_idx),
                "metadata": {
                    "document_name":          pdf_path.name,
                    "meeting_date":           meeting_date,
                    "meeting_time":           meeting_time,
                    "page_number":            page_num,
                    "mom_meeting_text_chunk": chunk_text,
                    "mom_analysis_data":      analysis,
                    "project_ids":            project_ids,
                },
                "_text": chunk_text,   # used for embedding, stripped before upsert
            })

    print(f"Total vectors to upsert : {len(all_vectors)}")
    print(f"PDFs skipped            : {skipped}")
    avg = len(all_vectors) / max(len(pdf_files) - skipped, 1)
    print(f"Avg chunks per PDF      : {avg:.1f}")
    print()

    if not confirm:
        print("Dry-run complete. No data was sent to Pinecone.")
        print("Re-run with  --confirm  to upsert.")
        return

    # ── Live upsert ────────────────────────────────────────────────────────────
    oai    = OpenAI(api_key=openai_key)
    pc     = Pinecone(api_key=pinecone_key)
    index  = pc.Index(index_name)

    texts  = [v["_text"] for v in all_vectors]
    total  = len(texts)
    print(f"Embedding {total} chunks in batches of {EMBED_BATCH}...")

    embeddings: list[list[float]] = []
    for start in range(0, total, EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        vecs  = embed_texts(oai, batch, embed_model)
        embeddings.extend(vecs)
        done = min(start + EMBED_BATCH, total)
        print(f"  Embedded {done}/{total}")
        time.sleep(0.1)   # gentle rate-limit buffer

    print()
    print(f"Upserting {total} vectors in batches of {UPSERT_BATCH}...")

    upserted = 0
    for start in range(0, total, UPSERT_BATCH):
        batch_v = all_vectors[start : start + UPSERT_BATCH]
        batch_e = embeddings[start : start + UPSERT_BATCH]

        pinecone_batch = [
            {
                "id":       v["id"],
                "values":   e,
                "metadata": {k: val for k, val in v["metadata"].items()},
            }
            for v, e in zip(batch_v, batch_e)
        ]

        index.upsert(vectors=pinecone_batch)
        upserted += len(pinecone_batch)
        print(f"  Upserted {upserted}/{total}")

    print()
    print(f"Done. {upserted} vectors indexed into '{index_name}'.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Pinecone with MOM PDF chunks.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually upsert to Pinecone. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()
    run(confirm=args.confirm)


if __name__ == "__main__":
    main()
