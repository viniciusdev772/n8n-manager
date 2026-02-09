"""Provisionamento de infraestrutura: Traefik, Redis, RabbitMQ, Fallback."""

import os
import socket
import subprocess
import time

import docker

from .config import (
    BASE_DOMAIN,
    DOCKER_NETWORK,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
    REDIS_PORT,
    TRAEFIK_CERT_RESOLVER,
)
from .docker_client import get_client
from .logger import get_logger

logger = get_logger("infra")


# ─── Helpers ──────────────────────────────────────────────


def _kill_port_holders(client, ports):
    """Remove containers que estejam ocupando as portas necessarias."""
    for container in client.containers.list(all=True):
        try:
            bindings = container.attrs.get("HostConfig", {}).get("PortBindings") or {}
            for _, host_binds in bindings.items():
                if not host_binds:
                    continue
                for bind in host_binds:
                    host_port = int(bind.get("HostPort", 0))
                    if host_port in ports:
                        logger.info("Removendo container '%s' (ocupa porta %d)", container.name, host_port)
                        container.remove(force=True)
                        break
        except Exception:
            continue


def _test_port(host, port, timeout=3):
    """Testa se uma porta TCP esta acessivel."""
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _has_host_port(container, port):
    """Verifica se um container expoe uma porta no host."""
    try:
        bindings = container.attrs.get("HostConfig", {}).get("PortBindings") or {}
        for _, host_binds in bindings.items():
            if not host_binds:
                continue
            for bind in host_binds:
                if int(bind.get("HostPort", 0)) == port:
                    return True
    except Exception:
        pass
    return False


def _container_networks(container):
    """Retorna set de nomes de redes do container."""
    container.reload()
    return set(container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys())


def _connect_to_network(container, network_name):
    """Conecta container a uma rede Docker se não estiver nela."""
    if network_name in _container_networks(container):
        return
    client = get_client()
    network = client.networks.get(network_name)
    network.connect(container)
    logger.info("Container '%s' conectado na rede '%s'", container.name, network_name)


# ─── Network ─────────────────────────────────────────────


def ensure_network():
    """Cria a rede Docker compartilhada se não existir."""
    client = get_client()
    try:
        client.networks.get(DOCKER_NETWORK)
    except Exception:
        client.networks.create(DOCKER_NETWORK, driver="bridge")
        logger.info("Rede '%s' criada", DOCKER_NETWORK)


# ─── Traefik ─────────────────────────────────────────────


def _find_running_traefik(client):
    """Busca qualquer container Traefik já rodando no host."""
    for c in client.containers.list(filters={"status": "running"}):
        # Checar image tags
        for tag in (c.image.tags or []):
            if "traefik" in tag.lower():
                return c
        # Fallback: checar nome do container
        if "traefik" in c.name.lower():
            return c
    return None


def _cleanup_orphan_traefik(client):
    """Remove container 'traefik' órfão (nosso antigo) se existir parado/created."""
    try:
        c = client.containers.get("traefik")
        if c.status != "running":
            logger.info("Removendo container Traefik orfao (status: %s)", c.status)
            c.remove(force=True)
    except docker.errors.NotFound:
        pass


