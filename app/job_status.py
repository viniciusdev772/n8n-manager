"""Store de status de jobs via Redis — bridge entre worker e SSE endpoint."""

import json

import redis

from .config import JOB_CLEANUP_TTL, JOB_TTL, REDIS_HOST, REDIS_PORT

_pool = None


def get_redis() -> redis.Redis:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool(
            host=REDIS_HOST, port=REDIS_PORT, decode_responses=True,
            socket_connect_timeout=5, retry_on_timeout=True,
            max_connections=10,
        )
    return redis.Redis(connection_pool=_pool)


def init_job(job_id: str):
    """Inicializa um job como pendente."""
    r = get_redis()
    r.set(f"job:{job_id}:state", "pending", ex=JOB_TTL)


def set_state(job_id: str, state: str):
    """Atualiza o estado do job (pending/running/complete/error)."""
    r = get_redis()
    r.set(f"job:{job_id}:state", state, ex=JOB_TTL)


def get_state(job_id: str) -> str:
    """Retorna o estado atual do job."""
    r = get_redis()
    return r.get(f"job:{job_id}:state") or "unknown"


def push_event(job_id: str, event: dict):
    """Adiciona um evento ao histórico do job."""
    r = get_redis()
    r.rpush(f"job:{job_id}:events", json.dumps(event))
    r.expire(f"job:{job_id}:events", JOB_TTL)


def get_events_since(job_id: str, index: int) -> list[dict]:
    """Retorna eventos a partir de um índice (para polling incremental)."""
    r = get_redis()
    raw = r.lrange(f"job:{job_id}:events", index, -1)
    return [json.loads(item) for item in raw]


def cleanup_job(job_id: str):
    """Marca keys do job para expirar em breve (auto-cleanup)."""
    r = get_redis()
    r.expire(f"job:{job_id}:events", JOB_CLEANUP_TTL)
    r.expire(f"job:{job_id}:state", JOB_CLEANUP_TTL)
