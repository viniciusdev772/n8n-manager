# DOCS - Python Codebase Reference

Data da analise: 2026-03-01
Escopo: documentacao externa gerada em modo somente leitura dos arquivos Python existentes.

## 1) Overview da Arquitetura

O projeto implementa dois backends Python no mesmo repositorio: (1) o **N8N Instance Manager** (FastAPI em `main.py` + modulos `app/`) e (2) uma **Parser API** dedicada (`parser.py`) para extracao de dados de PDFs e geracao de artefatos JSON/CSV/HTML. O manager e orientado a orquestracao de infraestrutura Docker (Traefik, Redis, RabbitMQ, containers n8n), enquanto a parser API e orientada a processamento de dados e distribuicao de arquivos gerados.

No manager, o design principal combina API stateless + estado operacional externo: RabbitMQ para fila de jobs, Redis para estado/eventos de job, Docker como runtime de instancias, e Traefik como roteador reverso de subdominios. A criacao de instancia e assincrona: endpoint enfileira job, worker processa em thread separada, estado e publicado no Redis, e o cliente acompanha por polling/SSE. O ciclo de vida da aplicacao usa `lifespan` para bootstrap de infraestrutura e inicializacao/parada controlada de worker/cleanup.

No parser, o design mistura pipeline procedural de extracao (`pdfplumber` + heuristicas por coluna) com uma camada API FastAPI opcional (ativada quando dependencia existe). A API salva outputs em diretorio persistente e expone listagem/download por endpoints. Em paralelo, o manager sobe essa parser API em container dedicado via `docker-compose.parser-api.yml`, detectando alteracoes de fonte por hash para controlar rebuild.

## 2) Referencia por Modulo

### 2.1 Modulos Python na raiz

| Arquivo | Proposito | Classes/Funcoes Principais |
|---|---|---|
| `main.py` | Entrypoint FastAPI do manager, lifecycle e montagem de rotas/UI | `lifespan`, `app` |
| `config_traefik.py` | Script de provisionamento Traefik (gera compose e sobe container) | Script procedural (sem classes/funcoes publicas) |
| `parser.py` | Parser de PDF + API FastAPI de parse/listagem/download | `parse_pdf`, `save_json/csv/common_*`, modelos `BaseModel`, endpoint `parse` |

### 2.2 Modulos Python em `app/`

| Arquivo | Proposito | Classes/Funcoes Principais |
|---|---|---|
| `app/config.py` | Config central via `.env` | Constantes de runtime |
| `app/logger.py` | Setup de logging padrao | `setup_logging`, `get_logger` |
| `app/docker_client.py` | Singleton do Docker SDK client | `get_client`, `close_client` |
| `app/auth.py` | Autenticacao Bearer de API | `verify_token` |
| `app/job_status.py` | Estado/eventos de jobs em Redis | `init_job`, `set_state`, `get_state`, `push_event`, `get_events_since`, `cleanup_job` |
| `app/queue.py` | Publicacao de jobs em RabbitMQ | `get_channel`, `publish_job`, `close_rabbitmq` |
| `app/n8n.py` | Regras de negocio de instancias n8n e capacidade | `create_container`, `list_n8n_containers`, `rebuild_container`, `calculate_max_instances` |
| `app/infra.py` | Bootstrap de network/Traefik/Redis/RabbitMQ/parser-api/fallback | `ensure_*`, `bootstrap_infra` |
| `app/worker.py` | Consumidor de fila para criar instancia assincronamente | `start_worker`, `stop_worker` |
| `app/cleanup.py` | Rotina periodica de remocao de instancias antigas | `start_cleanup`, `stop_cleanup` |
| `app/routes.py` | Superficie HTTP principal do manager | 33 handlers FastAPI (`/health`, `/instances`, `/enqueue-instance`, `/config`, etc.) |

## 3) Detalhamento dos Modulos Principais

## 3.1 `main.py`

Descricao do modulo:
Inicializa o app FastAPI do manager, aplica CORS, registra `router`, monta SPA em `/ui`, e controla inicializacao/encerramento de infra + threads em `lifespan`.

