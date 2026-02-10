"""Rotas da API — endpoints REST e SSE."""

import asyncio
import json
import time
import uuid

import docker
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from .auth import verify_token
from .config import BASE_DOMAIN, DEFAULT_N8N_VERSION, SSE_MAX_DURATION
from .docker_client import get_client
from .job_status import cleanup_job, get_events_since, get_state, init_job
from .n8n import (
    calculate_max_instances,
    container_name,
    create_container,
    extract_encryption_key,
    generate_encryption_key,
    get_container,
    instance_url,
    list_n8n_containers,
    rebuild_container,
    remove_container,
    validate_instance_name,
    validate_version,
)
from .queue import publish_job
from .config import DOCKER_NETWORK, N8N_IMAGE

router = APIRouter()


# ─── Info ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    from .job_status import get_redis

    checks = {"api": "ok"}
    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
    try:
        get_client().ping()
        checks["docker"] = "ok"
    except Exception:
        checks["docker"] = "error"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, "checks": checks, "timestamp": time.time()}


@router.get("/versions", dependencies=[Depends(verify_token)])
@router.get("/docker-versions", dependencies=[Depends(verify_token)])
async def list_versions():
    """Busca as versões mais recentes do N8N diretamente do Docker Hub."""
    import httpx

    import re

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://registry.hub.docker.com/v2/repositories/n8nio/n8n/tags",
                params={"page_size": 50, "ordering": "last_updated"},
            )
            if resp.status_code == 200:
                data = resp.json()
                versions = []
                seen = set()
                semver_re = re.compile(r"^1\.\d+\.\d+$")
                for tag in data.get("results", []):
                    tag_name = tag.get("name", "")
                    # Apenas versões semver 1.X.Y (sem task runners)
                    if semver_re.match(tag_name) and tag_name not in seen:
                        seen.add(tag_name)
                        versions.append({"id": tag_name, "name": tag_name})
                    if len(versions) >= 8:
                        break

                # Ordenar por versão decrescente
                versions.sort(
                    key=lambda v: [int(x) for x in v["id"].split(".")],
                    reverse=True,
                )

                if versions:
                    return {"versions": versions}
    except Exception as e:
        pass

    # Fallback: versão latest
    return {
        "versions": [
            {"id": "latest", "name": "Última versão (latest)"},
        ]
    }


@router.get("/locations", dependencies=[Depends(verify_token)])
@router.get("/server-locations", dependencies=[Depends(verify_token)])
async def list_locations():
    return {
        "locations": [
            {"id": "vinhedo", "name": "Vinhedo, São Paulo - Brasil", "active": True}
        ]
    }


@router.get("/instances", dependencies=[Depends(verify_token)])
async def list_instances():
    return {"instances": list_n8n_containers()}


@router.get("/capacity", dependencies=[Depends(verify_token)])
async def get_capacity():
    """Retorna capacidade da VPS e instâncias ativas."""
    return calculate_max_instances()


@router.get("/cleanup-preview", dependencies=[Depends(verify_token)])
async def cleanup_preview():
    """Mostra instâncias que serão removidas pelo auto-cleanup (5+ dias)."""
    containers = list_n8n_containers()
    preview = []
    for c in containers:
        age = c.get("age_days")
        preview.append({
            **c,
            "will_be_deleted": age is not None and age >= 5,
            "days_remaining": max(0, 5 - age) if age is not None else None,
        })
    return {"instances": preview}


# ─── Queue ────────────────────────────────────────────────


@router.get("/jobs", dependencies=[Depends(verify_token)])
async def list_jobs():
    """Lista todos os jobs ativos (pending/running) no Redis."""
    from .job_status import get_redis, get_events_since

    r = get_redis()
    keys = r.keys("job:*:state")
    jobs = []
    for key in keys:
        job_id = key.split(":")[1]
        state = r.get(key) or "unknown"
        if state in ("pending", "running"):
            events = get_events_since(job_id, 0)
            last_msg = events[-1].get("message", "") if events else ""
            progress = events[-1].get("progress", 0) if events else 0
            # Tentar extrair nome da instancia dos eventos
            name = ""
            for ev in events:
                if ev.get("name"):
                    name = ev["name"]
                    break
            jobs.append({
                "job_id": job_id,
                "state": state,
                "progress": progress,
                "last_message": last_msg,
                "name": name,
                "event_count": len(events),
            })
    return {"jobs": jobs}


