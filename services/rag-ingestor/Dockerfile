###############################################################################
#  Dockerfile — rag-ingestor
#
#  Single responsibility: publish local documents from /data/docs to the
#  Open-WebUI knowledge base via its REST API.
###############################################################################

FROM python:3.12-slim

LABEL maintainer="azure-private-ai-cicd"
LABEL description="Ingests local documents into the Open-WebUI RAG pipeline"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/ingestor

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingest.py .

RUN mkdir -p /data/docs /data/state

RUN useradd --create-home --shell /bin/bash ingestuser \
    && chown -R ingestuser:ingestuser /data
USER ingestuser

ENTRYPOINT ["python", "ingest.py"]
