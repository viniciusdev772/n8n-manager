"""Provisionamento de infraestrutura: Traefik, PostgreSQL, Redis."""

from .config import (
    ACME_EMAIL,
    DOCKER_NETWORK,
    PG_ADMIN_DB,
    PG_PASSWORD,
    PG_USER,
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


def ensure_traefik():
    """Garante que o Traefik está rodando com SSL automático."""
    client = get_client()
    name = "traefik"
    required_ports = {80, 443, 8080}

    try:
        c = client.containers.get(name)
        if c.status == "running":
            return
        # Existe mas parado — tentar iniciar
        try:
            c.start()
            return
        except Exception:
            # Porta ocupada ou outro erro — remover e recriar
            c.remove(force=True)
    except Exception:
        pass

    # Limpar containers que ocupam as portas
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


def ensure_postgres():
    """Garante que o PostgreSQL compartilhado está rodando."""
    client = get_client()
    name = "postgres"
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


def ensure_redis():
    """Garante que o Redis compartilhado está rodando."""
    client = get_client()
    name = "redis"
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

    client.containers.run(
        image="redis:7-alpine",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        command="redis-server --maxmemory 100mb --maxmemory-policy allkeys-lru",
        volumes={"redis-data": {"bind": "/data", "mode": "rw"}},
        mem_limit="128m",
        network=DOCKER_NETWORK,
    )
    print("[INFRA] Redis criado e iniciado")


def bootstrap_infra():
    """Provisiona toda a infraestrutura base."""
    ensure_network()
    ensure_traefik()
    ensure_postgres()
    ensure_redis()
