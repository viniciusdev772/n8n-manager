"""Auto-cleanup — remove instâncias N8N com mais de 5 dias."""

import threading
from datetime import datetime, timezone

from .n8n import list_n8n_containers, remove_container

MAX_AGE_DAYS = 5
CHECK_INTERVAL = 3600  # 1 hora

_stop_event = threading.Event()


def _run_cleanup():
    """Verifica e remove instâncias expiradas."""
    now = datetime.now(timezone.utc)
    containers = list_n8n_containers()
    removed = 0

    for c in containers:
        age = c.get("age_days")
        name = c.get("instance_id", "")

        if age is not None and age >= MAX_AGE_DAYS:
            try:
                remove_container(name)
                removed += 1
                print(f"[CLEANUP] Instância '{name}' removida (idade: {age} dias)")
            except Exception as e:
                print(f"[CLEANUP] Erro ao remover '{name}': {e}")

    if removed:
        print(f"[CLEANUP] {removed} instância(s) removida(s)")
    else:
        print(f"[CLEANUP] Nenhuma instância expirada ({len(containers)} ativas)")


def _cleanup_loop():
    """Loop principal — executa cleanup a cada CHECK_INTERVAL."""
    # Primeira execução após 60s (não bloquear startup)
    _stop_event.wait(60)

    while not _stop_event.is_set():
        try:
            _run_cleanup()
        except Exception as e:
            print(f"[CLEANUP] Erro inesperado: {e}")

        _stop_event.wait(CHECK_INTERVAL)


def start_cleanup() -> threading.Thread:
    """Inicia thread daemon do cleanup."""
    t = threading.Thread(target=_cleanup_loop, daemon=True, name="instance-cleanup")
    t.start()
    print(f"[CLEANUP] Thread de cleanup iniciada (intervalo: {CHECK_INTERVAL}s, max_age: {MAX_AGE_DAYS} dias)")
    return t


def stop_cleanup():
    """Sinaliza o cleanup para parar."""
    _stop_event.set()
    print("[CLEANUP] Sinal de parada enviado")