Funcoes publicas:
- `lifespan(app: FastAPI)`
  - Parametros: `app` (instancia FastAPI).
  - Retorno: context manager assincrono.
  - Excecoes: propaga erros de bootstrap/threads/infra.
  - Responsabilidade: startup (`setup_logging`, `bootstrap_infra`, `sync_instance_env_vars`, `start_worker`, `start_cleanup`) e shutdown (`stop_cleanup`, `stop_worker`, `close_rabbitmq`, `close_client`).

Dependencias:
- Internas: `app.cleanup`, `app.config`, `app.logger`, `app.docker_client`, `app.infra`, `app.n8n`, `app.queue`, `app.routes`, `app.worker`.
- Externas: `fastapi`, `uvicorn`.

Exemplo de uso:
```bash
python main.py
```

## 3.2 `app/config.py`

Descricao do modulo:
Carrega `.env` via `python-dotenv` e expoe constantes de configuracao usadas por todo o manager.

Constantes-chave:
- API: `API_AUTH_TOKEN`, `SERVER_PORT`, `ALLOWED_ORIGINS`.
- Dominio/SSL: `BASE_DOMAIN`, `ACME_EMAIL`, `CF_DNS_API_TOKEN`, `SSL_ENABLED`, `PROTOCOL`, `TRAEFIK_CERT_RESOLVER`.
- Infra: `DOCKER_NETWORK`, `RABBITMQ_*`, `REDIS_*`.
- n8n: `N8N_IMAGE`, `DEFAULT_N8N_VERSION`, `INSTANCE_MEM_LIMIT`, `INSTANCE_MEM_RESERVATION`, `INSTANCE_CPU_SHARES`, `DEFAULT_TIMEZONE`.
- Worker/Cleanup/SSE: `READINESS_MAX_ATTEMPTS`, `READINESS_POLL_INTERVAL`, `SSL_WAIT_SECONDS`, `CLEANUP_MAX_AGE_DAYS`, `CLEANUP_INTERVAL_SECONDS`, `JOB_TTL`, `JOB_CLEANUP_TTL`, `SSE_MAX_DURATION`.

Dependencias:
- Externas: `python-dotenv`.

Exemplo de uso:
```python
from app.config import SERVER_PORT, BASE_DOMAIN
```

## 3.3 `app/logger.py`

Descricao do modulo:
Centraliza configuracao de logging e factory de logger por nome.

Funcoes publicas:
- `setup_logging()`
  - Retorno: `None`.
  - Efeito: configura `logging.basicConfig` em `INFO` com stream stdout.
- `get_logger(name: str) -> logging.Logger`
  - Parametros: nome do logger.
  - Retorno: instancia `logging.Logger`.

Exemplo de uso:
```python
from app.logger import get_logger
logger = get_logger("infra")
```

## 3.4 `app/docker_client.py`

Descricao do modulo:
Implementa singleton de `docker.DockerClient` para reutilizacao de conexoes.

Funcoes publicas:
- `get_client() -> docker.DockerClient`
  - Retorno: cliente Docker inicializado sob demanda.
  - Excecoes: propagadas do `docker.from_env()`.
- `close_client()`
  - Retorno: `None`.
  - Efeito: fecha cliente e reseta singleton.

Padrao:
- Singleton de infraestrutura.

Exemplo de uso:
```python
from app.docker_client import get_client
client = get_client()
```

## 3.5 `app/auth.py`

Descricao do modulo:
Autenticacao por bearer token para endpoints protegidos.

Funcoes publicas:
- `verify_token(credentials: HTTPAuthorizationCredentials = Security(_bearer))`
  - Parametros: credenciais bearer injetadas pelo FastAPI.
  - Retorno: token validado.
  - Excecoes: `HTTPException(500)` se token server nao configurado; `HTTPException(403)` para token invalido.

Exemplo de uso:
```python
@router.get("/instances", dependencies=[Depends(verify_token)])
async def list_instances():
    ...
```

## 3.6 `app/job_status.py`

