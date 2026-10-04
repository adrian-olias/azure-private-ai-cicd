###############################################################################
#  Dockerfile — webui-bootstrap
#
#  One-time job: creates the admin user, knowledge base, and custom model
#  via REST API. Exits with code 0 when done.
###############################################################################

FROM python:3.12-slim

LABEL maintainer="azure-private-ai-cicd"
LABEL description="Idempotent Open-WebUI initialization (admin + knowledge base + custom model)"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/bootstrap

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bootstrap.py .

RUN useradd --create-home --shell /bin/bash bootstrapuser
USER bootstrapuser

ENTRYPOINT ["python", "bootstrap.py"]
