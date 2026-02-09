# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

N8N Manager is a **FastAPI-based Python backend** that provisions and manages isolated N8N workflow automation instances on a VPS. Each instance runs as a Docker container with its own subdomain, SSL, and resource limits. The system is a multi-tenant SaaS platform — not a single N8N deployment.

**Primary language:** Portuguese (Brazilian) — code comments, API messages, and documentation are in pt-BR.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server (starts on 0.0.0.0:$SERVER_PORT, default 5050)
python main.py

# Full VPS setup (installs Docker, pulls repo, configures system)
sudo bash setup.sh
```

There are **no tests, linters, or formatters** configured in this project.

## Architecture

### Startup Lifecycle (`main.py`)

The FastAPI lifespan handler runs this sequence on startup:
1. `bootstrap_infra()` — ensures Docker network, Traefik, Redis, RabbitMQ, and fallback containers exist
2. `sync_instance_env_vars()` — updates all running N8N containers with current config
3. `start_worker()` — launches RabbitMQ consumer thread for async instance creation
4. `start_cleanup()` — launches hourly thread that deletes instances older than 5 days

### Request Flow — Two Creation Paths

**Synchronous:** `POST /create-instance` → directly calls `create_container()` → returns result.

**Asynchronous (preferred):**
```
POST /enqueue-instance or GET /create-instance-stream
  → publish_job() to RabbitMQ
  → Worker thread consumes job, creates container, updates Redis with progress events
  → Client polls via GET /job/{id}/events or receives SSE stream
```

### Key Modules (`app/`)

| Module | Responsibility |
|--------|---------------|
| `routes.py` | All API endpoints (REST + SSE), request validation |
| `n8n.py` | Container creation, removal, rebuild; resource limits; encryption key generation; capacity calculation |
| `worker.py` | Background RabbitMQ consumer — creates containers and pushes status events to Redis |
| `infra.py` | Infrastructure bootstrap — Docker network, Traefik (via `config_traefik.py`), Redis, RabbitMQ, fallback page |
| `job_status.py` | Redis-backed job state machine and event stream |
| `queue.py` | RabbitMQ publisher (pika) |
| `auth.py` | Bearer token authentication middleware |
| `config.py` | Centralized env-var loading from `.env` |
| `cleanup.py` | Auto-cleanup thread (hourly, deletes instances >5 days old) |
| `docker_client.py` | Docker client singleton |

### Infrastructure Stack (all managed as Docker containers)

- **Traefik v3.6** — reverse proxy with automatic Let's Encrypt SSL via Cloudflare DNS Challenge
- **Redis** — job status tracking and event streaming
- **RabbitMQ** — async job queue for instance creation
- **Nginx fallback** — catch-all page for deleted/expired subdomains

### Resource Model

Each N8N instance runs with: 384MB hard memory limit, 192MB soft reservation, 512 CPU shares (relative weight). Capacity is calculated as `(Total_RAM - 768MB_reserved) / 384MB_per_instance`.

### Networking

All containers (Traefik, N8N instances, Redis, RabbitMQ) share the `n8n-public` Docker bridge network. Traefik routes traffic to instances via container labels. Each instance gets a subdomain: `{name}.{BASE_DOMAIN}`.

## Configuration

All runtime config is loaded from `.env` via `app/config.py`. See `.env.example` for the template. Key variables: `API_AUTH_TOKEN`, `BASE_DOMAIN`, `ACME_EMAIL`, `CF_DNS_API_TOKEN`, `SERVER_PORT`.
