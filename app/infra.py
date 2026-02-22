"""Provisionamento de infraestrutura: Traefik, Redis, RabbitMQ, Fallback."""

import os
import socket
import subprocess
import time
import hashlib

import docker

from .config import (
    DOCKER_NETWORK,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
    REDIS_PORT,
    SSL_ENABLED,
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


def _run_parser_api_compose():
    """Sobe/atualiza a parser-api dedicada via docker compose."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    compose_file = os.path.join(project_root, "docker-compose.parser-api.yml")
    env_file = os.path.join(project_root, ".env")
    build_hash_file = os.path.join(project_root, ".parser_api_build_hash")

    def _read_file_sha256(path):
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _compute_parser_source_hash():
        tracked_files = [
            os.path.join(project_root, "parser.py"),
            os.path.join(project_root, "requirements.txt"),
            os.path.join(project_root, "Dockerfile.parser-api"),
            compose_file,
        ]
        hasher = hashlib.sha256()
        for path in tracked_files:
            if not os.path.exists(path):
                return None
            file_hash = _read_file_sha256(path)
            hasher.update(path.encode("utf-8"))
            hasher.update(file_hash.encode("utf-8"))
        return hasher.hexdigest()

    if not os.path.exists(compose_file):
        logger.warning("AVISO: %s nao encontrado, pulando parser-api", compose_file)
        return False

    current_hash = _compute_parser_source_hash()
    if current_hash is None:
        logger.warning("Arquivos da parser-api incompletos; pulando parser-api")
        return False

    previous_hash = ""
    if os.path.exists(build_hash_file):
        try:
            with open(build_hash_file, "r", encoding="utf-8") as f:
                previous_hash = f.read().strip()
        except Exception:
            previous_hash = ""

    hash_changed = current_hash != previous_hash
    base_cmd = ["docker", "compose", "-f", compose_file]
    if os.path.exists(env_file):
        base_cmd.extend(["--env-file", env_file])

    build_cmd = base_cmd + ["build", "--progress=plain", "parser-api"]
    up_cmd = base_cmd + ["up", "-d", "parser-api"]

    try:
        build_result = subprocess.run(
            build_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if build_result.stdout:
            logger.info(build_result.stdout)
        if build_result.stderr and build_result.returncode == 0:
            logger.warning("parser-api build warnings: %s", build_result.stderr)
        if build_result.returncode != 0:
            if build_result.stdout:
                logger.error("parser-api build stdout (erro): %s", build_result.stdout)
            if build_result.stderr:
                logger.error("parser-api build stderr (erro): %s", build_result.stderr)
            return False

        up_result = subprocess.run(
            up_cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if up_result.stdout:
            logger.info(up_result.stdout)
        if up_result.stderr and up_result.returncode == 0:
            logger.warning("parser-api up warnings: %s", up_result.stderr)
        if up_result.returncode != 0:
            if up_result.stdout:
                logger.error("parser-api up stdout (erro): %s", up_result.stdout)
            if up_result.stderr:
                logger.error("parser-api up stderr (erro): %s", up_result.stderr)
            return False

        try:
            with open(build_hash_file, "w", encoding="utf-8") as f:
                f.write(current_hash)
        except Exception as e:
            logger.warning("Falha ao salvar hash da parser-api: %s", e)

        if hash_changed:
            logger.info("parser-api rebuildada (alteracoes detectadas por hash)")
        else:
            logger.info("parser-api rebuildada (hash sem alteracoes, conforme startup forcado)")
        logger.info("parser-api dedicada pronta")
        return True
    except Exception as e:
        logger.error("Erro ao subir parser-api: %s", e)
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


def _test_rabbitmq_auth():
    """Testa se as credenciais do .env funcionam no RabbitMQ."""
    try:
        import pika
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
        conn = pika.BlockingConnection(
            pika.ConnectionParameters(
                host="127.0.0.1", port=RABBITMQ_PORT,
                credentials=credentials, connection_attempts=1, retry_delay=0,
                socket_timeout=5,
            )
        )
        conn.close()
        return True
    except Exception:
        return False


def ensure_rabbitmq():
    """Garante que o RabbitMQ está rodando com credenciais corretas."""
    if not RABBITMQ_USER or not RABBITMQ_PASSWORD:
        logger.warning("RABBITMQ_USER/RABBITMQ_PASSWORD nao configurados no .env")
    client = get_client()
    name = "rabbitmq"
    recreate = False

    try:
        c = client.containers.get(name)
        if c.status == "running":
            # Testar se as credenciais do .env funcionam
            if _test_rabbitmq_auth():
                return
            # Credenciais nao batem — volume antigo com senha diferente
            logger.warning("RabbitMQ: credenciais do .env nao funcionam. Recriando com volume novo...")
            c.remove(force=True)
            recreate = True
        else:
            try:
                c.start()
                time.sleep(3)
                if _test_rabbitmq_auth():
                    return
            except Exception:
                pass
            c.remove(force=True)
            recreate = True
    except docker.errors.NotFound:
        recreate = True

    # Remover volume antigo se credenciais nao batiam
    if recreate:
        try:
            vol = client.volumes.get("rabbitmq-data")
            vol.remove(force=True)
            logger.info("Volume rabbitmq-data antigo removido (credenciais incompativeis)")
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.warning("Nao foi possivel remover volume rabbitmq-data: %s", e)

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
        if _test_rabbitmq_auth():
            logger.info("RabbitMQ: autenticacao verificada OK")
            return
    logger.warning("RabbitMQ criado mas autenticacao nao confirmada")


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

    # Regra global de fallback para qualquer Host não atendido por rotas de maior prioridade.
    # Prioridade baixa para não competir com as rotas específicas de instâncias.
    rule = "HostRegexp(`^.+$`)"
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.{name}.rule": rule,
        f"traefik.http.routers.{name}.priority": "1",
        f"traefik.http.services.{name}.loadbalancer.server.port": "80",
        "app.managed": "true",
        "app.type": "fallback",
    }
    labels[f"traefik.http.routers.{name}.entrypoints"] = "web,websecure"
    if SSL_ENABLED:
        labels[f"traefik.http.routers.{name}.tls.certresolver"] = TRAEFIK_CERT_RESOLVER

    recreate = False
    try:
        c = client.containers.get(name)
        c.reload()

        # Se estiver parado, recriar.
        if c.status != "running":
            c.remove(force=True)
            recreate = True
        else:
            # Verifica se está na rede correta e com labels/mounts esperados.
            if DOCKER_NETWORK not in _container_networks(c):
                _connect_to_network(c, DOCKER_NETWORK)

            current_labels = c.attrs.get("Config", {}).get("Labels", {}) or {}
            mounts = c.attrs.get("Mounts", [])
            mount_sources = {m.get("Source") for m in mounts}
            expected_sources = {os.path.abspath(fallback_dir), os.path.abspath(nginx_conf_path)}

            labels_ok = all(current_labels.get(k) == v for k, v in labels.items())
            mounts_ok = expected_sources.issubset(mount_sources)
            if labels_ok and mounts_ok:
                logger.info("Fallback '%s' ja esta rodando", name)
                return

            logger.info("Fallback '%s' com configuracao antiga; recriando...", name)
            c.remove(force=True)
            recreate = True
    except docker.errors.NotFound:
        recreate = True

    if not recreate:
        return

    client.containers.run(
        image="nginx:alpine",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        labels=labels,
        volumes={
            os.path.abspath(fallback_dir): {"bind": "/usr/share/nginx/html", "mode": "ro"},
            nginx_conf_path: {"bind": "/etc/nginx/conf.d/default.conf", "mode": "ro"},
        },
        mem_limit="32m",
        cpu_shares=128,
        network=DOCKER_NETWORK,
    )
    logger.info("Fallback '%s' criado (catchall global para hosts sem instancia)", name)


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
        ("parser-api", _run_parser_api_compose),
        ("fallback", ensure_fallback),
        ("image-pull", _pre_pull_n8n_image),
    ]:
        try:
            fn()
        except Exception as e:
            logger.error("ERRO em %s: %s. Continuando...", label, e)