Descricao do modulo:
Store de estado de job e stream de eventos no Redis (bridge entre API e worker).

Funcoes publicas:
- `get_redis() -> redis.Redis`
  - Retorno: cliente Redis via connection pool singleton.
  - Excecoes: conexao Redis se indisponivel.
- `init_job(job_id: str)`
  - Efeito: cria `job:{id}:state = pending` com TTL.
- `set_state(job_id: str, state: str)`
  - Efeito: atualiza estado (`pending/running/complete/error`).
- `get_state(job_id: str) -> str`
  - Retorno: estado atual ou `unknown`.
- `push_event(job_id: str, event: dict)`
  - Efeito: append JSON em lista de eventos + TTL.
- `get_events_since(job_id: str, index: int) -> list[dict]`
  - Retorno: eventos incrementais para polling/SSE.
- `cleanup_job(job_id: str)`
  - Efeito: encurta TTL de chaves apos termino.

Padrao:
- Event log append-only por job (lista Redis).

Exemplo de uso:
```python
init_job(job_id)
push_event(job_id, {"status": "info", "message": "iniciando"})
```

## 3.7 `app/queue.py`

Descricao do modulo:
Publisher RabbitMQ para jobs de criacao de instancia.

Funcoes publicas:
- `get_channel()`
  - Retorno: canal RabbitMQ ativo.
  - Excecoes: `pika` em falha de conexao/autenticacao.
  - Efeito: reconecta sob lock e declara fila duravel `instance_creation`.
- `publish_job(job_id: str, payload: dict)`
  - Efeito: publica mensagem persistente (`delivery_mode=2`).
- `close_rabbitmq()`
  - Efeito: fecha conexao singleton com lock.

Padroes:
- Singleton + lock para conexao/canal.

Exemplo de uso:
```python
publish_job(job_id, {"job_id": job_id, "name": "cliente-a", "version": "1.123.20"})
```

## 3.8 `app/n8n.py`

Descricao do modulo:
Regras de dominio para instancias n8n: validacao, env vars, labels Traefik, create/remove/list/rebuild/sync e calculo de capacidade por RAM.

Funcoes publicas:
- `validate_instance_name(name: str) -> str`
  - Valida regex `[a-z0-9-]` (2-32 chars efetivos).
  - Excecoes: `ValueError`.
- `validate_version(version: str) -> str`
  - Aceita `latest` ou `1.X.Y`.
  - Excecoes: `ValueError`.
- `container_name(instance_name: str) -> str`
- `instance_url(instance_name: str) -> str`
- `generate_encryption_key() -> str`
- `build_env(name: str, encryption_key: str) -> dict`
  - Monta env completa da instancia (URL, timezone, seguranca, execucao, performance).
- `build_traefik_labels(name: str) -> dict`
  - Monta labels de roteamento (HTTP ou HTTPS).
- `create_container(name: str, version: str, encryption_key: str, created_at: str | None = None)`
  - Pull de imagem + `containers.run` com limites de recursos e volume `n8n-data-{name}`.
  - Excecoes: Docker SDK.
- `get_container(name: str)`
  - Excecoes: `docker.errors.NotFound`.
- `remove_container(name: str)`
  - Remove container + tenta remover volume dedicado.
- `list_n8n_containers() -> list`
  - Retorna metadata consolidada (status, URL, versao, created_at, age_days).
- `extract_encryption_key(container) -> str`
- `rebuild_container(instance_id: str, version: str)`
  - Recria mantendo encryption key e volume.
  - Excecoes: `RuntimeError` se chave ausente; Docker errors.
- `sync_instance_env_vars()`
  - Compara env esperado vs atual e recria containers divergentes.
- `calculate_max_instances() -> dict`
  - Capacidade por RAM (`max_instances`, `active_instances`, `can_create`, metrica VPS).

Fluxo de dados (modulo):
input request -> validacao nome/versao -> composicao env/labels -> Docker run -> metadata para API.

Exemplo de uso:
```python
name = validate_instance_name("cliente-a")
version = validate_version("1.123.20")
create_container(name, version, generate_encryption_key())
```

