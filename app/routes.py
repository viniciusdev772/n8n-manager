"""Rotas da API — endpoints REST e SSE."""

import asyncio
import json
import time
import uuid

import docker
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from .auth import verify_token
from .config import BASE_DOMAIN, DEFAULT_N8N_VERSION
from .docker_client import get_client
from .job_status import cleanup_job, get_events_since, get_state, init_job
from .n8n import (
    build_env,
    build_traefik_labels,
    calculate_max_instances,
    container_name,
    create_container,
    generate_encryption_key,
    get_container,
    instance_url,
    list_n8n_containers,
    remove_container,
)
from .queue import publish_job
from .config import (
    DOCKER_NETWORK,
    INSTANCE_CPU_PERIOD,
    INSTANCE_CPU_QUOTA,
    INSTANCE_MEM_LIMIT,
    N8N_IMAGE,
)

router = APIRouter()


# ─── Info ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


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
        print(f"[WARN] Falha ao buscar versoes do Docker Hub: {e}")

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


@router.post("/enqueue-instance", dependencies=[Depends(verify_token)])
async def enqueue_instance(request: Request):
    """Enfileira criação de instância e retorna job_id imediatamente."""
    body = await request.json()
    name = body.get("name", "").strip()
    version = body.get("version", DEFAULT_N8N_VERSION).strip()
    location = body.get("location", "vinhedo").strip()

    if not name:
        raise HTTPException(400, "Nome obrigatório")

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
        max_duration = 300  # 5 minutos

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
        old = get_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    # Preservar env vars e labels
    old_env_list = old.attrs.get("Config", {}).get("Env", [])
    old_env = {}
    for e in old_env_list:
        k, _, v = e.partition("=")
        old_env[k] = v
    old_labels = old.labels

    old.remove(force=True)  # Mantém volume

    client = get_client()
    image_tag = f"{N8N_IMAGE}:{new_version}"
    client.images.pull(N8N_IMAGE, tag=new_version)

    client.containers.run(
        image=image_tag,
        name=container_name(instance_id),
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        environment=old_env,
        labels=old_labels,
        mem_limit=INSTANCE_MEM_LIMIT,
        cpu_period=INSTANCE_CPU_PERIOD,
        cpu_quota=INSTANCE_CPU_QUOTA,
        volumes={f"n8n-data-{instance_id}": {"bind": "/home/node/.n8n", "mode": "rw"}},
        network=DOCKER_NETWORK,
    )

    return {"message": f"Versão atualizada para {new_version}", "instance_id": instance_id}


@router.get("/instance/{instance_id}/logs", dependencies=[Depends(verify_token)])
async def instance_logs(instance_id: str, tail: int = Query(50)):
    """Retorna as últimas linhas de log do container."""
    try:
        container = get_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, "Instância não encontrada")

    logs = container.logs(tail=min(tail, 200)).decode("utf-8", errors="replace")
    return {"instance_id": instance_id, "logs": logs}


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
