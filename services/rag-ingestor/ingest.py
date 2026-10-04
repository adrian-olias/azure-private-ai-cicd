#!/usr/bin/env python3
"""
ingest.py — Local Document Ingestion into Open-WebUI RAG

SINGLE RESPONSIBILITY: Take the files that `storage-sync` downloaded to
/data/docs and publish them into the Open-WebUI knowledge base.

  1. Uploads the file to /api/v1/files/ (triggers extraction + embeddings).
  2. Waits for indexing to complete.
  3. Links the file to the knowledge base.
  4. Removes documents from the KB that no longer exist on disk.

Assumes that `webui-bootstrap` has already created the admin and KB.
Does not touch SQLite: everything goes through the REST API.
"""

import json
import mimetypes
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import schedule

WEBUI_URL = os.environ.get("OPEN_WEBUI_URL", "http://open-webui:8080").strip().rstrip("/")
ADMIN_EMAIL = os.environ["WEBUI_ADMIN_EMAIL"].strip()
ADMIN_PASSWORD = os.environ["WEBUI_ADMIN_PASSWORD"]

KB_NAME = os.environ.get("KNOWLEDGE_BASE_NAME", "Company Documents").strip()
DOCS_PATH = Path(os.environ.get("DOCS_DOWNLOAD_PATH", "/data/docs").strip())
STATE_FILE = Path(os.environ.get("INGEST_STATE_PATH", "/data/state/.ingest_state.json").strip())

INGEST_INTERVAL = int(os.environ.get("INGEST_INTERVAL_MINUTES", "15"))
PROCESS_TIMEOUT = int(os.environ.get("INGEST_PROCESS_TIMEOUT", "300"))

ALLOWED_DOC_EXTENSIONS = {
    ".pdf", ".xlsx", ".xls", ".docx", ".doc",
    ".txt", ".csv", ".md", ".pptx", ".rtf",
}


def log(msg: str) -> None:
    print(msg, flush=True)


# ─── Local State ──────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log(f"   ⚠️  State unreadable ({e}). Rebuilding.")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_FILE)


# ─── Open-WebUI API ───────────────────────────────────────────────────────────

def get_token() -> str | None:
    try:
        r = requests.post(
            f"{WEBUI_URL}/api/v1/auths/signin",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
    except requests.RequestException as e:
        log(f"   ⚠️  Open-WebUI inaccessible: {e}")
        return None

    if r.status_code == 200:
        return r.json().get("token")

    log(f"   ⚠️  Login rejected ({r.status_code}). Did webui-bootstrap run?")
    return None


def find_knowledge_base(headers: dict) -> str | None:
    try:
        r = requests.get(f"{WEBUI_URL}/api/v1/knowledge/", headers=headers, timeout=15)
    except requests.RequestException as e:
        log(f"   ⚠️  Could not list knowledge bases: {e}")
        return None

    if r.status_code != 200:
        log(f"   ⚠️  KB list returned {r.status_code}.")
        return None

    payload = r.json()
    items = payload if isinstance(payload, list) else payload.get("items", [])
    for kb in items:
        if isinstance(kb, dict) and kb.get("name") == KB_NAME:
            return kb["id"]

    log(f"   ⚠️  Knowledge base '{KB_NAME}' does not exist. Waiting for bootstrap.")
    return None


def wait_until_processed(headers: dict, file_id: str) -> bool:
    """Polls the file status until indexing is complete."""
    deadline = time.time() + PROCESS_TIMEOUT

    while time.time() < deadline:
        try:
            r = requests.get(
                f"{WEBUI_URL}/api/v1/files/{file_id}/process/status",
                headers=headers,
                timeout=10,
            )
        except requests.RequestException:
            time.sleep(3)
            continue

        if r.status_code == 404:
            # Versions without this endpoint handle processing synchronously on upload
            return True

        if r.status_code == 200:
            body = r.json()
            status = body.get("status") if isinstance(body, dict) else str(body)
            if status == "completed":
                return True
            if status == "failed":
                log("      ❌ Open-WebUI marked processing as failed.")
                return False

        time.sleep(3)

    log(f"      ⚠️  Indexing not confirmed within {PROCESS_TIMEOUT}s.")
    return False


def upload_document(headers: dict, kb_id: str, path: Path) -> str | None:
    """Uploads a file and links it to the KB. Returns the file_id or None."""
    mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"

    log(f"      📤 Uploading '{path.name}'...")
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"{WEBUI_URL}/api/v1/files/",
                headers=headers,
                files={"file": (path.name, f, mime_type)},
                timeout=180,
            )
    except requests.RequestException as e:
        log(f"      ❌ Network error during upload: {e}")
        return None

    if r.status_code != 200:
        log(f"      ❌ Upload rejected: {r.status_code} — {r.text}")
        return None

    file_id = r.json().get("id")
    if not file_id:
        log("      ❌ Upload response missing an identifier.")
        return None

    if not wait_until_processed(headers, file_id):
        return None

    try:
        r = requests.post(
            f"{WEBUI_URL}/api/v1/knowledge/{kb_id}/file/add",
            headers=headers,
            json={"file_id": file_id},
            timeout=30,
        )
    except requests.RequestException as e:
        log(f"      ❌ Network error linking to KB: {e}")
        return None

    if r.status_code != 200:
        log(f"      ❌ Link to KB rejected: {r.status_code} — {r.text}")
        return None

    log("      ✅ Document indexed and linked.")
    return file_id