## 3.9 `app/infra.py`

Descricao do modulo:
Provisionamento da infraestrutura compartilhada no startup: rede Docker, Traefik, Redis, RabbitMQ, parser-api dedicada, fallback nginx e pre-pull da imagem n8n.

Funcoes publicas:
- `ensure_network()`
  - Cria rede compartilhada se nao existir.
- `ensure_traefik()`
  - Reutiliza Traefik existente ou recria via `config_traefik.py`.
- `ensure_redis()`
  - Garante container Redis com porta exposta e limite de memoria.
- `ensure_rabbitmq()`
  - Garante RabbitMQ com credenciais do `.env` e valida autenticacao.
- `ensure_fallback()`
  - Garante container nginx fallback com rota catch-all no Traefik.
- `bootstrap_infra()`
  - Orquestra sequencia: network -> traefik -> redis -> rabbitmq -> parser-api -> fallback -> pre-pull n8n.

Funcoes internas relevantes:
- `_run_config_traefik()`: executa script root para compose do Traefik.
- `_run_parser_api_compose()`: build/up da parser-api com hash de fontes para rastrear mudancas.

Excecoes:
- Cada etapa captura erro e segue (`bootstrap_infra` fail-soft por componente).

Padroes:
- Bootstrap pipeline resiliente.
- Integridade de deploy via hash de arquivos na parser-api.

Exemplo de uso:
```python
from app.infra import bootstrap_infra
bootstrap_infra()
```

## 3.10 `app/worker.py`

Descricao do modulo:
Worker em thread daemon que consome fila RabbitMQ e executa criacao de instancia com health-check e eventos progressivos.

Funcoes publicas:
- `start_worker() -> threading.Thread`
  - Inicia loop de consumo em background.
- `stop_worker()`
  - Sinaliza parada via `Event`.

Fluxo interno (`_process_job`):
1. parse do job JSON;
2. estado `running` no Redis;
3. valida duplicata;
4. cria container;
5. polling de readiness (HTTP via Traefik);
6. publica `complete` ou `error`;
7. `ack` da mensagem.

Excecoes tratadas:
- Docker `NotFound` (controle de duplicata), falhas de criacao/readiness, falha de conexao RabbitMQ com reconexao.

Exemplo de uso:
```python
t = start_worker()
# ...
stop_worker()
```

## 3.11 `app/cleanup.py`

Descricao do modulo:
Thread periodica que remove instancias acima da idade configurada (`CLEANUP_MAX_AGE_DAYS`).

Funcoes publicas:
- `start_cleanup() -> threading.Thread`
- `stop_cleanup()`

Fluxo:
lista containers -> calcula `age_days` -> remove expirados -> loga resultado -> aguarda intervalo.

Excecoes:
- Erros de remocao sao logados por instancia; loop continua.

Exemplo de uso:
```python
start_cleanup()
```

## 3.12 `app/routes.py`

Descricao do modulo:
Superficie REST/SSE do manager. Contem endpoints de status, filas/jobs, CRUD de instancias, debug de infraestrutura e administracao de configuracao `.env`.

Dependencias internas:
- auth, config, docker_client, job_status, n8n, queue, infra.

Dependencias externas:
- `fastapi`, `docker`, `sse-starlette`, `httpx`.

### Endpoints publicos (handlers)

Status e catalogo:
- `health()` -> `GET /health`
- `list_versions()` -> `GET /versions`, `/docker-versions`
- `list_locations()` -> `GET /locations`, `/server-locations`
- `list_instances()` -> `GET /instances`
- `get_capacity()` -> `GET /capacity`
- `cleanup_preview()` -> `GET /cleanup-preview`

Fila/jobs:
- `list_jobs()` -> `GET /jobs`
- `enqueue_instance(request)` -> `POST /enqueue-instance`
- `job_events(job_id, since=0)` -> `GET /job/{job_id}/events`
- `create_instance_stream(name, version, location)` -> `GET /create-instance-stream` (SSE)

