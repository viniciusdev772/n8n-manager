"""Lógica de criação e configuração de containers N8N."""

import secrets

from .config import (
    BASE_DOMAIN,
    DOCKER_NETWORK,
    INSTANCE_CPU_PERIOD,
    INSTANCE_CPU_QUOTA,
    INSTANCE_MEM_LIMIT,
    N8N_IMAGE,
    PG_PASSWORD,
    PG_USER,
    TRAEFIK_CERT_RESOLVER,
)
from .database import db_name_for
from .docker_client import get_client


def container_name(instance_name: str) -> str:
    return f"n8n-{instance_name}"


def instance_url(instance_name: str) -> str:
    return f"https://{instance_name}.{BASE_DOMAIN}"


def generate_encryption_key() -> str:
    return secrets.token_hex(32)


def build_env(name: str, encryption_key: str) -> dict:
    """Variáveis de ambiente otimizadas para instância gratuita."""
    db = db_name_for(name)
    host = f"{name}.{BASE_DOMAIN}"

    return {
        # Geral
        "N8N_PORT": "5678",
        "N8N_PROTOCOL": "https",
        "N8N_HOST": host,
        "N8N_ENCRYPTION_KEY": encryption_key,
        "WEBHOOK_URL": f"https://{host}/",
        "N8N_PROXY_HOPS": "1",
        "GENERIC_TIMEZONE": "America/Sao_Paulo",
        "N8N_LOG_LEVEL": "warn",
        # Banco
        "DB_TYPE": "postgresdb",
        "DB_POSTGRESDB_HOST": "postgres",
        "DB_POSTGRESDB_PORT": "5432",
        "DB_POSTGRESDB_DATABASE": db,
        "DB_POSTGRESDB_USER": PG_USER,
        "DB_POSTGRESDB_PASSWORD": PG_PASSWORD,
        "DB_POSTGRESDB_SCHEMA": "public",
        # Economia agressiva
        "EXECUTIONS_DATA_SAVE_ON_ERROR": "all",
        "EXECUTIONS_DATA_SAVE_ON_SUCCESS": "none",
        "EXECUTIONS_DATA_SAVE_ON_PROGRESS": "false",
        "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS": "false",
        # Pruning
        "EXECUTIONS_DATA_PRUNE": "true",
        "EXECUTIONS_DATA_MAX_AGE": "72",
        "EXECUTIONS_DATA_PRUNE_MAX_COUNT": "500",
        # Performance
        "N8N_CONCURRENCY_PRODUCTION_LIMIT": "5",
        "NODE_OPTIONS": "--max-old-space-size=384",
        # Desabilita task runners (Python não disponível na imagem)
        "N8N_RUNNERS_ENABLED": "false",
        "N8N_PYTHON_ENABLED": "false",
    }


def build_traefik_labels(name: str) -> dict:
    """Labels para roteamento automático do Traefik com SSL."""
    host = f"{name}.{BASE_DOMAIN}"
    return {
        "traefik.enable": "true",
        f"traefik.http.routers.n8n-{name}.rule": f"Host(`{host}`)",
        f"traefik.http.routers.n8n-{name}.entrypoints": "websecure",
        f"traefik.http.routers.n8n-{name}.tls.certresolver": TRAEFIK_CERT_RESOLVER,
        f"traefik.http.services.n8n-{name}.loadbalancer.server.port": "5678",
        "app.managed": "true",
        "app.type": "n8n",
        "app.instance": name,
    }


def create_container(name: str, version: str, encryption_key: str):
    """Cria e inicia um container N8N."""
    client = get_client()
    image_tag = f"{N8N_IMAGE}:{version}"

    client.images.pull(N8N_IMAGE, tag=version)

    env = build_env(name, encryption_key)
    labels = build_traefik_labels(name)

    return client.containers.run(
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


def get_container(name: str):
    """Obtém um container pelo nome da instância."""
    return get_client().containers.get(container_name(name))


def remove_container(name: str):
    """Remove container e seu volume de dados."""
    client = get_client()

    container = client.containers.get(container_name(name))
    container.remove(force=True, v=True)

    try:
        vol = client.volumes.get(f"n8n-data-{name}")
        vol.remove(force=True)
    except Exception:
        pass


def list_n8n_containers() -> list:
    """Lista todos os containers N8N gerenciados."""
    client = get_client()
    containers = client.containers.list(
        all=True, filters={"label": "app.type=n8n"}
    )
    result = []
    for c in containers:
        labels = c.labels
        inst = labels.get("app.instance", "")
        result.append({
            "instance_id": inst,
            "name": inst,
            "status": c.status,
            "url": instance_url(inst),
            "location": "vinhedo",
            "version": c.image.tags[0].split(":")[-1] if c.image.tags else "unknown",
            "container_id": c.short_id,
        })
    return result
