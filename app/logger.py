"""Configuração centralizada de logging."""

import logging
import sys


def setup_logging():
    """Configura logging para todos os módulos da aplicação."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