@router.post("/enqueue-instance", dependencies=[Depends(verify_token)])
async def enqueue_instance(request: Request):
    """Enfileira criação de instância e retorna job_id imediatamente."""
    body = await request.json()
    name = body.get("name", "").strip()
    version = body.get("version", DEFAULT_N8N_VERSION).strip()
    location = body.get("location", "vinhedo").strip()

    if not name:
        raise HTTPException(400, "Nome obrigatório")

    try:
        name = validate_instance_name(name)
        version = validate_version(version)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Checar capacidade da VPS
    cap = calculate_max_instances()
    if not cap["can_create"]:
        raise HTTPException(
            409,
            f"VPS sem recursos. {cap['active_instances']}/{cap['max_instances']} instâncias ativas.",
        )

    try:
        get_container(name)
        raise HTTPException(400, f"Instância '{name}' já existe")
    except docker.errors.NotFound:
        pass

    job_id = str(uuid.uuid4())
    init_job(job_id)
    publish_job(job_id, {
        "job_id": job_id,
        "name": name,
        "version": version,
        "location": location,
    })

    return {"job_id": job_id, "name": name}


@router.get("/job/{job_id}/events", dependencies=[Depends(verify_token)])
async def job_events(job_id: str, since: int = Query(0)):
    """Retorna eventos de um job a partir de um índice."""
    state = get_state(job_id)
    if state == "unknown":
        raise HTTPException(404, "Job não encontrado ou expirado")

    events = get_events_since(job_id, since)
    if state in ("complete", "error"):
        cleanup_job(job_id)

    return {"state": state, "events": events, "next_index": since + len(events)}


# ─── CRUD ─────────────────────────────────────────────────


@router.post("/create-instance", dependencies=[Depends(verify_token)])
async def create_instance(request: Request):
    """Cria instância N8N (resposta simples)."""
    body = await request.json()
    name = body.get("name", "").strip()
    version = body.get("version", DEFAULT_N8N_VERSION).strip()

    if not name:
        raise HTTPException(400, "Nome obrigatório")

    try:
        name = validate_instance_name(name)
        version = validate_version(version)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Checar capacidade da VPS
    cap = calculate_max_instances()
    if not cap["can_create"]:
        raise HTTPException(
            409,
            f"VPS sem recursos. {cap['active_instances']}/{cap['max_instances']} instâncias ativas.",
        )

    try:
        get_container(name)
        raise HTTPException(400, f"Instância '{name}' já existe")
    except docker.errors.NotFound:
        pass

    encryption_key = generate_encryption_key()

    container = create_container(name, version, encryption_key)

    return {
        "instance_id": name,
        "url": instance_url(name),
        "status": container.status,
        "location": "vinhedo",
        "container_status": "running",
    }


@router.get("/create-instance-stream", dependencies=[Depends(verify_token)])
async def create_instance_stream(
    name: str = Query(...),
    version: str = Query(DEFAULT_N8N_VERSION),
    location: str = Query("vinhedo"),
):
    """Cria instância N8N via fila RabbitMQ com streaming SSE de progresso."""

    try:
        name = validate_instance_name(name)
        version = validate_version(version)
    except ValueError as e:
        async def validation_error_gen():
            yield json.dumps({"status": "error", "message": str(e)})
        return EventSourceResponse(validation_error_gen())

    # Fast-fail: verificar capacidade da VPS
    cap = calculate_max_instances()
    if not cap["can_create"]:
        async def cap_error_gen():
            yield json.dumps({
                "status": "error",
                "message": f"VPS sem recursos. {cap['active_instances']}/{cap['max_instances']} instâncias ativas.",
            })
        return EventSourceResponse(cap_error_gen())

    # Fast-fail: verificar duplicata antes de enfileirar
    try:
        get_container(name)

        async def error_gen():
            yield json.dumps({"status": "error", "message": f"Instância '{name}' já existe"})

        return EventSourceResponse(error_gen())
    except docker.errors.NotFound:
        pass

    # Criar job e publicar na fila
    job_id = str(uuid.uuid4())
    init_job(job_id)

    try:
        publish_job(job_id, {
            "job_id": job_id,
            "name": name,
            "version": version,
            "location": location,
        })
    except Exception as e:
        async def queue_error_gen():
            yield json.dumps({"status": "error", "message": f"Erro ao enfileirar job: {e}"})

        return EventSourceResponse(queue_error_gen())

    async def event_generator():
        """Poll Redis por eventos do worker e yield como SSE."""
        event_index = 0
        start_time = time.time()
        max_duration = SSE_MAX_DURATION

        try:
            while True:
                # Buscar novos eventos desde o ultimo indice
                events = get_events_since(job_id, event_index)
                for ev in events:
                    yield json.dumps(ev)
                    event_index += 1

                    if ev.get("status") in ("complete", "error"):
                        cleanup_job(job_id)
                        return

                # Timeout de seguranca
                if time.time() - start_time > max_duration:
                    yield json.dumps({"status": "error", "message": "Timeout: criacao demorou mais de 5 minutos"})
                    cleanup_job(job_id)
                    return

                # Verificar se job ainda existe
                state = get_state(job_id)
                if state == "unknown":
                    yield json.dumps({"status": "error", "message": "Job perdido ou expirado"})
                    return

                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            cleanup_job(job_id)
            raise

    return EventSourceResponse(event_generator())


