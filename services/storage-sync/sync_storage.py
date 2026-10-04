#!/usr/bin/env python3
"""
sync_storage.py — Azure Storage → Local Volumes

Single responsibility: download blobs from Azure to disk. Nothing else.

  1. /documents  → /data/docs    (picked up by rag-ingestor)
  2. /config     → /data/config  (logo, system_prompt.txt, templates)

This service does NOT talk to the Open-WebUI API or touch its database.
RAG ingestion is handled by `rag-ingestor`; admin/KB/model setup by `webui-bootstrap`.

Uses a JSON manifest (in its own state volume) to download only new or modified blobs.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import schedule
from azure.storage.blob import BlobServiceClient

# ─── Configuration from environment variables ─────────────────────────────────

CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"].strip()

DOCS_CONTAINER = os.environ.get("AZURE_DOCS_CONTAINER", "documents").strip()
DOCS_DOWNLOAD_PATH = Path(os.environ.get("DOCS_DOWNLOAD_PATH", "/data/docs").strip())

CONFIG_CONTAINER = os.environ.get("AZURE_CONFIG_CONTAINER", "config").strip()
CONFIG_DOWNLOAD_PATH = Path(os.environ.get("CONFIG_DOWNLOAD_PATH", "/data/config").strip())

SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL_MINUTES", "15").strip())

MANIFEST_FILE = Path(os.environ.get("MANIFEST_PATH", "/data/state/.sync_manifest.json").strip())

# Document extensions worth sending to the RAG pipeline
ALLOWED_DOC_EXTENSIONS = {
    ".pdf", ".xlsx", ".xls", ".docx", ".doc",
    ".txt", ".csv", ".md", ".pptx", ".rtf",
}


# ─── Manifest ─────────────────────────────────────────────────────────────────

def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"   ⚠️  Manifest unreadable ({e}). Rebuilding from scratch.")
    return {}


def save_manifest(manifest: dict) -> None:
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
    tmp.replace(MANIFEST_FILE)


# ─── Download ─────────────────────────────────────────────────────────────────

def safe_target_path(root: Path, blob_name: str) -> Path | None:
    """
    Resolves the target path for a blob, preventing path traversal attacks.
    A blob named '../../etc/passwd' could escape the volume — discard it.
    """
    root_resolved = root.resolve()
    candidate = (root_resolved / blob_name).resolve()
    if candidate == root_resolved or root_resolved not in candidate.parents:
        return None
    return candidate


def sync_container(
    blob_service: BlobServiceClient,
    container_name: str,
    download_path: Path,
    manifest: dict,
    allowed_extensions: set | None = None,
) -> int:
    """Synchronizes an Azure container with a local directory."""
    download_path.mkdir(parents=True, exist_ok=True)

    try:
        container_client = blob_service.get_container_client(container_name)
        container_client.get_container_properties()
    except Exception as e:
        print(f"   ⚠️  Container '{container_name}' not accessible: {e}")
        return 0

    downloaded = 0
    skipped = 0

    for blob in container_client.list_blobs():
        if allowed_extensions and Path(blob.name).suffix.lower() not in allowed_extensions:
            continue

        manifest_key = f"{container_name}/{blob.name}"
        last_modified = blob.last_modified.isoformat()

        if manifest.get(manifest_key) == last_modified:
            skipped += 1
            continue

        local_path = safe_target_path(download_path, blob.name)
        if local_path is None:
            print(f"   🚫 Suspicious blob path, skipped: {blob.name}")
            continue

        local_path.parent.mkdir(parents=True, exist_ok=True)
        size_kb = (blob.size or 0) / 1024
        print(f"   📥 {container_name}/{blob.name} ({size_kb:.1f} KB)")

        blob_client = container_client.get_blob_client(blob.name)
        with open(local_path, "wb") as f:
            f.write(blob_client.download_blob().readall())

        manifest[manifest_key] = last_modified
        downloaded += 1

    if skipped:
        print(f"   ⏭️  {container_name}: {skipped} unchanged")

    return downloaded


# ─── Main sync loop ───────────────────────────────────────────────────────────

def sync_all() -> None:
    print(f"\n{'═' * 65}")
    print(f"🔄 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Sync started")
    print(f"{'═' * 65}")

    try:
        blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
        manifest = load_manifest()
        total = 0

        print(f"\n   📂 {DOCS_CONTAINER} → {DOCS_DOWNLOAD_PATH}")
        count = sync_container(
            blob_service=blob_service,
            container_name=DOCS_CONTAINER,
            download_path=DOCS_DOWNLOAD_PATH,
            manifest=manifest,
            allowed_extensions=ALLOWED_DOC_EXTENSIONS,
        )
        total += count
        print(f"   ✅ Documents: {count} new")

        print(f"\n   ⚙️  {CONFIG_CONTAINER} → {CONFIG_DOWNLOAD_PATH}")
        count = sync_container(
            blob_service=blob_service,
            container_name=CONFIG_CONTAINER,
            download_path=CONFIG_DOWNLOAD_PATH,
            manifest=manifest,
            allowed_extensions=None,
        )
        total += count
        print(f"   ✅ Config: {count} new")

        save_manifest(manifest)

        print(f"\n{'─' * 65}")
        print(f"   🏁 Total downloaded: {total} file(s)")
        print(f"   ⏲️  Next run in {SYNC_INTERVAL} minutes")
        print(f"{'═' * 65}")

    except Exception as e:
        print(f"\n   ❌ Error during sync: {e}")


def main() -> None:
    print("🚀 Storage Sync — Azure Storage → Disk (single responsibility)")
    print(f"   📂 Documents:  {DOCS_CONTAINER} → {DOCS_DOWNLOAD_PATH}")
    print(f"   ⚙️  Config:     {CONFIG_CONTAINER} → {CONFIG_DOWNLOAD_PATH}")
    print(f"   ⏲️  Interval:   every {SYNC_INTERVAL} minute(s)")
    print()

    DOCS_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
    CONFIG_DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)

    sync_all()

    schedule.every(SYNC_INTERVAL).minutes.do(sync_all)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