CRUD/operacoes de instancia:
- `create_instance(request)` -> `POST /create-instance`
- `delete_instance(instance_id)` -> `DELETE /delete-instance/{instance_id}`
- `instance_status(instance_id)` -> `GET /instance/{instance_id}/status` e alias
- `restart_instance(instance_id)` -> `POST /instance/{instance_id}/restart` e alias
- `reset_instance(instance_id, request)` -> `POST /instance/{instance_id}/reset` e alias
- `update_version(instance_id, request)` -> `POST /instance/{instance_id}/update-version` e alias
- `instance_env(instance_id)` -> `GET /instance/{instance_id}/env`
- `instance_logs(instance_id, tail=50)` -> `GET /instance/{instance_id}/logs`
- `instance_network(instance_id)` -> `GET /instance/{instance_id}/network`

Debug de containers/infra:
- `debug_container_env(name)` -> `GET /debug/container-env/{name}`
- `debug_start_container(name)` -> `POST /debug/start-container/{name}`
- `debug_container_logs(name, tail=30)` -> `GET /debug/container-logs/{name}`
- `fix_traefik_network()` -> `POST /debug/fix-traefik-network`
- `recreate_traefik()` -> `POST /debug/recreate-traefik`
- `debug_all_containers()` -> `GET /debug/all-containers`
- `debug_infra_networks()` -> `GET /debug/infra-networks`

Configuracao/operacao do servico:
- `get_config(request)` -> `GET /config` (mascara segredos)
- `update_config(request)` -> `PUT /config` (edita `.env`, valida campos)
- `test_cloudflare(request)` -> `POST /config/test-cloudflare`
- `system_info()` -> `GET /config/system-info`
- `restart_service()` -> `POST /config/restart-service` (`systemctl restart n8n-manager` em thread)
- `regenerate_api_token()` -> `POST /config/regenerate-token` (reescreve `.env`)
- `regenerate_rabbitmq_password()` -> `POST /config/regenerate-rabbitmq-password` (reescreve `.env`)

Excecoes frequentes:
- `HTTPException(400/403/404/409/500)` para validacao, autenticacao, capacidade, recursos ausentes, falhas operacionais.

Exemplo de uso:
```bash
curl -H "Authorization: Bearer <TOKEN>" http://localhost:5050/instances
```

## 3.13 `config_traefik.py`

Descricao do modulo:
Script operacional que:
1. cria pasta `./traefik` e `./traefik/letsencrypt`;
2. gera `./traefik/.env` com token Cloudflare (ou comentario HTTP-only);
3. gera `./traefik/docker-compose.yml` em modo HTTPS (Cloudflare DNS challenge) ou HTTP;
4. cria rede Docker `n8n-public` (best-effort);
5. executa `docker compose up -d`.

Entradas:
- env vars: `CF_DNS_API_TOKEN`, `ACME_EMAIL`.

Saida:
- arquivos de compose/env + container Traefik ativo (quando comando bem-sucedido).

Exemplo de uso:
```bash
python config_traefik.py
```

## 3.14 `parser.py`

Descricao do modulo:
Parser principal de PDF de saldo de abastecimento (fab0257), com heuristicas por coordenadas de coluna. Gera parse consolidado e relatorios de itens comuns entre mini fabricas. Tambem expoe API FastAPI (quando disponivel) para upload/listagem/download.

### Funcoes publicas (pipeline de parse)
- `clean_color_word(word)`
- `strip_unid_bleed(item_zone)`
- `group_rows(words, y_tol=2.0)`
- `is_header_row(row_words)`
- `extract_color(color_zone)`
- `extract_abast(row_words)`
- `extract_saldo_casa(row_words)`
- `extract_tam(row_words)`
- `is_zero_saldo(value)`
- `make_source_meta(page_number, row_words, row_text, color_zone=None)`
- `row_bbox(row_words)`
- `save_extraction_debug_images(pdf, annotations_by_page, pdf_path)`
- `parse_pdf(pdf_path)`
  - Entrada: caminho PDF.
  - Saida: lista de itens com cores, metadados de origem, `abast`, `saldo_casa`, `par_tipo`, `tam`.
  - Regras: fallback de saldo por substituto quando nacional zero; agrupamento por mini fabrica.
  - Excecoes: I/O e parse de PDF propagadas.