@router.delete("/delete-instance/{instance_id}", dependencies=[Depends(verify_token)])
async def delete_instance(instance_id: str):
    try:
        remove_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, f"Instância '{instance_id}' não encontrada")

    return {"message": "Instância excluída com sucesso", "instance_id": instance_id}


# ─── Operações ────────────────────────────────────────────


@router.get("/instance/{instance_id}/status", dependencies=[Depends(verify_token)])
@router.get("/instance-status/{instance_id}", dependencies=[Depends(verify_token)])
async def instance_status(instance_id: str):
    try:
        container = get_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    container.reload()
    stats = container.stats(stream=False)
    mem_usage = stats.get("memory_stats", {}).get("usage", 0)
    mem_limit = stats.get("memory_stats", {}).get("limit", 0)

    return {
        "instance_id": instance_id,
        "status": container.status,
        "url": instance_url(instance_id),
        "location": "vinhedo",
        "version": container.image.tags[0].split(":")[-1] if container.image.tags else "unknown",
        "uptime": container.attrs.get("State", {}).get("StartedAt", ""),
        "memory": {
            "usage_mb": round(mem_usage / 1024 / 1024, 1),
            "limit_mb": round(mem_limit / 1024 / 1024, 1),
        },
    }


@router.post("/instance/{instance_id}/restart", dependencies=[Depends(verify_token)])
@router.post("/restart-instance/{instance_id}", dependencies=[Depends(verify_token)])
async def restart_instance(instance_id: str):
    try:
        container = get_container(instance_id)
        container.restart(timeout=15)
        return {"message": "Instância reiniciada", "instance_id": instance_id}
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")


@router.post("/instance/{instance_id}/reset", dependencies=[Depends(verify_token)])
@router.post("/reset-instance/{instance_id}", dependencies=[Depends(verify_token)])
async def reset_instance(instance_id: str, request: Request):
    body = await request.json()
    version = body.get("version", "latest")

    try:
        remove_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    encryption_key = generate_encryption_key()
    create_container(instance_id, version, encryption_key)

    return {
        "message": "Instância resetada",
        "instance_id": instance_id,
        "url": instance_url(instance_id),
    }


@router.post("/instance/{instance_id}/update-version", dependencies=[Depends(verify_token)])
@router.post("/update-version/{instance_id}", dependencies=[Depends(verify_token)])
async def update_version(instance_id: str, request: Request):
    body = await request.json()
    new_version = body.get("version", "latest")

    try:
        rebuild_container(instance_id, new_version)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    return {"message": f"Versão atualizada para {new_version}", "instance_id": instance_id}


@router.get("/instance/{instance_id}/env", dependencies=[Depends(verify_token)])
async def instance_env(instance_id: str):
    """Retorna as variáveis de ambiente do container (somente leitura)."""
    try:
        container = get_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    container.reload()
    env_list = container.attrs.get("Config", {}).get("Env", [])
    env_vars = {}
    for item in env_list:
        key, _, value = item.partition("=")
        env_vars[key] = value
    return {"instance_id": instance_id, "env": env_vars}


@router.get("/instance/{instance_id}/logs", dependencies=[Depends(verify_token)])
async def instance_logs(instance_id: str, tail: int = Query(50)):
    """Retorna as últimas linhas de log do container."""
    try:
        container = get_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    logs = container.logs(tail=min(tail, 200)).decode("utf-8", errors="replace")
    return {"instance_id": instance_id, "logs": logs}