def _run_config_traefik():
    """Executa config_traefik.py para criar/atualizar Traefik via docker compose."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(project_root, "config_traefik.py")

    if not os.path.exists(script):
        logger.warning("AVISO: %s nao encontrado, pulando config_traefik", script)
        return False

    try:
        result = subprocess.run(
            ["python3", script],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        logger.info(result.stdout)
        if result.returncode != 0:
            logger.error("config_traefik.py stderr: %s", result.stderr)
            return False
        return True
    except Exception as e:
        logger.error("Erro ao executar config_traefik.py: %s", e)
        return False


def ensure_traefik():
    """Detecta Traefik existente ou cria um novo via docker compose."""
    client = get_client()

    # 1. Buscar Traefik já rodando (EasyPanel, docker-compose, etc.)
    existing = _find_running_traefik(client)

    if existing:
        nets = _container_networks(existing)
        if DOCKER_NETWORK in nets:
            logger.info("Traefik existente '%s' ja esta na rede '%s'", existing.name, DOCKER_NETWORK)
        else:
            try:
                _connect_to_network(existing, DOCKER_NETWORK)
                logger.info("Traefik existente '%s' conectado na rede '%s'", existing.name, DOCKER_NETWORK)
            except Exception as e:
                logger.warning("Nao foi possivel conectar Traefik '%s' na rede: %s", existing.name, e)
        # Limpar container órfão nosso se existir
        if existing.name != "traefik":
            _cleanup_orphan_traefik(client)
        return

    # 2. Nenhum Traefik encontrado — criar via config_traefik.py (docker compose)
    logger.info("Nenhum Traefik encontrado. Criando via docker compose...")
    _cleanup_orphan_traefik(client)
    _kill_port_holders(client, {80, 443})
    _run_config_traefik()
    logger.info("Traefik criado com Cloudflare DNS Challenge")


# ─── Redis ───────────────────────────────────────────────


def ensure_redis():
    """Garante que o Redis compartilhado está rodando com porta exposta."""
    client = get_client()
    name = "redis"
    try:
        c = client.containers.get(name)
        if c.status == "running":
            if _has_host_port(c, REDIS_PORT):
                return
            logger.info("Redis: recriando com porta exposta no host...")
            c.remove(force=True)
        else:
            try:
                c.start()
                if _has_host_port(c, REDIS_PORT):
                    return
            except Exception:
                pass
            c.remove(force=True)
    except docker.errors.NotFound:
        pass

    _kill_port_holders(client, {REDIS_PORT})

    client.containers.run(
        image="redis:7-alpine",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        command="redis-server --maxmemory 100mb --maxmemory-policy allkeys-lru",
        ports={"6379/tcp": REDIS_PORT},
        volumes={"redis-data": {"bind": "/data", "mode": "rw"}},
        mem_limit="128m",
        network=DOCKER_NETWORK,
    )
    logger.info("Redis criado e iniciado")


# ─── RabbitMQ ────────────────────────────────────────────


def ensure_rabbitmq():
    """Garante que o RabbitMQ está rodando com management UI."""
    if not RABBITMQ_USER or not RABBITMQ_PASSWORD:
        logger.warning("RABBITMQ_USER/RABBITMQ_PASSWORD nao configurados no .env")
    client = get_client()
    name = "rabbitmq"
    try:
        c = client.containers.get(name)
        if c.status == "running":
            return
        try:
            c.start()
            return
        except Exception:
            c.remove(force=True)
    except docker.errors.NotFound:
        pass

    _kill_port_holders(client, {RABBITMQ_PORT, 15672})

    client.containers.run(
        image="rabbitmq:3-management-alpine",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        ports={"5672/tcp": RABBITMQ_PORT, "15672/tcp": 15672},
        environment={
            "RABBITMQ_DEFAULT_USER": RABBITMQ_USER,
            "RABBITMQ_DEFAULT_PASS": RABBITMQ_PASSWORD,
        },
        volumes={"rabbitmq-data": {"bind": "/var/lib/rabbitmq", "mode": "rw"}},
        mem_limit="256m",
        network=DOCKER_NETWORK,
    )
    logger.info("RabbitMQ criado e iniciado")

    for i in range(15):
        time.sleep(2)
        if _test_port("127.0.0.1", RABBITMQ_PORT):
            logger.info("RabbitMQ: conexao verificada OK")
            return
    logger.warning("RabbitMQ criado mas conexao nao confirmada")


# ─── Fallback (página "instância removida") ─────────────


def ensure_fallback():
    """Garante que o container fallback está rodando para subdomínios sem instância."""
    client = get_client()
    name = "n8n-fallback"

    # Caminho dos arquivos de fallback
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fallback_dir = os.path.join(project_root, "fallback")
    html_path = os.path.join(fallback_dir, "index.html")
    nginx_conf_path = os.path.join(fallback_dir, "nginx.conf")

    if not os.path.exists(html_path):
        logger.warning("AVISO: %s nao encontrado, pulando fallback", html_path)
        return

    try:
        c = client.containers.get(name)
        if c.status == "running":
            # Verificar se está na rede correta
            if DOCKER_NETWORK not in _container_networks(c):
                _connect_to_network(c, DOCKER_NETWORK)
            logger.info("Fallback '%s' ja esta rodando", name)
            return
        # Parado — remover e recriar
        c.remove(force=True)
    except docker.errors.NotFound:
        pass

    # Labels Traefik: catchall para *.n8n.marketcodebrasil.com.br com prioridade baixa
    # Traefik v3 usa regex puro (sem named groups do v2)
    # Dots no domínio precisam ser escapados como \. para regex
    escaped_domain = BASE_DOMAIN.replace(".", "\\.")
    rule = f"HostRegexp(`[a-z0-9-]+\\.{escaped_domain}`)"
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.{name}.rule": rule,
        f"traefik.http.routers.{name}.entrypoints": "websecure",
        f"traefik.http.routers.{name}.tls.certresolver": TRAEFIK_CERT_RESOLVER,
        f"traefik.http.routers.{name}.priority": "1",
        f"traefik.http.services.{name}.loadbalancer.server.port": "80",
        "app.managed": "true",
        "app.type": "fallback",
    }

    client.containers.run(
        image="nginx:alpine",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        labels=labels,
        volumes={
            html_path: {"bind": "/usr/share/nginx/html/index.html", "mode": "ro"},
            nginx_conf_path: {"bind": "/etc/nginx/conf.d/default.conf", "mode": "ro"},
        },
        mem_limit="32m",
        cpu_shares=128,
        network=DOCKER_NETWORK,
    )
    logger.info("Fallback '%s' criado (catchall para subdominios sem instancia)", name)


# ─── Pre-pull ───────────────────────────────────────────


def _pre_pull_n8n_image():
    """Pré-baixa a imagem N8N para acelerar criação de instâncias."""
    from .config import DEFAULT_N8N_VERSION, N8N_IMAGE

    client = get_client()
    image_tag = f"{N8N_IMAGE}:{DEFAULT_N8N_VERSION}"
    logger.info("Pre-pull da imagem %s...", image_tag)
    client.images.pull(N8N_IMAGE, tag=DEFAULT_N8N_VERSION)
    logger.info("Imagem %s pronta", image_tag)


# ─── Bootstrap ───────────────────────────────────────────


def bootstrap_infra():
    """Provisiona toda a infraestrutura base."""
    for label, fn in [
        ("network", ensure_network),
        ("traefik", ensure_traefik),
        ("redis", ensure_redis),
        ("rabbitmq", ensure_rabbitmq),
        ("fallback", ensure_fallback),
        ("image-pull", _pre_pull_n8n_image),
    ]:
        try:
            fn()
        except Exception as e:
            logger.error("ERRO em %s: %s. Continuando...", label, e)
