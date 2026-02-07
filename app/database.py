"""Gerenciamento de databases PostgreSQL por tenant."""

import psycopg2

from .config import PG_ADMIN_DB, PG_PASSWORD, PG_PORT, PG_USER


def _connect():
    return psycopg2.connect(
        host="127.0.0.1",
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        dbname=PG_ADMIN_DB,
    )


def db_name_for(instance_name: str) -> str:
    return f"n8n_{instance_name.replace('-', '_')}"


def create_tenant_db(instance_name: str):
    """Cria um database PostgreSQL isolado para o tenant."""
    name = db_name_for(instance_name)
    conn = _connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{name}" OWNER "{PG_USER}"')
        print(f"[DB] Database '{name}' criado")
    cur.close()
    conn.close()


def drop_tenant_db(instance_name: str):
    """Remove o database do tenant, desconectando sessões ativas."""
    name = db_name_for(instance_name)
    conn = _connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    print(f"[DB] Database '{name}' removido")
    cur.close()
    conn.close()