@router.get("/debug/container-env/{name}", dependencies=[Depends(verify_token)])
async def debug_container_env(name: str):
    """Variáveis de ambiente de qualquer container (somente leitura)."""
    client = get_client()
    try:
        c = client.containers.get(name)
        c.reload()
        env_list = c.attrs.get("Config", {}).get("Env", [])
        env_vars = {}
        for item in env_list:
            key, _, value = item.partition("=")
            env_vars[key] = value
        return {"name": name, "env": env_vars}
    except docker.errors.NotFound:
        raise HTTPException(404, f"Container '{name}' não encontrado")


@router.post("/debug/start-container/{name}", dependencies=[Depends(verify_token)])
async def debug_start_container(name: str):
    """Tenta iniciar um container e retorna erro exato se falhar."""
    client = get_client()
    try:
        c = client.containers.get(name)
        if c.status == "running":
            return {"name": name, "status": "already_running"}
        try:
            c.start()
            c.reload()
            return {"name": name, "status": c.status, "started": True}
        except Exception as start_err:
            return {"name": name, "status": c.status, "error": str(start_err)}
    except docker.errors.NotFound:
        raise HTTPException(404, f"Container '{name}' não encontrado")


@router.get("/debug/container-logs/{name}", dependencies=[Depends(verify_token)])
async def debug_container_logs(name: str, tail: int = Query(30)):
    """Logs de qualquer container para debug."""
    client = get_client()
    try:
        c = client.containers.get(name)
        logs = c.logs(tail=min(tail, 200)).decode("utf-8", errors="replace")
        return {"name": name, "status": c.status, "logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(404, f"Container '{name}' não encontrado")


@router.post("/debug/fix-traefik-network", dependencies=[Depends(verify_token)])
async def fix_traefik_network():
    """Remove e recria Traefik na rede correta (one-time fix)."""
    from .infra import ensure_traefik
    client = get_client()
    try:
        # Verificar se já está correto
        try:
            traefik = client.containers.get("traefik")
            traefik.reload()
            networks = traefik.attrs.get("NetworkSettings", {}).get("Networks", {})
            if DOCKER_NETWORK in networks and traefik.status == "running":
                return {"message": "Traefik já está na rede correta", "status": "ok"}

            # Remover Traefik que está na rede errada
            traefik.remove(force=True)
        except docker.errors.NotFound:
            pass

        # Aguardar portas liberarem
        import time
        time.sleep(5)

        # Recriar via ensure_traefik (cria com network=DOCKER_NETWORK)
        ensure_traefik()

        # Verificar resultado
        traefik = client.containers.get("traefik")
        traefik.reload()
        nets = list(traefik.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
        return {
            "message": "Traefik recriado na rede correta",
            "status": traefik.status,
            "networks": nets,
            "fixed": True,
        }
    except Exception as e:
        raise HTTPException(500, f"Erro: {e}")


@router.post("/debug/recreate-traefik", dependencies=[Depends(verify_token)])
async def recreate_traefik():
    """Forca remocao e recriacao do Traefik (usa config_traefik.py)."""
    from .infra import _run_config_traefik, ensure_network
    import time as _time

    client = get_client()

    # Remover todos containers traefik
    removed = []
    for c in client.containers.list(all=True):
        if "traefik" in c.name.lower():
            try:
                c.remove(force=True)
                removed.append(c.name)
            except Exception:
                pass

    _time.sleep(3)
    ensure_network()
    ok = _run_config_traefik()

    # Verificar resultado
    try:
        traefik = client.containers.get("traefik")
        traefik.reload()
        image = traefik.image.tags[0] if traefik.image.tags else "unknown"
        nets = list(traefik.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
        return {
            "removed": removed,
            "status": traefik.status,
            "image": image,
            "networks": nets,
            "config_traefik_ok": ok,
        }
    except docker.errors.NotFound:
        raise HTTPException(500, "Traefik nao foi criado")


@router.get("/debug/all-containers", dependencies=[Depends(verify_token)])
async def debug_all_containers():
    """Lista TODOS os containers Docker (não só n8n)."""
    client = get_client()
    containers = client.containers.list(all=True)
    result = []
    for c in containers:
        c.reload()
        ports = c.attrs.get("HostConfig", {}).get("PortBindings") or {}
        port_list = []
        for cp, binds in ports.items():
            if binds:
                for b in binds:
                    port_list.append(f"{b.get('HostPort', '?')}->{cp}")
        nets = list(c.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())
        result.append({
            "name": c.name,
            "image": c.image.tags[0] if c.image.tags else c.attrs.get("Config", {}).get("Image", "?"),
            "status": c.status,
            "ports": port_list,
            "networks": nets,
        })
    return {"containers": result}


@router.get("/debug/infra-networks", dependencies=[Depends(verify_token)])
async def debug_infra_networks():
    """Lista redes de todos os containers de infra para debug."""
    client = get_client()
    infra_names = ["traefik", "postgres", "redis", "rabbitmq"]
    result = {}
    for name in infra_names:
        try:
            c = client.containers.get(name)
            c.reload()
            networks = c.attrs.get("NetworkSettings", {}).get("Networks", {})
            net_info = {}
            for net_name, net_data in networks.items():
                net_info[net_name] = net_data.get("IPAddress", "")
            result[name] = {"status": c.status, "networks": net_info}
        except Exception:
            result[name] = {"status": "not_found", "networks": {}}
    return result


# ─── Configuração ─────────────────────────────────────────


@router.get("/config", dependencies=[Depends(verify_token)])
async def get_config(request: Request):
    """Retorna configuração atual do .env + defaults."""
    import os as _os
    from dotenv import dotenv_values

    env_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".env")
    env_vals = dotenv_values(env_path) if _os.path.exists(env_path) else {}

    # Defaults para campos que podem não existir no .env
    defaults = {
        "BASE_DOMAIN": "n8n.marketcodebrasil.com.br",
        "ACME_EMAIL": "lojasketchware@gmail.com",
        "CF_DNS_API_TOKEN": "",
        "SERVER_PORT": "5050",
        "ALLOWED_ORIGINS": "*",
        "API_AUTH_TOKEN": "",
        "DEFAULT_N8N_VERSION": "1.123.20",
        "DEFAULT_TIMEZONE": "America/Sao_Paulo",
        "INSTANCE_MEM_LIMIT": "384m",
        "INSTANCE_MEM_RESERVATION": "192m",
        "INSTANCE_CPU_SHARES": "512",
        "CLEANUP_MAX_AGE_DAYS": "5",
        "CLEANUP_INTERVAL_SECONDS": "3600",
        "DOCKER_NETWORK": "n8n-public",
        "RABBITMQ_HOST": "127.0.0.1",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "",
        "RABBITMQ_PASSWORD": "",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "6379",
        "JOB_TTL": "600",
        "JOB_CLEANUP_TTL": "300",
    }

    merged = {**defaults, **env_vals}

    # Campos sensiveis: mascarar
    sensitive = ["CF_DNS_API_TOKEN", "RABBITMQ_PASSWORD", "API_AUTH_TOKEN"]
    reveal = request.query_params.get("reveal", "")
    for key in sensitive:
        val = merged.get(key, "")
        if val and key != reveal:
            merged[key] = "****" + val[-4:] if len(val) > 4 else "****"

    return {"config": merged, "env_path": env_path}


@router.put("/config", dependencies=[Depends(verify_token)])
async def update_config(request: Request):
    """Salva alterações no .env."""
    import os as _os

    body = await request.json()
    updates = body.get("config", {})
    if not updates:
        raise HTTPException(400, "Nenhuma configuração enviada")

    # Validações básicas
    if "SERVER_PORT" in updates:
        try:
            port = int(updates["SERVER_PORT"])
            if port < 1 or port > 65535:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, "SERVER_PORT deve ser um número entre 1 e 65535")

    if "BASE_DOMAIN" in updates and not updates["BASE_DOMAIN"].strip():
        raise HTTPException(400, "BASE_DOMAIN não pode ser vazio")

    if "CLEANUP_MAX_AGE_DAYS" in updates:
        try:
            days = int(updates["CLEANUP_MAX_AGE_DAYS"])
            if days < 1:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, "CLEANUP_MAX_AGE_DAYS deve ser >= 1")

    if "INSTANCE_CPU_SHARES" in updates:
        try:
            shares = int(updates["INSTANCE_CPU_SHARES"])
            if shares < 128 or shares > 4096:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, "INSTANCE_CPU_SHARES deve ser entre 128 e 4096")

    env_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".env")

    # Ler .env existente preservando comentários e ordem
    lines = []
    existing_keys = set()
    if _os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()

    # Atualizar linhas existentes
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0]
            if key in updates:
                # Não sobrescrever com valor mascarado
                if updates[key].startswith("****"):
                    new_lines.append(line)
                else:
                    new_lines.append(f"{key}={updates[key]}\n")
                existing_keys.add(key)
                continue
        new_lines.append(line)

    # Adicionar novas chaves que não existiam
    for key, value in updates.items():
        if key not in existing_keys and not value.startswith("****"):
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)

    # Campos que precisam de restart para aplicar
    restart_fields = {
        "SERVER_PORT", "API_AUTH_TOKEN", "BASE_DOMAIN", "CF_DNS_API_TOKEN",
        "ACME_EMAIL", "DOCKER_NETWORK", "RABBITMQ_HOST", "RABBITMQ_PORT",
        "RABBITMQ_USER", "RABBITMQ_PASSWORD", "REDIS_HOST", "REDIS_PORT",
        "ALLOWED_ORIGINS",
    }
    needs_restart = bool(set(updates.keys()) & restart_fields)

    return {
        "message": "Configuração salva",
        "needs_restart": needs_restart,
        "updated_keys": list(updates.keys()),
    }


