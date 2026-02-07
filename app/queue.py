"""Publisher RabbitMQ — enfileira jobs de criacao de instancia."""

import json

import pika

from .config import RABBITMQ_HOST, RABBITMQ_PASSWORD, RABBITMQ_PORT, RABBITMQ_USER

QUEUE_NAME = "instance_creation"

_connection = None
_channel = None


def get_channel():
    """Retorna canal RabbitMQ, reconectando se necessario."""
    global _connection, _channel
    if _connection is None or _connection.is_closed:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
        _connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300,
            )
        )
        _channel = _connection.channel()
        _channel.queue_declare(queue=QUEUE_NAME, durable=True)
    return _channel


def publish_job(job_id: str, payload: dict):
    """Publica job na fila (persistente — sobrevive restart do broker)."""
    channel = get_channel()
    channel.basic_publish(
        exchange="",
        routing_key=QUEUE_NAME,
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2),  # persistent
    )
    print(f"[QUEUE] Job {job_id} publicado na fila '{QUEUE_NAME}'")


def close_rabbitmq():
    """Fecha conexao com RabbitMQ."""
    global _connection
    if _connection and not _connection.is_closed:
        try:
            _connection.close()
        except Exception:
            pass
    _connection = None
