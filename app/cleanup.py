"""Auto-cleanup — remove instâncias N8N com mais de MAX_AGE_DAYS dias."""

import threading

from .config import CLEANUP_INTERVAL_SECONDS, CLEANUP_MAX_AGE_DAYS
from .logger import get_logger
from .n8n import list_n8n_containers, remove_container

logger = get_logger("cleanup")

_stop_event = threading.Event()


def _run_cleanup():
    """Verifica e remove instâncias expiradas."""
    now = datetime.now(timezone.utc)
    containers = list_n8n_containers()
    removed = 0

    for c in containers:
        age = c.get("age_days")
        name = c.get("instance_id", "")

        if age is not None and age >= CLEANUP_MAX_AGE_DAYS:
            try:
                remove_container(name)
                removed += 1
                logger.info("Instancia '%s' removida (idade: %d dias)", name, age)
            except Exception as e:
                logger.error("Erro ao remover '%s': %s", name, e)

    if removed:
        logger.info("%d instancia(s) removida(s)", removed)
    else:
        logger.info("Nenhuma instancia expirada (%d ativas)", len(containers))


def _cleanup_loop():
    """Loop principal — executa cleanup a cada CHECK_INTERVAL."""
    # Primeira execução após 60s (não bloquear startup)
    _stop_event.wait(60)

    while not _stop_event.is_set():
        try:
            _run_cleanup()
        except Exception as e:
            logger.error("Erro inesperado: %s", e)

        _stop_event.wait(CLEANUP_INTERVAL_SECONDS)


def start_cleanup() -> threading.Thread:
    """Inicia thread daemon do cleanup."""
    t = threading.Thread(target=_cleanup_loop, daemon=True, name="instance-cleanup")
    t.start()
    logger.info("Thread de cleanup iniciada (intervalo: %ds, max_age: %d dias)", CLEANUP_INTERVAL_SECONDS, CLEANUP_MAX_AGE_DAYS)
    return t


def stop_cleanup():
    """Sinaliza o cleanup para parar."""
    _stop_event.set()
    logger.info("Sinal de parada enviado")