@router.post("/config/test-cloudflare", dependencies=[Depends(verify_token)])
async def test_cloudflare(request: Request):
    """Testa se o token Cloudflare é válido."""
    import httpx

    body = await request.json()
    token = body.get("token", "").strip()
    if not token:
        raise HTTPException(400, "Token não informado")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.cloudflare.com/client/v4/user/tokens/verify",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = resp.json()
            if data.get("success"):
                return {"valid": True, "message": "Token válido"}
            errors = data.get("errors", [{}])
            msg = errors[0].get("message", "Token inválido") if errors else "Token inválido"
            return {"valid": False, "message": msg}
    except Exception as e:
        return {"valid": False, "message": f"Erro de conexão: {e}"}


@router.get("/config/system-info", dependencies=[Depends(verify_token)])
async def system_info():
    """Retorna informações do sistema (RAM, swap, Docker, capacidade)."""
    import subprocess
    from .config import SSL_ENABLED, PROTOCOL

    info = {"ram": {}, "swap": {}, "docker": {}, "capacity": {}, "ssl_enabled": SSL_ENABLED, "protocol": PROTOCOL}

    # RAM e Swap via /proc/meminfo
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])

        info["ram"] = {
            "total_mb": round(meminfo.get("MemTotal", 0) / 1024),
            "available_mb": round(meminfo.get("MemAvailable", 0) / 1024),
            "used_mb": round((meminfo.get("MemTotal", 0) - meminfo.get("MemAvailable", 0)) / 1024),
        }
        info["swap"] = {
            "total_mb": round(meminfo.get("SwapTotal", 0) / 1024),
            "used_mb": round((meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)) / 1024),
        }
    except Exception:
        pass

    # Docker version
    try:
        client = get_client()
        ver = client.version()
        info["docker"] = {
            "version": ver.get("Version", "unknown"),
            "api_version": ver.get("ApiVersion", "unknown"),
        }
    except Exception:
        info["docker"] = {"version": "indisponível"}

    # Capacidade de instâncias
    try:
        info["capacity"] = calculate_max_instances()
    except Exception:
        pass

    # Uptime
    try:
        with open("/proc/uptime", "r") as f:
            uptime_secs = float(f.read().split()[0])
            days = int(uptime_secs // 86400)
            hours = int((uptime_secs % 86400) // 3600)
            info["uptime"] = f"{days}d {hours}h"
    except Exception:
        info["uptime"] = "indisponível"

    return info


@router.post("/config/restart-service", dependencies=[Depends(verify_token)])
async def restart_service():
    """Reinicia o serviço n8n-manager via systemctl."""
    import subprocess
    import threading

    def _restart():
        import time as _time
        _time.sleep(1)  # Dar tempo para a resposta HTTP ser enviada
        subprocess.run(["systemctl", "restart", "n8n-manager"], timeout=30)

    threading.Thread(target=_restart, daemon=True).start()
    return {"message": "Reiniciando serviço... aguarde alguns segundos"}


@router.get("/instance/{instance_id}/network", dependencies=[Depends(verify_token)])
async def instance_network(instance_id: str):
    """Retorna info de rede do container para debug."""
    try:
        container = get_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    container.reload()
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    net_info = {}
    for net_name, net_data in networks.items():
        net_info[net_name] = {
            "ip": net_data.get("IPAddress", ""),
            "gateway": net_data.get("Gateway", ""),
        }

    return {
        "instance_id": instance_id,
        "networks": net_info,
        "expected_network": DOCKER_NETWORK,
    }
