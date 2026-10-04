#!/usr/bin/env python3
"""
bootstrap.py — Idempotent Open-WebUI Initialization

Runs ONCE when the stack starts, then exits (exit 0). Leaves the system in a
known state so that `rag-ingestor` can do its job:

  1. Waits for the Open-WebUI API to be ready.
  2. Creates the admin account if the instance is fresh.
     Open-WebUI automatically promotes the FIRST registered user to 'admin'
     (see backend/open_webui/routers/auths.py::signup_handler), so admin
     creation requires a direct call to /api/v1/auths/signup.
  3. Creates the knowledge base if it does not exist.
  4. Creates or updates the custom model and links it to the knowledge base.

Everything via REST API. Zero direct SQLite access:
the database belongs exclusively to the Open-WebUI process.
"""

import os
import sys
import time
from pathlib import Path

import requests

WEBUI_URL = os.environ.get("OPEN_WEBUI_URL", "http://open-webui:8080").strip().rstrip("/")
ADMIN_EMAIL = os.environ["WEBUI_ADMIN_EMAIL"].strip()
ADMIN_PASSWORD = os.environ["WEBUI_ADMIN_PASSWORD"]
ADMIN_NAME = os.environ.get("WEBUI_ADMIN_NAME", "Administrator").strip()

KB_NAME = os.environ.get("KNOWLEDGE_BASE_NAME", "Company Documents").strip()
MODEL_NAME = os.environ.get("CUSTOM_MODEL_NAME", "AI Assistant").strip()
MODEL_ID = os.environ.get("CUSTOM_MODEL_ID", "ai-assistant").strip()
BASE_MODEL_ID = os.environ.get("BASE_MODEL_ID", "llama3.1:latest").strip()

CONFIG_PATH = Path(os.environ.get("CONFIG_DOWNLOAD_PATH", "/data/config").strip())

WAIT_TIMEOUT = int(os.environ.get("BOOTSTRAP_WAIT_TIMEOUT", "300"))

DEFAULT_SYSTEM_PROMPT = (
    "You are a private corporate AI assistant running on the company's local infrastructure. "
    "You have access to internal documentation through your knowledge base. "
    "Always respond professionally, clearly, and concisely. "
    "When the question relates to content covered in the documents, rely strictly on them "
    "and cite the source document. If the information is not in the documents, say so clearly "
    "rather than making it up."
)


def log(msg: str) -> None:
    print(msg, flush=True)


def wait_for_api() -> None:
    """Blocks until Open-WebUI serves requests or the timeout is reached."""
    log(f"   ⏳ Waiting for Open-WebUI at {WEBUI_URL} ...")
    deadline = time.time() + WAIT_TIMEOUT

    while time.time() < deadline:
        try:
            r = requests.get(f"{WEBUI_URL}/health", timeout=5)
            if r.status_code == 200:
                log("   ✅ Open-WebUI is ready.")
                return
        except requests.RequestException:
            pass
        time.sleep(5)

    log(f"   ❌ Open-WebUI did not respond within {WAIT_TIMEOUT}s.")
    sys.exit(1)


