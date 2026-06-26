"""
Upload all MOM PDFs from data/documents/ to Azure Blob Storage.

Usage (run from backend/):
    python -m app.scripts.upload_to_blob
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv

_ROOT    = Path(__file__).parent.parent.parent
DOCS_DIR = _ROOT / "data" / "documents"
ENV_FILE = _ROOT / ".env"


def main() -> None:
    if not ENV_FILE.exists():
        sys.exit(f"ERROR: .env not found at {ENV_FILE}")
    load_dotenv(ENV_FILE)

    conn_str  = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "mom-rag-documents")

    if not conn_str:
        sys.exit("ERROR: AZURE_STORAGE_CONNECTION_STRING not set in .env")

    pdfs = sorted(DOCS_DIR.glob("mom_*.pdf"))
    if not pdfs:
        sys.exit(f"ERROR: No PDFs found in {DOCS_DIR}")

    print(f"Connecting to Azure Blob Storage (container: {container}) ...")
    client = BlobServiceClient.from_connection_string(conn_str)
    container_client = client.get_container_client(container)

    content_settings = ContentSettings(
        content_type="application/pdf",
        content_disposition="inline",
    )

    print(f"Uploading {len(pdfs)} PDFs ...\n")
    uploaded = failed = 0

    for i, pdf_path in enumerate(pdfs, 1):
        blob_name = pdf_path.name
        try:
            blob_client = container_client.get_blob_client(blob_name)
            with open(pdf_path, "rb") as f:
                blob_client.upload_blob(f, overwrite=True, content_settings=content_settings)
            uploaded += 1
            if i % 10 == 0 or i == len(pdfs):
                print(f"  [{i:3d}/{len(pdfs)}]  {blob_name}")
        except Exception as exc:
            print(f"  FAILED: {blob_name} — {exc}")
            failed += 1

    print(f"\nDone. {uploaded} uploaded, {failed} failed.")


if __name__ == "__main__":
    main()
