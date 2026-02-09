# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Visão Geral

Gerenciador de instâncias N8N multi-tenant em Python/FastAPI. Provisiona containers Docker com Traefik como reverse proxy (SSL via Let's Encrypt/Cloudflare DNS Challenge). Usado como backend SaaS para um frontend Next.js.

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
```

Não há testes automatizados nem linter configurados no projeto.

## Arquitetura

### Fluxo de criação de instância (async)

```
POST /enqueue-instance → RabbitMQ → Worker Thread → Docker SDK → Container
                                        ↓
Frontend polls GET /job/{id}/events  ← Redis (eventos de progresso)
```

Também existe `GET /create-instance-stream` que faz o mesmo mas via SSE (Server-Sent Events).

### Módulos (app/)

- **config.py** — Todas as variáveis de ambiente centralizadas (`.env` via python-dotenv)
- **routes.py** — Endpoints REST + SSE. Autenticação via `Depends(verify_token)`
- **n8n.py** — Criação/remoção/rebuild de containers N8N. Configura labels Traefik, volumes, env vars, limites de recursos
- **worker.py** — Consumer RabbitMQ em thread separada. Publica progresso no Redis
- **job_status.py** — CRUD de status de jobs no Redis (init/update/get_events/cleanup)
- **queue.py** — Publisher RabbitMQ (singleton connection)
- **infra.py** — Bootstrap de infraestrutura: rede Docker, Traefik, Redis, RabbitMQ, fallback page, pre-pull da imagem N8N
- **cleanup.py** — Thread de limpeza automática: remove instâncias com 5+ dias a cada 1h
- **auth.py** — Bearer token simples via header `Authorization`
- **docker_client.py** — Singleton do Docker client

### Infraestrutura Docker

Na startup (`main.py` lifespan), `bootstrap_infra()` garante que existam:
- Rede `n8n-public`
- Traefik v3 (detecta Traefik existente, ex: EasyPanel)
- Redis e RabbitMQ (containers locais)
- Container fallback (nginx com página de erro para instâncias deletadas)
- Pre-pull da imagem N8N

### Recursos por instância

Definidos em `config.py`: 384MB RAM (hard limit), 192MB reservation, CPU shares 512. Máximo calculado dinamicamente baseado na RAM da VPS.

## Convenções

- Código e comentários em português brasileiro
- Containers N8N seguem o padrão de nome `n8n-{nome}` com volume `n8n-data-{nome}`
- Rotas possuem aliases para compatibilidade (ex: `/restart-instance/{id}` e `/instance/{id}/restart`)
- Autenticação por Bearer token em todos endpoints exceto `/health`
- `.env` é a fonte de configuração; `config.py` é o único ponto de leitura

## Variáveis de Ambiente Importantes

Veja `.env.example`. As críticas são: `API_AUTH_TOKEN`, `BASE_DOMAIN`, `CF_DNS_API_TOKEN`, `RABBITMQ_HOST`, `REDIS_HOST`.
