# 🤖 Azure Private AI Platform — Docker Stack & CI/CD

> **Portfolio project** · DevOps focus: **GitHub Actions CI/CD pipeline** with matrix builds,
> multi-environment deployments (Dev/Prod), self-hosted runners on Azure GPU VMs, and
> automated rollout of a full AI platform stack.
>
> The application itself is a **fully agnostic, reusable** private AI assistant
> (Ollama + Open-WebUI) with zero client data in the repo — everything is pulled
> dynamically from Azure Storage at runtime.

---

## 🔄 CI/CD Pipeline Highlights

```
feature/*  ──PR──►  dev  ──PR──►  main
                     │              │
              Build & Push      Build & Push
             (ubuntu-latest)   (ubuntu-latest)
              Matrix: 3 imgs    Matrix: 3 imgs
                     │              │
               Deploy DEV      Deploy PROD
            [self-hosted,     [self-hosted,
              dev-vm]           prod-vm]
             tag: dev-<sha>   tag: release-<sha>
```

### What the pipeline does

| Job | Trigger | Runner | Action |
|-----|---------|--------|--------|
| `build-and-push` | PR opened/updated → dev or main | `ubuntu-latest` | Builds 3 Docker images in parallel (matrix), pushes to ACR with immutable tags |
| `deploy-dev` | PR merged feature/* → dev | `self-hosted, dev-vm` | Generates `.env` from GitHub Secrets/Vars, deploys full stack via Docker Compose |
| `deploy-main` | PR merged dev → main | `self-hosted, prod-vm` | Same as dev but with `release-<sha>` tag, targets prod VM |

**Key CI/CD design decisions:**
- ✅ **Matrix builds** — 3 images built in parallel, fail-fast disabled
- ✅ **Immutable tags** — `dev-<7-char-sha>` / `release-<7-char-sha>`, never `latest`
- ✅ **Layer caching** — `type=registry` cache cuts build times significantly
- ✅ **Path filtering** — pipeline only triggers when `services/**` or `docker-compose.yml` changes
- ✅ **Zero secrets in code** — all credentials injected at deploy time from GitHub Environments
- ✅ **Self-hosted runners** — deploy jobs run directly on the Azure VM (no SSH needed)
- ✅ **Automatic image cleanup** — post-deploy step removes stale ACR images

---

## 📁 Repository Structure

```
azure-private-ai-cicd/
├── .github/
│   └── workflows/
│       └── build_and_push.yml     # 🚀 CI/CD: Build (matrix) → ACR → Deploy
│
├── services/                      # 🐍 Python microservices (one image each)
│   ├── storage-sync/              #    Azure Storage → shared volume
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── sync_storage.py
│   ├── webui-bootstrap/           #    One-time job: admin + KB + model setup
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── bootstrap.py
│   └── rag-ingestor/              #    Volume → Open-WebUI knowledge base
│       ├── Dockerfile
│       ├── requirements.txt
│       └── ingest.py
│
├── docker-compose.yml             # 🐳 8-service orchestrator
├── .env.example                   # 🔑 Environment variable template
├── .gitignore
└── README.md
```

---

## 🧩 Application Architecture — 8 Docker Services

Single responsibility per container: a RAG failure won't bring down the sync, and vice versa.

| Service           | Image                                         | Role                                                         |
|-------------------|-----------------------------------------------|--------------------------------------------------------------|
| `ollama-engine`   | `ollama/ollama:latest`                        | Inference engine with NVIDIA GPU                             |
| `ollama-puller`   | `ollama/ollama:latest`                        | One-time job: pulls base model on first boot (idempotent)    |
| `chromadb`        | `chromadb/chroma:0.5.23`                      | External vector store (survives UI restarts)                 |
| `tika`            | `apache/tika:2.9.2.1`                         | PDF/Office text extraction, off Open-WebUI's event loop      |
| `open-webui`      | `ghcr.io/open-webui/open-webui:main`          | Chat UI — serves the interface only                          |
| `webui-bootstrap` | `<ACR>/webui-bootstrap:<TAG>` (CI/CD)         | One-time job: creates admin, knowledge base and custom model |
| `storage-sync`    | `<ACR>/storage-sync:<TAG>` (CI/CD)            | Syncs Docs + Config from Azure Storage to shared volume      |
| `rag-ingestor`    | `<ACR>/rag-ingestor:<TAG>` (CI/CD)            | Uploads documents from volume to Open-WebUI knowledge base   |
| `cloud-tunnel`    | `cloudflare/cloudflared:latest`               | Encrypted Cloudflare Tunnel — no public IP required          |

> **Golden rule:** no service other than `open-webui` mounts or writes to `openwebui_data`.
> The SQLite database has a single owner.

---

## 🔄 Dynamic Data Flow

```
AZURE STORAGE ACCOUNT
├── Container: /documents  ──► (PDFs, Excel, CSVs, Word docs...)
└── Container: /config     ──► (logo.png, system_prompt.txt, report_template.md)
             │
             │  storage-sync (every N min, AZURE_STORAGE_CONNECTION_STRING)
             ▼
 AZURE VM (Docker Shared Volumes)
 ├── /data/docs/    (rag_docs)      ──► rag-ingestor uploads to Open-WebUI KB
 └── /data/config/  (client_config) ──► webui-bootstrap reads logo + system_prompt
             │
  ┌──────────▼──────────┐       ┌──────────────────┐     ┌──────────────┐
  │  webui-bootstrap    │──────►│   open-webui     │◄───►│  chromadb    │
  │  (admin + KB + model│       │   (Chat UI)      │     │  (vectors)   │
  └─────────────────────┘       └────────┬─────────┘     └──────────────┘
                                         │            ▲
                                rag-ingestor (API)    │ text extraction
                                         ▼            │
                                ┌──────────────┐  ┌──────┐
                                │ ollama-engine│  │ tika │
                                │  (NVIDIA GPU)│  └──────┘
                                └──────────────┘
                                         ▲
                              cloud-tunnel (Cloudflare)
                              exposes open-webui, no public IP
```

---

## 🚀 Quick Deploy (Manual)

### 1. Clone and configure

```bash
git clone https://github.com/<your-user>/azure-private-ai-cicd.git
cd azure-private-ai-cicd
cp .env.example .env
# ✏️ Edit .env with your credentials
```

### 2. Prepare Azure Storage

Create two containers in your Azure Storage Account:

| Container    | Contents                                               |
|--------------|--------------------------------------------------------|
| `documents`  | PDFs, Excel, invoices — everything the AI should know  |
| `config`     | `logo.png`, `system_prompt.txt`, `report_template.md`  |

### 3. Start

```bash
docker compose --env-file .env up -d
```

### 4. Verify

```bash
docker compose ps
docker compose logs -f storage-sync
docker compose logs -f webui-bootstrap
docker compose logs -f rag-ingestor
```

---

## ⚙️ GitHub Actions Setup

### Required GitHub Environments

Create two GitHub Environments (`Dev` and `Production`) with:

| Type     | Name                                | Description                           |
|----------|-------------------------------------|---------------------------------------|
| Variable | `ACR_LOGIN_SERVER`                  | `<acr-name>.azurecr.io`               |
| Variable | `ACR_USERNAME`                      | Service Principal Client ID           |
| Secret   | `ACR_PASSWORD`                      | Service Principal Client Secret       |
| Secret   | `AZURE_STORAGE_CONNECTION_STRING`   | Storage connection string             |
| Secret   | `CLOUDFLARE_TUNNEL_TOKEN`           | Cloudflare Tunnel token               |
| Secret   | `WEBUI_ADMIN_PASSWORD`              | Admin password                        |
| Secret   | `WEBUI_SECRET_KEY`                  | `openssl rand -hex 32`                |
| Variable | `WEBUI_ADMIN_EMAIL`                 | Admin email                           |
| Variable | `WEBUI_NAME`, `DEFAULT_MODELS`, ... | Optional customization (see `.env.example`) |

> The ACR credentials (`ACR_USERNAME` / `ACR_PASSWORD`) are automatically generated by the
> Terraform infrastructure repo as outputs of the GitHub Actions Service Principal.
> See [`terraform-azure-private-ai`](https://github.com/<your-user>/terraform-azure-private-ai).

### Self-hosted runners

The deploy jobs require self-hosted runners registered on the Azure VMs:

```bash
# On the DEV VM:
# Register a runner with labels: self-hosted, dev-vm

# On the PROD VM:
# Register a runner with labels: self-hosted, prod-vm
```

[GitHub Docs: Adding self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/adding-self-hosted-runners)

---

## 🔐 Security

| Aspect                   | Implementation                                          |
|--------------------------|---------------------------------------------------------|
| No public IP on VM       | Isolated VM, access only via Cloudflare Tunnel / Bastion |
| Credentials              | `.env` in `.gitignore`, secrets via GitHub Environments |
| ACR                      | Managed Identity (`AcrPull`), admin disabled            |
| Storage Account          | Private Endpoint + RBAC `Storage Blob Data Reader`      |
| Custom services          | Each talks to Open-WebUI via API, no direct SQLite access|
| Repo                     | Zero client data in code                                |

---

## 📋 New Client Onboarding (2 minutes)

1. **Infra**: Deploy with [`terraform-azure-private-ai`](https://github.com/<your-user>/terraform-azure-private-ai) to get ACR, VM, Storage.
2. **GitHub Environment**: Create `Dev`/`Production` with the variables/secrets above.
3. **Azure Storage**: Create `documents` and `config` containers.
4. **Upload config**: Upload `logo.png` and `system_prompt.txt` to `config`.
5. **Upload docs**: Upload PDFs/Excel to `documents`.
6. **Merge to dev**: Pipeline builds 3 images and deploys the full stack automatically.

> **Self-service:** If the client uploads a new PDF, `storage-sync` and `rag-ingestor` pick it
> up automatically within the configured interval (default: 15 minutes). No manual action needed.

---

## 📄 License

MIT — Free to use as a portfolio reference or starting point for your own projects.