# N8N Instance Manager

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-SDK-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)
[![License](https://img.shields.io/github/license/viniciusdev772/n8n-manager?style=for-the-badge)](LICENSE)

Gerenciador multi-tenant de instancias [N8N](https://n8n.io) com provisionamento automatico via Docker. Cada instancia recebe um subdominio dedicado com SSL automatico via Traefik + Cloudflare DNS Challenge. Projetado como backend SaaS para frontends como Next.js.

---

## Sumario

- [Sobre o Projeto](#sobre-o-projeto)
- [Arquitetura](#arquitetura)
- [Tecnologias](#tecnologias)
- [Pre-requisitos](#pre-requisitos)
- [Instalacao](#instalacao)
  - [Setup automatizado (VPS)](#setup-automatizado-vps)
  - [Setup manual (desenvolvimento)](#setup-manual-desenvolvimento)
- [Configuracao](#configuracao)
  - [Variaveis de Ambiente](#variaveis-de-ambiente)
  - [Painel Web](#painel-web)
- [Referencia da API](#referencia-da-api)
- [Frontend](#frontend)
- [Infraestrutura Docker](#infraestrutura-docker)
- [Limpeza Automatica](#limpeza-automatica)
- [Modo HTTP (Local/WSL)](#modo-http-localwsl)

---

## Sobre o Projeto

O N8N Manager resolve o problema de operar dezenas ou centenas de instancias N8N isoladas em uma unica VPS. Cada instancia e um container Docker independente com:

- Subdominio proprio (ex: `cliente1.n8n.seudominio.com`)
- Certificado SSL automatico via Let's Encrypt
- Limites de CPU e memoria configuráveis
- Volume persistente para dados e workflows
- Limpeza automatica de instancias antigas

A criacao de instancias e assincrona: o frontend enfileira um job via RabbitMQ, o worker processa em background, e o progresso e transmitido em tempo real via polling ou SSE.

---

## Arquitetura

```
                                  +-------------------+
                                  |   Frontend (SPA)  |
                                  |   ou Next.js App  |
                                  +--------+----------+
                                           |
                                    REST API / SSE
                                           |
+------------------------------------------v-------------------------------------------+
|                            FastAPI (main.py)                                          |
|                                                                                       |
|  POST /enqueue-instance -----> RabbitMQ -----> Worker Thread -----> Docker SDK         |
|                                                     |                                 |
|  GET /job/{id}/events <------- Redis <------------- +  (eventos de progresso)         |
|                                                                                       |
|  Cleanup Thread (1h) ---------> Remove instancias com 5+ dias                         |
+---------------------------------------------------------------------------------------+
         |                    |                    |                    |
    +---------+         +---------+          +---------+         +-----------+
    | Traefik |         |  Redis  |          |RabbitMQ |         | n8n-xxx   |
    | (proxy) |         | (jobs)  |          | (queue) |         | (instancia|
    +---------+         +---------+          +---------+         +-----------+
```

### Fluxo de Criacao de Instancia

1. Cliente faz `POST /enqueue-instance` com nome e versao
2. API publica job no RabbitMQ e retorna `job_id`
3. Worker consome o job, puxa a imagem Docker e cria o container
4. Worker faz health check ate N8N responder (max 3 min)
5. Progresso e publicado no Redis como eventos indexados
6. Cliente faz polling via `GET /job/{id}/events?since=0`

### Modulos

| Modulo | Responsabilidade |
|--------|-----------------|
| `main.py` | Entry point, lifespan, CORS, static files |
| `app/config.py` | Todas variaveis de ambiente centralizadas |
| `app/routes.py` | Endpoints REST, SSE, configuracao |
| `app/n8n.py` | CRUD de containers N8N, labels Traefik, env vars |
| `app/worker.py` | Consumer RabbitMQ em thread separada |
| `app/job_status.py` | CRUD de status de jobs no Redis |
| `app/queue.py` | Publisher RabbitMQ (singleton com lock) |
| `app/infra.py` | Bootstrap: rede, Traefik, Redis, RabbitMQ, fallback |
| `app/cleanup.py` | Thread de limpeza automatica |
| `app/auth.py` | Bearer token via header Authorization |
| `app/docker_client.py` | Singleton do Docker SDK client |
| `app/logger.py` | Configuracao centralizada de logging |
| `config_traefik.py` | Gera docker-compose do Traefik (HTTPS ou HTTP) |

---

## Tecnologias

| Componente | Tecnologia |
|-----------|-----------|
| API | FastAPI 0.115 + Uvicorn |
| Containers | Docker SDK for Python |
| Job Queue | RabbitMQ 3 (pika) |
| Event Store | Redis 7 |
| Reverse Proxy | Traefik v3 |
| SSL | Let's Encrypt via Cloudflare DNS Challenge |
| Frontend | HTML/CSS/JS vanilla (Terminal Luxe theme) |

---

## Pre-requisitos

- Linux (Ubuntu 20.04+, Debian 11+, CentOS 8+, Fedora, Rocky, Alma)
- Docker Engine 20+
- Python 3.10+
- Dominio com DNS apontando para o servidor (wildcard `*.n8n.seudominio.com`)
- Token Cloudflare com permissao `Zone > DNS > Edit` (opcional, para SSL)

---

## Instalacao

### Setup automatizado (VPS)

O script de setup configura tudo automaticamente: Docker, swap, firewall, Python, systemd e gera credenciais seguras.

```bash
curl -fsSL https://raw.githubusercontent.com/viniciusdev772/n8n-manager/main/setup.sh | sudo bash
```

O script realiza:

1. Detecta a distribuicao Linux e gerenciador de pacotes
2. Atualiza o sistema e instala dependencias
3. Configura swap automatico (se RAM < 4GB)
4. Instala e hardena o Docker (log rotation, ulimits, live-restore)
5. Instala Python 3 + venv
6. Configura firewall (UFW ou firewalld)
7. Aplica otimizacoes de kernel (rede, file descriptors)
8. Clona o repositorio em `/opt/n8n-manager`
9. Gera `.env` com tokens seguros e `BASE_DOMAIN=localhost`
10. Cria servico systemd (`n8n-manager`)
11. Provisiona Traefik na rede Docker

Apos a instalacao, acesse o painel web para configurar dominio e SSL:

```
http://SEU_IP:5050/ui/config.html
```

### Setup manual (desenvolvimento)

```bash
git clone https://github.com/viniciusdev772/n8n-manager.git
cd n8n-manager

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edite o .env com suas configuracoes

python main.py
```

---

## Configuracao

### Variaveis de Ambiente

Todas as variaveis sao lidas de `.env` via `app/config.py`. O arquivo `.env.example` contem todos os valores disponiveis.

#### Obrigatorias

| Variavel | Descricao | Exemplo |
|----------|-----------|---------|
| `API_AUTH_TOKEN` | Token Bearer para autenticacao da API | (gerado automaticamente) |
| `BASE_DOMAIN` | Dominio base dos subdominios N8N | `n8n.seudominio.com` |

#### SSL (opcionais)

| Variavel | Descricao | Default |
|----------|-----------|---------|
| `CF_DNS_API_TOKEN` | Token Cloudflare para DNS Challenge | (vazio = modo HTTP) |
| `ACME_EMAIL` | Email para certificados Let's Encrypt | (vazio) |

#### Infraestrutura

| Variavel | Descricao | Default |
|----------|-----------|---------|
| `SERVER_PORT` | Porta da API FastAPI | `5050` |
| `DOCKER_NETWORK` | Rede Docker compartilhada | `n8n-public` |
| `RABBITMQ_HOST` | Host do RabbitMQ | `127.0.0.1` |
| `RABBITMQ_PORT` | Porta AMQP | `5672` |
| `RABBITMQ_USER` | Usuario RabbitMQ | `n8n_manager` |
| `RABBITMQ_PASSWORD` | Senha RabbitMQ | (gerada automaticamente) |
| `REDIS_HOST` | Host do Redis | `127.0.0.1` |
| `REDIS_PORT` | Porta Redis | `6379` |

#### Instancias

| Variavel | Descricao | Default |
|----------|-----------|---------|
| `DEFAULT_N8N_VERSION` | Versao padrao ao criar instancias | `1.123.20` |
| `DEFAULT_TIMEZONE` | Timezone das instancias | `America/Sao_Paulo` |
| `INSTANCE_MEM_LIMIT` | Limite de RAM por instancia | `384m` |
| `INSTANCE_MEM_RESERVATION` | Reserva minima de RAM | `192m` |
| `INSTANCE_CPU_SHARES` | Peso relativo de CPU (128-4096) | `512` |

#### Cleanup

| Variavel | Descricao | Default |
|----------|-----------|---------|
| `CLEANUP_MAX_AGE_DAYS` | Dias ate remocao automatica | `5` |
| `CLEANUP_INTERVAL_SECONDS` | Intervalo de verificacao | `3600` |

#### Outros

| Variavel | Descricao | Default |
|----------|-----------|---------|
| `ALLOWED_ORIGINS` | CORS origins (separar por virgula) | `*` |
| `JOB_TTL` | TTL de jobs no Redis (segundos) | `600` |
| `JOB_CLEANUP_TTL` | TTL apos conclusao do job | `300` |

### Painel Web

O painel em `/ui/config.html` permite alterar todas as configuracoes sem SSH:

- Informacoes do sistema (RAM, swap, Docker, uptime, capacidade)
- Dominio e SSL com teste de token Cloudflare
- Recursos por instancia (memoria, CPU)
- Parametros de cleanup
- Regeneracao de API Token e senha RabbitMQ
- Restart do servico
- Banner de primeiro acesso quando `BASE_DOMAIN=localhost`

---

## Referencia da API

Todas as rotas (exceto `/health`) requerem o header `Authorization: Bearer SEU_TOKEN`.

### Saude e Informacoes

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `GET` | `/health` | Health check (sem auth) - status da API, Redis, Docker |
| `GET` | `/versions` | Lista versoes N8N disponiveis no Docker Hub |
| `GET` | `/capacity` | Capacidade da VPS (instancias ativas vs maximo) |
| `GET` | `/instances` | Lista todas as instancias N8N |

### Criacao de Instancias

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `POST` | `/enqueue-instance` | Enfileira criacao (retorna `job_id`) |
| `GET` | `/job/{job_id}/events?since=0` | Polling de eventos do job |
| `GET` | `/jobs` | Lista jobs ativos (pending/running) |
| `POST` | `/create-instance` | Criacao sincrona (resposta simples) |
| `GET` | `/create-instance-stream` | Criacao via SSE (progresso em tempo real) |

### Operacoes em Instancias

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `GET` | `/instance/{id}/status` | Status, memoria, uptime do container |
| `POST` | `/instance/{id}/restart` | Reinicia o container |
| `POST` | `/instance/{id}/reset` | Remove e recria com volume limpo |
| `POST` | `/instance/{id}/update-version` | Atualiza versao N8N (rebuild) |
| `DELETE` | `/delete-instance/{id}` | Remove instancia e volume |
| `GET` | `/instance/{id}/env` | Variaveis de ambiente (read-only) |
| `GET` | `/instance/{id}/logs?tail=50` | Logs do container |
| `GET` | `/instance/{id}/network` | Info de rede para debug |

### Cleanup

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `GET` | `/cleanup-preview` | Preview de instancias que serao removidas |

### Configuracao

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `GET` | `/config` | Retorna configuracao (segredos mascarados) |
| `PUT` | `/config` | Atualiza `.env` (detecta campos que requerem restart) |
| `POST` | `/config/test-cloudflare` | Valida token Cloudflare |
| `GET` | `/config/system-info` | RAM, swap, Docker, uptime, capacidade |
| `POST` | `/config/restart-service` | Reinicia o servico via systemctl |
| `POST` | `/config/regenerate-token` | Gera novo API_AUTH_TOKEN |
| `POST` | `/config/regenerate-rabbitmq-password` | Gera nova senha RabbitMQ |

### Debug

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `GET` | `/debug/container-env/{name}` | Env vars de qualquer container |
| `GET` | `/debug/container-logs/{name}` | Logs de qualquer container |
| `POST` | `/debug/start-container/{name}` | Tenta iniciar um container |
| `POST` | `/debug/fix-traefik-network` | Reconecta Traefik na rede correta |
| `POST` | `/debug/recreate-traefik` | Forca recriacao do Traefik |
| `GET` | `/debug/all-containers` | Lista todos os containers Docker |
| `GET` | `/debug/infra-networks` | Redes do Traefik, Redis, RabbitMQ |

---

## Frontend

O projeto inclui um frontend SPA em `/frontend/` servido em `/ui/`:

- **Dashboard** (`/ui/`) -- Visao geral de instancias, jobs ativos, capacidade
- **Configuracoes** (`/ui/config.html`) -- Painel completo de configuracao

O tema "Terminal Luxe" usa paleta escura com acentos em ciano/teal, fontes DM Sans e JetBrains Mono, e efeito grain sutil. Autenticacao via bearer token armazenado no localStorage.

---

## Infraestrutura Docker

Na startup, `bootstrap_infra()` garante que todos os componentes existam:

| Container | Imagem | Funcao | Limites |
|-----------|--------|--------|---------|
| `traefik` | `traefik:v3.6` | Reverse proxy + SSL | -- |
| `n8n-redis` | `redis:7-alpine` | Store de eventos de jobs | 128MB |
| `n8n-rabbitmq` | `rabbitmq:3-management-alpine` | Fila de criacao de instancias | 256MB |
| `n8n-fallback` | `nginx:alpine` | Pagina para subdominios deletados | -- |
| `n8n-{nome}` | `docker.n8n.io/n8nio/n8n` | Instancia N8N do cliente | 384MB |

Todos os containers estao na rede `n8n-public`. O Traefik descobre rotas automaticamente via labels Docker.

### Calculo de Capacidade

```
RAM reservada = 768MB (Traefik 50 + Redis 100 + RabbitMQ 150 + OS 200 + margem)
RAM por instancia = INSTANCE_MEM_LIMIT (default 384MB)
Max instancias = (RAM total - RAM reservada) / RAM por instancia
```

---

## Limpeza Automatica

Uma thread em background executa a cada `CLEANUP_INTERVAL_SECONDS` (default: 1h):

1. Lista todos os containers `n8n-*`
2. Verifica a idade pela label `app.created_at`
3. Remove instancias com idade >= `CLEANUP_MAX_AGE_DAYS` (default: 5 dias)
4. Remove tanto o container quanto o volume de dados

O endpoint `GET /cleanup-preview` mostra quais instancias serao removidas no proximo ciclo.

---

## Modo HTTP (Local/WSL)

Quando `CF_DNS_API_TOKEN` esta vazio, o sistema opera em modo HTTP:

- Traefik escuta apenas na porta 80 (sem SSL)
- O health check do worker usa `127.0.0.1` com header `Host` (em vez do dominio publico)
- Ideal para desenvolvimento local ou ambientes WSL

Para habilitar HTTPS depois, basta adicionar o token Cloudflare no painel web e reiniciar o servico.

---

## Estrutura do Projeto

```
n8n-manager/
  main.py                  # Entry point (FastAPI + lifespan)
  config_traefik.py        # Gerador de docker-compose do Traefik
  requirements.txt         # Dependencias Python
  setup.sh                 # Script de instalacao para VPS
  .env.example             # Template de variaveis de ambiente
  app/
    __init__.py
    auth.py                # Bearer token authentication
    cleanup.py             # Thread de limpeza automatica
    config.py              # Variaveis de ambiente centralizadas
    docker_client.py       # Singleton Docker SDK
    infra.py               # Bootstrap de infraestrutura
    job_status.py          # CRUD de jobs no Redis
    logger.py              # Configuracao de logging
    n8n.py                 # CRUD de containers N8N
    queue.py               # Publisher RabbitMQ
    routes.py              # Endpoints REST + SSE
    worker.py              # Consumer RabbitMQ
  frontend/
    index.html             # Dashboard SPA
    config.html            # Painel de configuracao
    app.js                 # Logica do dashboard
    config.js              # Logica do painel de config
    style.css              # Terminal Luxe theme
  fallback/
    index.html             # Pagina para instancias removidas
    nginx.conf             # Config nginx do fallback
```

---

## Licenca

Distribuido sob a licenca MIT. Veja `LICENSE` para mais informacoes.

---

## Contato

Vinicius - [@viniciusdev772](https://github.com/viniciusdev772)

Link do projeto: [https://github.com/viniciusdev772/n8n-manager](https://github.com/viniciusdev772/n8n-manager)
