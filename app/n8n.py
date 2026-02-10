"""Lógica de criação e configuração de containers N8N."""

import re
import secrets
from datetime import datetime, timezone

from .config import (
    BASE_DOMAIN,
    DEFAULT_TIMEZONE,
    DOCKER_NETWORK,
    INSTANCE_CPU_SHARES,
    INSTANCE_MEM_LIMIT,
    INSTANCE_MEM_RESERVATION,
    N8N_IMAGE,
    PROTOCOL,
    SSL_ENABLED,
    TRAEFIK_CERT_RESOLVER,
)
from .docker_client import get_client
from .logger import get_logger

logger = get_logger("n8n")

# Recursos reservados para infra (Traefik ~50 + Redis ~100 + RabbitMQ ~150 + OS ~200 + margem)
RESERVED_RAM_MB = 768
PER_INSTANCE_RAM_MB = 384


_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}[a-z0-9]$")
_VALID_VERSION = re.compile(r"^(latest|1\.\d{1,3}\.\d{1,3})$")


def validate_instance_name(name: str) -> str:
    """Valida e normaliza nome de instância."""
    name = name.lower().strip()
    if not name or not _VALID_NAME.match(name):
        raise ValueError("Nome deve conter apenas letras minusculas, numeros e hifens (2-32 chars)")
    return name


def validate_version(version: str) -> str:
    """Valida formato da versão N8N."""
    version = version.strip()
    if not _VALID_VERSION.match(version):
        raise ValueError(f"Versao invalida: '{version}'. Use formato 1.X.Y ou 'latest'")
    return version


def container_name(instance_name: str) -> str:
    return f"n8n-{instance_name}"


def instance_url(instance_name: str) -> str:
    return f"{PROTOCOL}://{instance_name}.{BASE_DOMAIN}"


def generate_encryption_key() -> str:
    return secrets.token_hex(32)


def build_env(name: str, encryption_key: str) -> dict:
    """Variáveis de ambiente para instância N8N (SQLite embutido)."""
    host = f"{name}.{BASE_DOMAIN}"

    return {
        "N8N_HOST": "0.0.0.0",
        "N8N_PORT": "5678",
        "N8N_PROTOCOL": PROTOCOL,
        "N8N_EDITOR_BASE_URL": f"{PROTOCOL}://{host}/",
        "N8N_ENCRYPTION_KEY": encryption_key,
        "WEBHOOK_URL": f"{PROTOCOL}://{host}/",
        "GENERIC_TIMEZONE": DEFAULT_TIMEZONE,
        "N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS": "true",
        "N8N_SECURE_COOKIE": "true" if SSL_ENABLED else "false",
        "N8N_LOG_LEVEL": "warn",
        # SQLite pool (elimina deprecation warning)
        "DB_SQLITE_POOL_SIZE": "4",
        # Desabilitar telemetria (reduz startup e erros de DNS)
        "N8N_DIAGNOSTICS_ENABLED": "false",
        # Segurança recomendada pelo n8n
        "N8N_BLOCK_ENV_ACCESS_IN_NODE": "true",
        "N8N_GIT_NODE_DISABLE_BARE_REPOS": "true",
        # Economia de execuções
        "EXECUTIONS_DATA_SAVE_ON_ERROR": "all",
        "EXECUTIONS_DATA_SAVE_ON_SUCCESS": "none",
        "EXECUTIONS_DATA_SAVE_ON_PROGRESS": "false",
        "EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS": "false",
        "EXECUTIONS_DATA_PRUNE": "true",
        "EXECUTIONS_DATA_MAX_AGE": "24",
        "EXECUTIONS_DATA_PRUNE_MAX_COUNT": "100",
        # Performance
        "N8N_CONCURRENCY_PRODUCTION_LIMIT": "3",
        "NODE_OPTIONS": "--max-old-space-size=256",
        # Desabilitar features desnecessárias (economia de memória + segurança)
        "N8N_TEMPLATES_ENABLED": "false",
        "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
        "N8N_PERSONALIZATION_ENABLED": "false",
        "N8N_HIRING_BANNER_ENABLED": "false",
        "N8N_COMMUNITY_PACKAGES_ENABLED": "true",
    }


def build_traefik_labels(name: str) -> dict:
    """Labels para roteamento automático do Traefik (com ou sem SSL)."""
    host = f"{name}.{BASE_DOMAIN}"
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.n8n-{name}.rule": f"Host(`{host}`)",
        f"traefik.http.services.n8n-{name}.loadbalancer.server.port": "5678",
        "app.managed": "true",
        "app.type": "n8n",
        "app.instance": name,
        "app.created_at": datetime.now(timezone.utc).isoformat(),
    }
    if SSL_ENABLED:
        labels[f"traefik.http.routers.n8n-{name}.entrypoints"] = "websecure"
        labels[f"traefik.http.routers.n8n-{name}.tls.certresolver"] = TRAEFIK_CERT_RESOLVER
    else:
        labels[f"traefik.http.routers.n8n-{name}.entrypoints"] = "web"
    return labels


