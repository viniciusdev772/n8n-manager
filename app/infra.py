"""Provisionamento de infraestrutura: Traefik, PostgreSQL, Redis, RabbitMQ."""

import os
import socket
import subprocess
import time

import docker

from .config import (
    DOCKER_NETWORK,
    PG_ADMIN_DB,
    PG_PASSWORD,
    PG_PORT,
    PG_USER,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
    REDIS_PORT,
)
from .docker_client import get_client


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
                        print(f"[INFRA] Removendo container '{container.name}' (ocupa porta {host_port})")
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
    print(f"[INFRA] Container '{container.name}' conectado na rede '{network_name}'")


# ─── Network ─────────────────────────────────────────────


def ensure_network():
    """Cria a rede Docker compartilhada se não existir."""
    client = get_client()
    try:
        client.networks.get(DOCKER_NETWORK)
    except Exception:
        client.networks.create(DOCKER_NETWORK, driver="bridge")
        print(f"[INFRA] Rede '{DOCKER_NETWORK}' criada")


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
            print(f"[INFRA] Removendo container Traefik orfao (status: {c.status})")
            c.remove(force=True)
    except docker.errors.NotFound:
        pass


def _run_config_traefik():
    """Executa config_traefik.py para criar/atualizar Traefik via docker compose."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(project_root, "config_traefik.py")

    if not os.path.exists(script):
        print(f"[INFRA] AVISO: {script} nao encontrado, pulando config_traefik")
        return False

    try:
        result = subprocess.run(
            ["python3", script],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"[INFRA] config_traefik.py stderr: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"[INFRA] Erro ao executar config_traefik.py: {e}")
        return False


def ensure_traefik():
    """Detecta Traefik existente ou cria um novo via docker compose."""
    client = get_client()

    # 1. Buscar Traefik já rodando (EasyPanel, docker-compose, etc.)
    existing = _find_running_traefik(client)

    if existing:
        nets = _container_networks(existing)
        if DOCKER_NETWORK in nets:
            print(f"[INFRA] Traefik existente '{existing.name}' ja esta na rede '{DOCKER_NETWORK}'")
        else:
            try:
                _connect_to_network(existing, DOCKER_NETWORK)
                print(f"[INFRA] Traefik existente '{existing.name}' conectado na rede '{DOCKER_NETWORK}'")
            except Exception as e:
                print(f"[INFRA] AVISO: Nao foi possivel conectar Traefik '{existing.name}' na rede: {e}")
        # Limpar container órfão nosso se existir
        if existing.name != "traefik":
            _cleanup_orphan_traefik(client)
        return

    # 2. Nenhum Traefik encontrado — criar via config_traefik.py (docker compose)
    print("[INFRA] Nenhum Traefik encontrado. Criando via docker compose...")
    _cleanup_orphan_traefik(client)
    _kill_port_holders(client, {80, 443})
    _run_config_traefik()
    print("[INFRA] Traefik criado com Cloudflare DNS Challenge")


# ─── PostgreSQL ──────────────────────────────────────────


def _test_pg_connection():
    """Testa conexao com PostgreSQL. Retorna True se OK."""
    try:
        import psycopg2

        conn = psycopg2.connect(
            host="127.0.0.1",
            port=PG_PORT,
            user=PG_USER,
            password=PG_PASSWORD,
            dbname=PG_ADMIN_DB,
            connect_timeout=5,
        )
        conn.close()
        return True
    except Exception as e:
        print(f"[INFRA] PostgreSQL conexao falhou: {e}")
        return False


def ensure_postgres():
    """Garante que o PostgreSQL compartilhado está rodando com a senha correta."""
    client = get_client()
    name = "postgres"

    try:
        c = client.containers.get(name)
        if c.status == "running":
            time.sleep(1)
            if _test_pg_connection():
                return
            print("[INFRA] PostgreSQL: senha do .env nao bate com o volume. Recriando...")
            c.remove(force=True)
            try:
                client.volumes.get("pg-data").remove(force=True)
                print("[INFRA] Volume pg-data removido para reset de senha")
            except Exception:
                pass
        else:
            try:
                c.start()
                time.sleep(2)
                if _test_pg_connection():
                    return
            except Exception:
                pass
            c.remove(force=True)
    except docker.errors.NotFound:
        pass

    _kill_port_holders(client, {5432})

    client.containers.run(
        image="postgres:16-alpine",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        environment={
            "POSTGRES_USER": PG_USER,
            "POSTGRES_PASSWORD": PG_PASSWORD,
            "POSTGRES_DB": PG_ADMIN_DB,
        },
        ports={"5432/tcp": 5432},
        volumes={"pg-data": {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
        mem_limit="512m",
        network=DOCKER_NETWORK,
    )
    print("[INFRA] PostgreSQL criado e iniciado")

    for i in range(15):
        time.sleep(2)
        if _test_pg_connection():
            print("[INFRA] PostgreSQL: conexao verificada OK")
            return
    print("[INFRA] AVISO: PostgreSQL criado mas conexao nao confirmada")


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
            print("[INFRA] Redis: recriando com porta exposta no host...")
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
    print("[INFRA] Redis criado e iniciado")


# ─── RabbitMQ ────────────────────────────────────────────


def ensure_rabbitmq():
    """Garante que o RabbitMQ está rodando com management UI."""
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
    print("[INFRA] RabbitMQ criado e iniciado")

    for i in range(15):
        time.sleep(2)
        if _test_port("127.0.0.1", RABBITMQ_PORT):
            print("[INFRA] RabbitMQ: conexao verificada OK")
            return
    print("[INFRA] AVISO: RabbitMQ criado mas conexao nao confirmada")


# ─── Bootstrap ───────────────────────────────────────────


def bootstrap_infra():
    """Provisiona toda a infraestrutura base."""
    for label, fn in [
        ("network", ensure_network),
        ("traefik", ensure_traefik),
        ("redis", ensure_redis),
        ("rabbitmq", ensure_rabbitmq),
    ]:
        try:
            fn()
        except Exception as e:
            print(f"[INFRA] ERRO em {label}: {e}. Continuando...")