def get_admin_token() -> str:
    """
    Returns an admin session token.

    Tries to sign in first; if credentials don't exist yet, registers the account.
    On a fresh instance the first registration gets the 'admin' role.
    """
    r = requests.post(
        f"{WEBUI_URL}/api/v1/auths/signin",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    if r.status_code == 200 and r.json().get("token"):
        log("   ✅ Admin session started.")
        return r.json()["token"]

    log("   🆕 Account not found. Registering initial admin...")
    r = requests.post(
        f"{WEBUI_URL}/api/v1/auths/signup",
        json={"name": ADMIN_NAME, "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    if r.status_code == 200 and r.json().get("token"):
        payload = r.json()
        if payload.get("role") != "admin":
            log(
                "   ⚠️  Account created with role "
                f"'{payload.get('role')}'. The instance already had users; "
                "promote this user to admin manually."
            )
        else:
            log("   ✅ Admin account created.")
        return payload["token"]

    log(f"   ❌ Could not authenticate or register: {r.status_code} — {r.text}")
    sys.exit(1)


def ensure_knowledge_base(headers: dict) -> str:
    """Returns the knowledge base UUID, creating it if necessary."""
    r = requests.get(f"{WEBUI_URL}/api/v1/knowledge/", headers=headers, timeout=15)
    if r.status_code == 200:
        payload = r.json()
        items = payload if isinstance(payload, list) else payload.get("items", [])
        for kb in items:
            if isinstance(kb, dict) and kb.get("name") == KB_NAME:
                log(f"   ℹ️  Existing knowledge base: {kb['id']}")
                return kb["id"]

    log(f"   🆕 Creating knowledge base '{KB_NAME}'...")
    r = requests.post(
        f"{WEBUI_URL}/api/v1/knowledge/create",
        headers=headers,
        json={
            "name": KB_NAME,
            "description": "Corporate documentation synchronized automatically from Azure Storage.",
            "data": {},
            "access_control": None,  # None = visible to all users
        },
        timeout=15,
    )
    if r.status_code != 200:
        log(f"   ❌ Error creating knowledge base: {r.status_code} — {r.text}")
        sys.exit(1)

    kb_id = r.json()["id"]
    log(f"   ✅ Knowledge base created: {kb_id}")
    return kb_id


def load_system_prompt() -> str:
    """Reads system_prompt.txt from the config volume if the client has uploaded one."""
    prompt_file = CONFIG_PATH / "system_prompt.txt"
    if prompt_file.exists():
        try:
            content = prompt_file.read_text(encoding="utf-8").strip()
            if content:
                log("   📝 Custom system prompt loaded from system_prompt.txt.")
                return content
        except OSError as e:
            log(f"   ⚠️  Could not read system_prompt.txt: {e}")
    return DEFAULT_SYSTEM_PROMPT


def ensure_model(headers: dict, kb_id: str) -> None:
    """Creates or updates the custom model linked to the knowledge base."""
    payload = {
        "id": MODEL_ID,
        "name": MODEL_NAME,
        "base_model_id": BASE_MODEL_ID,
        "meta": {
            "description": "Private AI assistant with access to internal documentation.",
            "profile_image_url": "/static/favicon.png",
            "capabilities": {"vision": False, "citations": True},
            "knowledge": [{"id": kb_id, "type": "collection", "name": KB_NAME}],
        },
        "params": {"system": load_system_prompt()},
        "access_control": None,  # None = visible to all users
        "is_active": True,
    }

    r = requests.get(
        f"{WEBUI_URL}/api/v1/models/model",
        headers=headers,
        params={"id": MODEL_ID},
        timeout=15,
    )
    exists = r.status_code == 200 and r.json()

    endpoint = "update" if exists else "create"
    verb = "Updating" if exists else "Creating"
    log(f"   🤖 {verb} model '{MODEL_NAME}' ({MODEL_ID})...")

    r = requests.post(
        f"{WEBUI_URL}/api/v1/models/{endpoint}",
        headers=headers,
        params={"id": MODEL_ID} if exists else None,
        json=payload,
        timeout=20,
    )
    if r.status_code != 200:
        log(f"   ❌ Error configuring model: {r.status_code} — {r.text}")
        sys.exit(1)

    log(f"   ✅ Model ready and linked to knowledge base {kb_id}.")


def main() -> None:
    log("🚀 WebUI Bootstrap — Idempotent initialization")
    log(f"   🌐 Target: {WEBUI_URL}")
    log("")

    wait_for_api()
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    kb_id = ensure_knowledge_base(headers)
    ensure_model(headers, kb_id)

    log("")
    log("🏁 Bootstrap complete. Service exiting normally.")


if __name__ == "__main__":
    main()
