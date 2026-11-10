# docs-sync: Azure Blob Storage -> Open WebUI knowledge base
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sync.py .

RUN useradd --create-home app && mkdir -p /data/state && chown app /data/state
USER app

ENTRYPOINT ["python", "sync.py"]
