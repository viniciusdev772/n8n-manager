# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão Geral

Gerenciador de instâncias N8N multi-tenant em Python/FastAPI. Provisiona containers Docker com Traefik como reverse proxy (SSL via Let's Encrypt/Cloudflare DNS Challenge). Usado como backend SaaS para um frontend Next.js. Inclui também uma parser-api dedicada (PDF → JSON/CSV) em container separado.

## Comandos

```bash
# Setup
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Executar (dev)
python main.py

# Executar (produção via systemd)
systemctl start n8n-manager

# Setup completo de VPS (inclui Docker, firewall, systemd, swap)
curl -fsSL https://raw.githubusercontent.com/viniciusdev772/n8n-manager/main/setup.sh | bash

# Gerar/atualizar Traefik manualmente
python config_traefik.py
```

Não há testes automatizados nem linter configurados no projeto.

## Arquitetura

### Startup (lifespan)

`main.py` → `lifespan()` executa em ordem:
1. `setup_logging()` — configura logging global
2. `bootstrap_infra()` — provisiona rede, Traefik, Redis, RabbitMQ, parser-api, fallback, pre-pull imagem
3. `sync_instance_env_vars()` — recria containers N8N cujas env vars divergem do esperado
4. `start_worker()` — inicia thread consumer do RabbitMQ
5. `start_cleanup()` — inicia thread de limpeza automática

### Fluxo de criação de instância (async)

```
POST /enqueue-instance → RabbitMQ → Worker Thread → Docker SDK → Container
                                        ↓
Frontend polls GET /job/{id}/events  ← Redis (eventos de progresso)
```

Também existe `GET /create-instance-stream` que faz o mesmo mas via SSE (Server-Sent Events), e `POST /create-instance` para criação síncrona simples.

### Módulos (app/)

- **config.py** — Todas as variáveis de ambiente centralizadas (`.env` via python-dotenv). Único ponto de leitura do `.env`
- **routes.py** — Endpoints REST + SSE. Autenticação via `Depends(verify_token)`. Inclui rotas de debug e configuração do `.env`
- **n8n.py** — Criação/remoção/rebuild de containers N8N. Configura labels Traefik, volumes, env vars, limites de recursos. `create_container()` é a fonte única (DRY)
- **worker.py** — Consumer RabbitMQ em thread separada. Publica progresso no Redis. Health check via Traefik (HTTP ou HTTPS)
- **job_status.py** — CRUD de status de jobs no Redis. Connection pool com `max_connections=10`
- **queue.py** — Publisher RabbitMQ (singleton com `threading.Lock()` para thread safety)
- **infra.py** — Bootstrap de infraestrutura: rede Docker, Traefik, Redis, RabbitMQ, parser-api (via docker compose), fallback page, pre-pull da imagem N8N. Detecta Traefik existente (ex: EasyPanel)
- **cleanup.py** — Thread de limpeza automática: remove instâncias com idade >= `CLEANUP_MAX_AGE_DAYS`
- **auth.py** — Bearer token simples via header `Authorization`
- **docker_client.py** — Singleton do Docker client
- **logger.py** — `get_logger("modulo")` para logging centralizado

### Arquivos raiz

- **config_traefik.py** — Gera `traefik/docker-compose.yml` (HTTPS com Cloudflare ou HTTP-only) e sobe via `docker compose up -d`
- **parser.py** — Parser de PDF (Relatório de Saldo de Abastecimento fab0257). Também expõe FastAPI como parser-api dedicada
- **docker-compose.parser-api.yml** + **Dockerfile.parser-api** — Container da parser-api com rebuild automático por hash de arquivos-fonte

### Modo SSL vs HTTP

Determinado por `CF_DNS_API_TOKEN` em `.env`:
- **Com token**: HTTPS via Cloudflare DNS Challenge. Traefik redireciona HTTP→HTTPS. Worker faz health check na URL pública
- **Sem token**: HTTP-only (local/WSL/dev). Worker faz health check em `127.0.0.1` com header `Host`

### Recursos por instância

Definidos em `config.py`: 384MB RAM (hard limit), 192MB reservation, CPU shares 512. Máximo calculado dinamicamente: `(RAM total - 768MB reservados) / RAM por instância`.

## Convenções

- Código e comentários em português brasileiro
- Logging: `from .logger import get_logger` → `logger = get_logger("modulo")`
- Containers N8N seguem o padrão de nome `n8n-{nome}` com volume `n8n-data-{nome}`
- Rotas possuem aliases para compatibilidade (ex: `/restart-instance/{id}` e `/instance/{id}/restart`)
- Autenticação por Bearer token em todos endpoints exceto `/health`
- `.env` é a fonte de configuração; `config.py` é o único ponto de leitura
- Validação de input: `validate_instance_name()` (regex `^[a-z0-9][a-z0-9\-]{0,30}[a-z0-9]$`) e `validate_version()` (regex `^(latest|1\.\d{1,3}\.\d{1,3})$`)
- `rebuild_container()` preserva `N8N_ENCRYPTION_KEY` e `app.created_at` ao recriar

## Variáveis de Ambiente Importantes

Veja `.env.example`. As críticas são: `API_AUTH_TOKEN`, `BASE_DOMAIN`, `CF_DNS_API_TOKEN`, `RABBITMQ_HOST`, `REDIS_HOST`.
