"""Provisionamento de infraestrutura: Traefik, PostgreSQL, Redis, RabbitMQ."""

import socket
import time

from .config import (
    ACME_EMAIL,
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


def ensure_network():
    """Cria a rede Docker compartilhada se não existir."""
    client = get_client()
    try:
        client.networks.get(DOCKER_NETWORK)
    except Exception:
        client.networks.create(DOCKER_NETWORK, driver="bridge")
        print(f"[INFRA] Rede '{DOCKER_NETWORK}' criada")


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


def _container_on_network(container, network_name):
    """Verifica se um container esta numa rede."""
    container.reload()
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    return network_name in networks


def ensure_traefik():
    """Garante que o Traefik está rodando com SSL automático na rede correta."""
    client = get_client()
    name = "traefik"
    required_ports = {80, 443, 8080}

    try:
        c = client.containers.get(name)
        if c.status == "running":
            if _container_on_network(c, DOCKER_NETWORK):
                return
            # Traefik rodando mas fora da rede — recriar
            print(f"[INFRA] Traefik fora da rede '{DOCKER_NETWORK}', recriando...")
            c.remove(force=True)
        else:
            c.remove(force=True)
    except Exception:
        pass

    _kill_port_holders(client, required_ports)

    client.containers.run(
        image="traefik:v3.3",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        ports={"80/tcp": 80, "443/tcp": 443, "8080/tcp": 8080},
        volumes={
            "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "ro"},
            "traefik-certs": {"bind": "/certs", "mode": "rw"},
        },
        command=[
            "--api.dashboard=true",
            "--api.insecure=true",
            "--providers.docker=true",
            "--providers.docker.exposedbydefault=false",
            f"--providers.docker.network={DOCKER_NETWORK}",
            "--entrypoints.web.address=:80",
            "--entrypoints.websecure.address=:443",
            "--entrypoints.web.http.redirections.entrypoint.to=websecure",
            "--entrypoints.web.http.redirections.entrypoint.scheme=https",
            "--certificatesresolvers.letsencrypt.acme.tlschallenge=true",
            f"--certificatesresolvers.letsencrypt.acme.email={ACME_EMAIL}",
            "--certificatesresolvers.letsencrypt.acme.storage=/certs/acme.json",
        ],
        labels={"app.managed": "true"},
        network=DOCKER_NETWORK,
    )
    print("[INFRA] Traefik criado e iniciado")


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
    created = False

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
    except Exception:
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
    created = True
    print("[INFRA] PostgreSQL criado e iniciado")

    for i in range(15):
        time.sleep(2)
        if _test_pg_connection():
            print("[INFRA] PostgreSQL: conexao verificada OK")
            return
    if created:
        print("[INFRA] AVISO: PostgreSQL criado mas conexao nao confirmada")


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


def ensure_redis():
    """Garante que o Redis compartilhado está rodando com porta exposta."""
    client = get_client()
    name = "redis"
    try:
        c = client.containers.get(name)
        if c.status == "running":
            # Verificar se porta 6379 esta exposta no host
            if _has_host_port(c, REDIS_PORT):
                return
            # Container sem port mapping — recriar
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
    except Exception:
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
    except Exception:
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

    # Aguardar RabbitMQ ficar pronto
    for i in range(15):
        time.sleep(2)
        if _test_port("127.0.0.1", RABBITMQ_PORT):
            print("[INFRA] RabbitMQ: conexao verificada OK")
            return
    print("[INFRA] AVISO: RabbitMQ criado mas conexao nao confirmada")


def bootstrap_infra():
    """Provisiona toda a infraestrutura base."""
    ensure_network()
    ensure_traefik()
    ensure_postgres()
    ensure_redis()
    ensure_rabbitmq()