def create_container(name: str, version: str, encryption_key: str, created_at: str | None = None):
    """Cria e inicia um container N8N."""
    client = get_client()
    image_tag = f"{N8N_IMAGE}:{version}"

    client.images.pull(N8N_IMAGE, tag=version)

    env = build_env(name, encryption_key)
    labels = build_traefik_labels(name)
    if created_at:
        labels["app.created_at"] = created_at

    return client.containers.run(
        image=image_tag,
        name=container_name(name),
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        environment=env,
        labels=labels,
        mem_limit=INSTANCE_MEM_LIMIT,
        mem_reservation=INSTANCE_MEM_RESERVATION,
        cpu_shares=INSTANCE_CPU_SHARES,
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
    except Exception as e:
        logger.warning("Falha ao remover volume n8n-data-%s: %s", name, e)


def list_n8n_containers() -> list:
    """Lista todos os containers N8N gerenciados."""
    client = get_client()
    containers = client.containers.list(
        all=True, filters={"label": "app.type=n8n"}
    )
    now = datetime.now(timezone.utc)
    result = []
    for c in containers:
        labels = c.labels
        inst = labels.get("app.instance", "")

        # Idade da instância via label ou fallback para Docker Created
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

        # Fallback: usar Docker Created timestamp
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
            "url": instance_url(inst),
            "location": "vinhedo",
            "version": c.image.tags[0].split(":")[-1] if c.image.tags else "unknown",
            "container_id": c.short_id,
            "created_at": created_at.isoformat() if created_at else None,
            "age_days": age_days,
        })
    return result


def extract_encryption_key(container) -> str:
    """Extrai N8N_ENCRYPTION_KEY do container existente."""
    for e in container.attrs.get("Config", {}).get("Env", []):
        k, _, v = e.partition("=")
        if k == "N8N_ENCRYPTION_KEY":
            return v
    return ""


def _get_container_env(container) -> dict:
    """Retorna dict de env vars do container."""
    env = {}
    for e in container.attrs.get("Config", {}).get("Env", []):
        k, _, v = e.partition("=")
        env[k] = v
    return env


def rebuild_container(instance_id: str, version: str):
    """Recria container com env vars atuais, preservando encryption key e volume."""
    old = get_container(instance_id)
    encryption_key = extract_encryption_key(old)
    if not encryption_key:
        raise RuntimeError(f"N8N_ENCRYPTION_KEY não encontrada no container {instance_id}")

    old_created_at = old.labels.get("app.created_at")
    old.remove(force=True)  # Mantém volume

    return create_container(instance_id, version, encryption_key, created_at=old_created_at)


def sync_instance_env_vars():
    """Verifica todas instâncias e recria as que têm env vars desatualizadas."""
    client = get_client()
    containers = client.containers.list(
        all=True, filters={"label": "app.type=n8n"}
    )

    synced = 0
    for c in containers:
        inst = c.labels.get("app.instance", "")
        if not inst:
            continue

        try:
            encryption_key = extract_encryption_key(c)
            if not encryption_key:
                logger.warning("%s: sem encryption key, pulando", inst)
                continue

            expected_env = build_env(inst, encryption_key)
            current_env = _get_container_env(c)

            # Comparar apenas as chaves que build_env define
            needs_rebuild = False
            for key, expected_val in expected_env.items():
                if current_env.get(key) != expected_val:
                    logger.info("%s: env '%s' diverge", inst, key)
                    needs_rebuild = True
                    break

            if needs_rebuild:
                version = c.image.tags[0].split(":")[-1] if c.image.tags else "latest"
                logger.info("Recriando container %s (versao %s)...", inst, version)
                rebuild_container(inst, version)
                synced += 1
                logger.info("%s recriado com env vars atualizadas", inst)
            else:
                logger.debug("%s: env vars OK", inst)
        except Exception as e:
            logger.error("ERRO em %s: %s. Continuando...", inst, e)

    logger.info("Sync concluido: %d instancia(s) recriada(s)", synced)


def calculate_max_instances() -> dict:
    """Calcula o número máximo de instâncias com base na RAM da VPS.

    Com cpu_shares (peso relativo), CPU não é gargalo — capacidade é só por RAM.
    """
    client = get_client()
    info = client.info()

    total_ram_mb = info["MemTotal"] / (1024 * 1024)
    total_cpus = info["NCPU"]

    available_ram = total_ram_mb - RESERVED_RAM_MB
    max_instances = max(1, int(available_ram / PER_INSTANCE_RAM_MB))

    current = list_n8n_containers()
    active_count = len([c for c in current if c["status"] == "running"])

    return {
        "max_instances": max_instances,
        "active_instances": active_count,
        "can_create": active_count < max_instances,
        "instances": current,
        "vps": {
            "total_ram_mb": round(total_ram_mb),
            "total_cpus": total_cpus,
            "reserved_ram_mb": RESERVED_RAM_MB,
            "per_instance_ram_mb": PER_INSTANCE_RAM_MB,
        },
    }