def remove_document(headers: dict, kb_id: str, file_id: str) -> None:
    """Unlinks a file from the KB and then deletes it from storage."""
    try:
        requests.post(
            f"{WEBUI_URL}/api/v1/knowledge/{kb_id}/file/remove",
            headers=headers,
            json={"file_id": file_id},
            timeout=30,
        )
    except requests.RequestException as e:
        log(f"      ⚠️  Could not unlink {file_id}: {e}")

    try:
        requests.delete(f"{WEBUI_URL}/api/v1/files/{file_id}", headers=headers, timeout=30)
    except requests.RequestException as e:
        log(f"      ⚠️  Could not delete {file_id}: {e}")


# ─── Ingestion Loop ───────────────────────────────────────────────────────────

def ingest() -> None:
    log(f"\n{'═' * 65}")
    log(f"📚 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Ingestion started")
    log(f"{'═' * 65}")

    token = get_token()
    if not token:
        return

    headers = {"Authorization": f"Bearer {token}"}
    kb_id = find_knowledge_base(headers)
    if not kb_id:
        return

    state = load_state()
    entries = state.get("documents", {})
    if state.get("kb_id") != kb_id:
        # KB was recreated: previous state points to orphaned files.
        log("   ♻️  Knowledge base changed. Reindexing everything.")
        entries = {}

    local_files = sorted(
        f for f in DOCS_PATH.glob("**/*")
        if f.is_file() and f.suffix.lower() in ALLOWED_DOC_EXTENSIONS
    )
    seen: set[str] = set()
    uploaded = 0

    for path in local_files:
        key = str(path.relative_to(DOCS_PATH))
        seen.add(key)
        mtime = str(path.stat().st_mtime)

        previous = entries.get(key)
        if isinstance(previous, dict) and previous.get("mtime") == mtime:
            continue

        if isinstance(previous, dict) and previous.get("file_id"):
            log(f"      🔄 '{key}' changed. Replacing previous version.")
            remove_document(headers, kb_id, previous["file_id"])

        file_id = upload_document(headers, kb_id, path)
        if file_id:
            entries[key] = {"file_id": file_id, "mtime": mtime}
            uploaded += 1

    # Documents deleted from Azure → remove from RAG
    removed = 0
    for key in list(entries):
        if key not in seen:
            log(f"      🗑️  '{key}' no longer exists at source. Removing from RAG.")
            remove_document(headers, kb_id, entries[key]["file_id"])
            del entries[key]
            removed += 1

    save_state({"kb_id": kb_id, "documents": entries})

    log(f"\n{'─' * 65}")
    log(f"   🏁 {uploaded} indexed, {removed} removed, {len(entries)} total in KB")
    log(f"   ⏲️  Next run in {INGEST_INTERVAL} minutes")
    log(f"{'═' * 65}")


def main() -> None:
    log("🚀 RAG Ingestor — Disk → Open-WebUI")
    log(f"   📂 Source: {DOCS_PATH}")
    log(f"   🌐 Target: {WEBUI_URL} (KB '{KB_NAME}')")
    log(f"   ⏲️  Interval: every {INGEST_INTERVAL} minute(s)")
    log("")

    if not DOCS_PATH.exists():
        log(f"   ❌ Documents directory {DOCS_PATH} does not exist.")
        sys.exit(1)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    ingest()

    schedule.every(INGEST_INTERVAL).minutes.do(ingest)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
