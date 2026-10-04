###############################################################################
#  Dockerfile — storage-sync
#
#  Single responsibility: download blobs from Azure Storage to local volumes.
#  Does NOT talk to Open-WebUI or its database.
###############################################################################

FROM python:3.12-slim

# ─── Metadata ─────────────────────────────────────────────────────────────────
LABEL maintainer="azure-private-ai-cicd"
LABEL description="Downloads documents and config from Azure Blob Storage to shared volumes"

# ─── Runtime environment ──────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ─── Working directory ────────────────────────────────────────────────────────
WORKDIR /opt/sync

# ─── Install dependencies ─────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Copy script ──────────────────────────────────────────────────────────────
COPY sync_storage.py .

# ─── Create data directories ──────────────────────────────────────────────────
RUN mkdir -p /data/docs /data/config /data/state

# ─── Non-root user ────────────────────────────────────────────────────────────
RUN useradd --create-home --shell /bin/bash syncuser \
    && chown -R syncuser:syncuser /data
USER syncuser

# ─── Entrypoint ───────────────────────────────────────────────────────────────
ENTRYPOINT ["python", "sync_storage.py"]