- `save_json(items, path)`
- `save_csv(items, path)`
- `print_summary(items)`
- `build_common_items(items)`
- `save_common_json(common_items, path)`
- `save_common_csv(common_items, path)`
- `save_common_html(common_items, html_path, source_label="Multiplos PDFs")`
- `print_common_summary(common_items)`

### Classes (Pydantic, quando FastAPI disponivel)
- `HealthResponse`: `{ status: str }`
- `GeneratedFile`: `{ name, relative_path, size_bytes, updated_at, direct_url }`
- `FileListResponse`: `{ output_dir, count, files: List[GeneratedFile] }`
- `ParseColor`: `{ color_code, color_desc, par_tipo, tam, abast, saldo_casa, saldo_origem }`
- `ParseItem`: `{ item_code, item_desc, mini_fabrica, colors }`
- `ParseSummary`: `{ total_items, total_colors, total_itens_comuns }`
- `ParseOutputs`: links/paths de `json/csv/html` e `common_*`
- `ParseResponse`: `{ files, summary, outputs, items }`

### Endpoints API (quando `FASTAPI_AVAILABLE`)
- `health()` -> `GET /health`
- `list_files(request)` -> `GET /files`
- `download_file(filename)` -> `GET /files/{filename:path}`
- `parse(request, files=None, file=None)` -> `POST /parse`

### Modo CLI
- 1 PDF: gera `<base>_parsed.(json|csv)` + `<base>_common_items.(json|csv|html)`.
- N PDFs: gera `multi_pdf_*` consolidado.

Dependencias externas:
- `pdfplumber`, `pandas`, `fastapi`, `pydantic`.

Exemplo de uso:
```bash
python parser.py relatorio1.pdf relatorio2.pdf
```

## 4) Fluxo de Dados

### 4.1 Criacao de instancia n8n (manager)
1. Cliente chama `POST /enqueue-instance` ou `GET /create-instance-stream`.
2. API valida nome/versao/capacidade e publica job no RabbitMQ (`app/queue.py`).
3. Worker (`app/worker.py`) consome job, cria container via `app/n8n.py`.
4. Worker executa health-check periodico da URL publica e publica eventos no Redis (`app/job_status.py`).
5. Cliente consulta progresso por polling (`/job/{id}/events`) ou SSE (`/create-instance-stream`).
6. Em sucesso, instancia fica acessivel por Traefik (`subdominio.BASE_DOMAIN`).

### 4.2 Bootstrap e runtime de infraestrutura
1. Startup do FastAPI chama `bootstrap_infra()`.
2. Infra garante rede, Traefik, Redis, RabbitMQ, parser-api dedicada, fallback e pre-pull da imagem n8n.
3. Threads de `worker` e `cleanup` iniciam em paralelo.
4. Cleanup remove periodicamente instancias acima de `CLEANUP_MAX_AGE_DAYS`.

### 4.3 Fluxo parser API
1. Upload PDF(s) via `POST /parse`.
2. Arquivos temporarios sao criados e parseados por `parse_pdf` (heuristicas por coordenadas + regex).
3. Resultados sao serializados em JSON/CSV e painel HTML de itens comuns.
4. Artefatos sao persistidos em `PARSER_OUTPUT_DIR`.
5. `GET /files` lista e `GET /files/{filename}` serve download/visualizacao.

## 5) Configuracao

Arquivos de configuracao relevantes:
- `.env.example`: template principal de variaveis do manager.
- `app/config.py`: leitura e defaults do `.env`.
- `docker-compose.parser-api.yml`: deploy da parser-api e labels Traefik.
- `config_traefik.py`: gera compose/env de Traefik dinamicamente.

Variaveis de ambiente (manager):

