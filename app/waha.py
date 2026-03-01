"""Lógica de criação e gestão de containers WAHA."""

import re
import secrets
from datetime import datetime, timezone

from .config import (
    BASE_DOMAIN,
    DOCKER_NETWORK,
    INSTANCE_MEM_LIMIT,
    PROTOCOL,
    SSL_ENABLED,
    TRAEFIK_CERT_RESOLVER,
    WAHA_CPU_SHARES,
    WAHA_DEFAULT_ENGINE,
    WAHA_IMAGE,
    WAHA_MEM_LIMIT,
    WAHA_MEM_RESERVATION,
)
from .docker_client import get_client
from .logger import get_logger

logger = get_logger("waha")

# Reserva fixa para infra compartilhada.
RESERVED_RAM_MB = 768


def _parse_mem_string(s: str) -> int:
    s = s.strip().lower()
    if s.endswith("g"):
        return int(float(s[:-1]) * 1024)
    if s.endswith("m"):
        return int(s[:-1])
    return int(s) // (1024 * 1024)


PER_WAHA_RAM_MB = _parse_mem_string(WAHA_MEM_LIMIT)
PER_N8N_RAM_MB = _parse_mem_string(INSTANCE_MEM_LIMIT)

_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}[a-z0-9]$")
_VALID_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_waha_instance_name(name: str) -> str:
    name = name.lower().strip()
    if not name or not _VALID_NAME.match(name):
        raise ValueError("Nome deve conter apenas letras minusculas, numeros e hifens (2-32 chars)")
    return name


def validate_waha_version(version: str) -> str:
    version = version.strip()
    if not version or not _VALID_VERSION.match(version):
        raise ValueError(f"Versao WAHA invalida: '{version}'")
    return version


def waha_container_name(instance_name: str) -> str:
    return f"waha-{instance_name}"


def waha_instance_url(instance_name: str) -> str:
    return f"{PROTOCOL}://{instance_name}-waha.{BASE_DOMAIN}"


def generate_waha_api_key() -> str:
    return secrets.token_hex(16)


def build_waha_env(name: str, api_key: str) -> dict:
    host = f"{name}-waha.{BASE_DOMAIN}"
    return {
        "WAHA_API_KEY": api_key,
        "WAHA_BASE_URL": f"{PROTOCOL}://{host}",
        "WHATSAPP_DEFAULT_ENGINE": WAHA_DEFAULT_ENGINE,
        "WAHA_DASHBOARD_ENABLED": "False",
        "WHATSAPP_SWAGGER_ENABLED": "False",
        "WAHA_PRINT_QR": "False",
    }


def build_waha_traefik_labels(name: str) -> dict:
    host = f"{name}-waha.{BASE_DOMAIN}"
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.waha-{name}.rule": f"Host(`{host}`)",
        f"traefik.http.services.waha-{name}.loadbalancer.server.port": "3000",
        "app.managed": "true",
        "app.type": "waha",
        "app.instance": name,
        "app.created_at": datetime.now(timezone.utc).isoformat(),
    }
    if SSL_ENABLED:
        labels[f"traefik.http.routers.waha-{name}.entrypoints"] = "websecure"
        labels[f"traefik.http.routers.waha-{name}.tls.certresolver"] = TRAEFIK_CERT_RESOLVER
    else:
        labels[f"traefik.http.routers.waha-{name}.entrypoints"] = "web"
    return labels


def create_waha_container(name: str, version: str, api_key: str, created_at: str | None = None):
    client = get_client()
    image_tag = f"{WAHA_IMAGE}:{version}"

    client.images.pull(WAHA_IMAGE, tag=version)

    labels = build_waha_traefik_labels(name)
    if created_at:
        labels["app.created_at"] = created_at

    return client.containers.run(
        image=image_tag,
        name=waha_container_name(name),
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        environment=build_waha_env(name, api_key),
        labels=labels,
        mem_limit=WAHA_MEM_LIMIT,
        mem_reservation=WAHA_MEM_RESERVATION,
        cpu_shares=WAHA_CPU_SHARES,
        volumes={
            f"waha-sessions-{name}": {"bind": "/app/.sessions", "mode": "rw"},
            f"waha-media-{name}": {"bind": "/app/.media", "mode": "rw"},
        },
        network=DOCKER_NETWORK,
    )


def get_waha_container(name: str):
    return get_client().containers.get(waha_container_name(name))


def remove_waha_container(name: str):
    client = get_client()
    container = client.containers.get(waha_container_name(name))
    container.remove(force=True, v=True)

    for volume_name in (f"waha-sessions-{name}", f"waha-media-{name}"):
        try:
            client.volumes.get(volume_name).remove(force=True)
        except Exception as e:
            logger.warning("Falha ao remover volume %s: %s", volume_name, e)


def list_waha_containers() -> list:
    client = get_client()
    containers = client.containers.list(all=True, filters={"label": "app.type=waha"})
    now = datetime.now(timezone.utc)
    result = []
    for c in containers:
        labels = c.labels
        inst = labels.get("app.instance", "")

        created_at_str = labels.get("app.created_at", "")
        created_at = None
        age_days = None
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age_days = (now - created_at).days
            except Exception:
                pass

        if created_at is None:
            try:
                docker_created = c.attrs.get("Created", "")
                if docker_created:
                    created_at = datetime.fromisoformat(docker_created.replace("Z", "+00:00"))
                    age_days = (now - created_at).days
            except Exception:
                pass

        result.append({
            "instance_id": inst,
            "name": inst,
            "status": c.status,
            "url": waha_instance_url(inst),
            "location": "vinhedo",
            "version": c.image.tags[0].split(":")[-1] if c.image.tags else "unknown",
            "container_id": c.short_id,
            "created_at": created_at.isoformat() if created_at else None,
            "age_days": age_days,
        })
    return result


def calculate_waha_capacity() -> dict:
    client = get_client()
    info = client.info()

    total_ram_mb = info["MemTotal"] / (1024 * 1024)
    total_cpus = info["NCPU"]

    n8n_running = len(client.containers.list(filters={"label": "app.type=n8n", "status": "running"}))
    waha_running = len(client.containers.list(filters={"label": "app.type=waha", "status": "running"}))

    used_ram_mb = (n8n_running * PER_N8N_RAM_MB) + (waha_running * PER_WAHA_RAM_MB)
    remaining_ram_mb = max(0, total_ram_mb - RESERVED_RAM_MB - used_ram_mb)

    max_new_waha = int(remaining_ram_mb // PER_WAHA_RAM_MB) if PER_WAHA_RAM_MB else 0

    return {
        "max_instances": waha_running + max_new_waha,
        "active_instances": waha_running,
        "can_create": max_new_waha > 0,
        "instances": list_waha_containers(),
        "vps": {
            "total_ram_mb": round(total_ram_mb),
            "total_cpus": total_cpus,
            "reserved_ram_mb": RESERVED_RAM_MB,
            "per_waha_ram_mb": PER_WAHA_RAM_MB,
            "per_n8n_ram_mb": PER_N8N_RAM_MB,
            "running_n8n": n8n_running,
            "running_waha": waha_running,
            "remaining_ram_mb": round(remaining_ram_mb),
        },
    }
