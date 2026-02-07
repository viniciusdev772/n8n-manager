"""Rotas da API — endpoints REST e SSE."""

import asyncio
import json
import time

import docker
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from .auth import verify_token
from .config import BASE_DOMAIN
from .database import create_tenant_db, drop_tenant_db
from .docker_client import get_client
from .n8n import (
    build_env,
    build_traefik_labels,
    container_name,
    create_container,
    generate_encryption_key,
    get_container,
    instance_url,
    list_n8n_containers,
    remove_container,
)
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

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://registry.hub.docker.com/v2/repositories/n8nio/n8n/tags",
                params={"page_size": 20, "ordering": "last_updated"},
            )
            if resp.status_code == 200:
                data = resp.json()
                versions = []
                seen = set()
                for tag in data.get("results", []):
                    name = tag.get("name", "")
                    # Filtrar: apenas versões semver (X.Y.Z), sem -beta, -next, -rc
                    if name and name[0].isdigit() and "-" not in name and name not in seen:
                        seen.add(name)
                        versions.append({"id": name, "name": name})
                    if len(versions) >= 8:
                        break

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


# ─── CRUD ─────────────────────────────────────────────────


@router.post("/create-instance", dependencies=[Depends(verify_token)])
async def create_instance(request: Request):
    """Cria instância N8N (resposta simples)."""
    body = await request.json()
    name = body.get("name", "").strip()
    version = body.get("version", "latest").strip()

    if not name:
        raise HTTPException(400, "Nome obrigatório")

    try:
        get_container(name)
        raise HTTPException(400, f"Instância '{name}' já existe")
    except docker.errors.NotFound:
        pass

    encryption_key = generate_encryption_key()
    create_tenant_db(name)

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
    version: str = Query("latest"),
    location: str = Query("vinhedo"),
):
    """Cria instância N8N com streaming SSE de progresso."""

    async def event_generator():
        try:
            # Verificar duplicata
            try:
                get_container(name)
                yield json.dumps({"status": "error", "message": f"Instância '{name}' já existe"})
                return
            except docker.errors.NotFound:
                pass

            encryption_key = generate_encryption_key()

            # 1. Banco
            yield json.dumps({"status": "info", "message": "Criando banco de dados..."})
            await asyncio.sleep(0.3)
            try:
                create_tenant_db(name)
            except Exception as e:
                yield json.dumps({"status": "error", "message": f"Erro ao criar banco: {e}"})
                return
            yield json.dumps({"status": "info", "message": "Banco criado com sucesso"})

            # 2. Imagem
            image_tag = f"{N8N_IMAGE}:{version}"
            yield json.dumps({"status": "info", "message": f"Baixando imagem {image_tag}..."})
            await asyncio.sleep(0.3)
            try:
                get_client().images.pull(N8N_IMAGE, tag=version)
            except Exception as e:
                yield json.dumps({"status": "error", "message": f"Erro ao baixar imagem: {e}"})
                return
            yield json.dumps({"status": "info", "message": "Imagem pronta"})

            # 3. Container
            yield json.dumps({"status": "info", "message": "Criando container N8N..."})
            await asyncio.sleep(0.3)
            try:
                env = build_env(name, encryption_key)
                labels = build_traefik_labels(name)

                container = get_client().containers.run(
                    image=image_tag,
                    name=container_name(name),
                    detach=True,
                    restart_policy={"Name": "unless-stopped"},
                    environment=env,
                    labels=labels,
                    mem_limit=INSTANCE_MEM_LIMIT,
                    cpu_period=INSTANCE_CPU_PERIOD,
                    cpu_quota=INSTANCE_CPU_QUOTA,
                    volumes={f"n8n-data-{name}": {"bind": "/home/node/.n8n", "mode": "rw"}},
                    network=DOCKER_NETWORK,
                )
            except Exception as e:
                yield json.dumps({"status": "error", "message": f"Erro ao criar container: {e}"})
                return

            yield json.dumps({"status": "info", "message": "Container criado, iniciando N8N..."})

            # 4. Aguardar startup
            yield json.dumps({"status": "info", "message": "Aguardando instância ficar disponível..."})
            for i in range(30):
                await asyncio.sleep(2)
                container.reload()
                if container.status == "running":
                    yield json.dumps({"status": "info", "message": f"Verificando N8N ({i + 1}/30)"})
                    try:
                        logs = container.logs(tail=5).decode("utf-8", errors="replace")
                        if "Editor is now accessible" in logs or "Webhook listener" in logs:
                            break
                    except Exception:
                        pass
                elif container.status == "exited":
                    logs = container.logs(tail=20).decode("utf-8", errors="replace")
                    yield json.dumps({"status": "error", "message": f"Container parou.\n{logs}"})
                    return

            yield json.dumps({"status": "info", "message": "Configurando SSL via Traefik..."})
            await asyncio.sleep(3)

            # 5. Sucesso
            yield json.dumps({
                "status": "complete",
                "message": "Instância N8N criada com sucesso!",
                "instance_id": name,
                "url": instance_url(name),
                "location": "vinhedo",
                "container_status": "running",
            })

        except Exception as e:
            yield json.dumps({"status": "error", "message": f"Erro inesperado: {e}"})

    return EventSourceResponse(event_generator())


@router.delete("/delete-instance/{instance_id}", dependencies=[Depends(verify_token)])
async def delete_instance(instance_id: str):
    try:
        remove_container(instance_id)
    except docker.errors.NotFound:
        raise HTTPException(404, f"Instância '{instance_id}' não encontrada")

    try:
        drop_tenant_db(instance_id)
    except Exception as e:
        print(f"[WARN] Erro ao remover banco: {e}")

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

    try:
        drop_tenant_db(instance_id)
    except Exception:
        pass
    create_tenant_db(instance_id)

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
