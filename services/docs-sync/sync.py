#!/usr/bin/env python3
"""
docs-sync: Azure Blob Storage -> Open WebUI knowledge base.

Every SYNC_INTERVAL_MINUTES:
  1. Lists the blobs in the documents container. Authenticates with the VM's
     managed identity (DefaultAzureCredential), so there is no key or
     connection string anywhere.
  2. Uploads new or changed blobs to Open WebUI and adds them to the
     knowledge base created by webui-bootstrap.
  3. Removes from the knowledge base the documents deleted from Storage.

A small state file (blob name -> etag and Open WebUI file id) keeps each run
incremental. Everything goes through the Open WebUI REST API.
"""

import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = os.environ["AZURE_STORAGE_ACCOUNT"].strip()
DOCS_CONTAINER = os.environ["AZURE_DOCS_CONTAINER"].strip()

WEBUI_URL = os.environ["OPEN_WEBUI_URL"].strip().rstrip("/")
ADMIN_EMAIL = os.environ["WEBUI_ADMIN_EMAIL"].strip()
ADMIN_PASSWORD = os.environ["WEBUI_ADMIN_PASSWORD"]
KB_NAME = os.environ["KNOWLEDGE_BASE_NAME"].strip()

SYNC_INTERVAL = int(os.environ["SYNC_INTERVAL_MINUTES"])
PROCESS_TIMEOUT = 300  # seconds Open WebUI may take to index one file
STATE_FILE = Path("/data/state/docs-sync.json")  # on the docs_sync_state volume

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md"}


def log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class Document:
    name: str
    etag: str
    content: bytes | None = None


# --- Source: Azure Blob Storage ----------------------------------------------

class BlobSource:
    def __init__(self) -> None:
        service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
        self.container = service.get_container_client(DOCS_CONTAINER)

    def list(self) -> list[Document]:
        return [
            Document(name=b.name, etag=b.etag)
            for b in self.container.list_blobs()
            if Path(b.name).suffix.lower() in ALLOWED_EXTENSIONS
        ]

    def read(self, doc: Document) -> bytes:
        return self.container.download_blob(doc.name).readall()


# --- Target: Open WebUI knowledge base ---------------------------------------

class KnowledgeBase:
    def __init__(self) -> None:
        r = requests.post(
            f"{WEBUI_URL}/api/v1/auths/signin",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
        r.raise_for_status()
        self.headers = {"Authorization": f"Bearer {r.json()['token']}"}
        self.id = self._find_id()

    def _find_id(self) -> str:
        r = requests.get(f"{WEBUI_URL}/api/v1/knowledge/", headers=self.headers, timeout=15)
        r.raise_for_status()
        payload = r.json()
        items = payload if isinstance(payload, list) else payload.get("items", [])
        for kb in items:
            if kb.get("name") == KB_NAME:
                return kb["id"]
        raise RuntimeError(f"knowledge base '{KB_NAME}' not found; webui-bootstrap creates it")

    def add(self, name: str, content: bytes) -> str:
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        r = requests.post(
            f"{WEBUI_URL}/api/v1/files/",
            headers=self.headers,
            files={"file": (Path(name).name, content, mime)},
            timeout=180,
        )
        r.raise_for_status()
        file_id = r.json()["id"]
        self._wait_until_processed(file_id)

        r = requests.post(
            f"{WEBUI_URL}/api/v1/knowledge/{self.id}/file/add",
            headers=self.headers,
            json={"file_id": file_id},
            timeout=60,
        )
        r.raise_for_status()
        return file_id

    def remove(self, file_id: str) -> None:
        # A failure is logged, not raised: the document may already be gone, and the
        # state keeps the entry, so the next run tries again
        for r in (
            requests.post(
                f"{WEBUI_URL}/api/v1/knowledge/{self.id}/file/remove",
                headers=self.headers,
                json={"file_id": file_id},
                timeout=30,
            ),
            requests.delete(f"{WEBUI_URL}/api/v1/files/{file_id}", headers=self.headers, timeout=30),
        ):
            if not r.ok:
                log(f"  warning: {r.status_code} removing file {file_id}")

    def _wait_until_processed(self, file_id: str) -> None:
        deadline = time.time() + PROCESS_TIMEOUT
        while time.time() < deadline:
            r = requests.get(
                f"{WEBUI_URL}/api/v1/files/{file_id}/process/status",
                headers=self.headers,
                timeout=10,
            )
            if r.ok:
                status = r.json().get("status")
                if status == "completed":
                    return
                if status == "failed":
                    raise RuntimeError(f"Open WebUI could not process file {file_id}")
            time.sleep(3)
        raise TimeoutError(f"file {file_id} not processed within {PROCESS_TIMEOUT}s")


# --- Sync --------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def sync(source, kb: KnowledgeBase) -> None:
    state = load_state()
    if state.get("kb_id") != kb.id:
        state = {"kb_id": kb.id, "documents": {}}  # knowledge base recreated: start over
    known = state["documents"]

    docs = source.list()
    added = removed = 0

    for doc in docs:
        previous = known.get(doc.name)
        if previous and previous["etag"] == doc.etag:
            continue
        if previous:
            kb.remove(previous["file_id"])
        try:
            file_id = kb.add(doc.name, source.read(doc))
        except Exception as e:  # one bad document must not stop the others
            log(f"  failed  {doc.name}: {e}")
            known.pop(doc.name, None)
            continue
        known[doc.name] = {"etag": doc.etag, "file_id": file_id}
        added += 1
        log(f"  added   {doc.name}")

    current = {d.name for d in docs}
    for name in [n for n in known if n not in current]:
        kb.remove(known.pop(name)["file_id"])
        removed += 1
        log(f"  removed {name}")

    save_state(state)
    log(f"sync done: {added} added, {removed} removed, {len(known)} in '{KB_NAME}'")


def main() -> None:
    log(f"docs-sync: {STORAGE_ACCOUNT}/{DOCS_CONTAINER} -> '{KB_NAME}', every {SYNC_INTERVAL} min")
    source = BlobSource()
    while True:
        try:
            sync(source, KnowledgeBase())
        except Exception as e:  # retried on the next run
            log(f"sync failed: {e}")
        time.sleep(SYNC_INTERVAL * 60)


if __name__ == "__main__":
    main()