| Variavel | Proposito |
|---|---|
| `API_AUTH_TOKEN` | Token Bearer para endpoints protegidos |
| `BASE_DOMAIN` | Dominio base para subdominios das instancias |
| `ACME_EMAIL` | Email ACME para certificados |
| `CF_DNS_API_TOKEN` | Habilita SSL via Cloudflare DNS challenge |
| `DOCKER_NETWORK` | Rede docker compartilhada |
| `SERVER_PORT` | Porta HTTP do FastAPI manager |
| `ALLOWED_ORIGINS` | Lista CORS separada por virgula |
| `RABBITMQ_HOST/PORT/USER/PASSWORD` | Config da fila de jobs |
| `REDIS_HOST/PORT` | Config do store de estado de jobs |
| `DEFAULT_N8N_VERSION` | Versao default do container n8n |
| `INSTANCE_MEM_LIMIT/RESERVATION` | Limites de memoria por instancia |
| `INSTANCE_CPU_SHARES` | Peso relativo de CPU por instancia |
| `DEFAULT_TIMEZONE` | Timezone default das instancias |
| `READINESS_MAX_ATTEMPTS` | Tentativas de readiness no worker |
| `READINESS_POLL_INTERVAL` | Intervalo entre tentativas |
| `CLEANUP_MAX_AGE_DAYS` | Idade maxima para auto-remocao |
| `CLEANUP_INTERVAL_SECONDS` | Periodo do job de cleanup |
| `JOB_TTL` | TTL do estado/eventos de job |
| `JOB_CLEANUP_TTL` | TTL reduzido apos conclusao |
| `PARSER_SUBDOMAIN` | Subdominio da parser-api no Traefik |

Variaveis parser API:

| Variavel | Proposito |
|---|---|
| `PARSER_OUTPUT_DIR` | Diretorio de outputs da parser API |
| `PARSER_EXPORT_DEBUG_IMAGES` | Habilita export de imagens de depuracao na extracao |

## 6) Dependencias Externas

Fonte: `requirements.txt`

| Pacote | Versao | Proposito no projeto |
|---|---|---|
| `fastapi` | `0.115.13` | Framework web principal (manager + parser API) |
| `uvicorn[standard]` | `0.34.0` | ASGI server para FastAPI |
| `docker` | `7.1.0` | SDK para controlar containers/rede/volumes |
| `sse-starlette` | `2.2.1` | Streaming SSE em endpoint de criacao assincrona |
| `python-dotenv` | `1.1.0` | Carregamento de variaveis `.env` |
| `httpx` | `0.28.1` | Chamadas HTTP externas (Docker Hub, Cloudflare) |
| `pika` | `1.3.2` | Cliente RabbitMQ |
| `redis` | `5.2.1` | Cliente Redis para estado/eventos de job |
| `pdfplumber` | `0.11.9` | Extracao de texto/posicao de PDFs |
| `python-multipart` | `0.0.20` | Upload multipart em endpoints FastAPI |
| `pandas` | `3.0.1` | Geracao e serializacao tabular para CSV |

## 7) Arquivos Ignorados

Arquivos `.py` intencionalmente nao documentados como modulo principal (SKIP):

- `app/__init__.py`
  - Motivo: arquivo vazio (0 linhas), sem logica de runtime.

- `parser.old.py`
  - Motivo: implementacao legada da parser API (v1), nao referenciada pelo manager (`main.py`/`app/*`) nem pelo bootstrap atual.

- `parser_response_examples.py`
  - Motivo: somente payloads de exemplo para docs Swagger/ReDoc da versao legada (`parser.old.py`), sem logica operacional.

## 8) Classificacao Final (RELEVANTE vs SKIP)

RELEVANTE (14):
- `main.py`
- `config_traefik.py`
- `parser.py`
- `app/auth.py`
- `app/cleanup.py`
- `app/config.py`
- `app/docker_client.py`
- `app/infra.py`
- `app/job_status.py`
- `app/logger.py`
- `app/n8n.py`
- `app/queue.py`
- `app/routes.py`
- `app/worker.py`

SKIP (3):
- `app/__init__.py`
- `parser.old.py`
- `parser_response_examples.py`

