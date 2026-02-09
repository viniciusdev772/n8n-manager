"""Worker — consome jobs de criacao de instancia do RabbitMQ."""

import json
import ssl
import threading
import time
import urllib.request

import docker
import pika

from .config import (
    RABBITMQ_HOST,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
    READINESS_MAX_ATTEMPTS,
    READINESS_POLL_INTERVAL,
    SSL_WAIT_SECONDS,
)
from .job_status import push_event, set_state
from .logger import get_logger
from .n8n import (
    create_container,
    generate_encryption_key,
    get_container,
    instance_url,
)

logger = get_logger("worker")

QUEUE_NAME = "instance_creation"
_stop_event = threading.Event()


def _process_job(ch, method, properties, body):
    """Executa a criacao de uma instancia N8N."""
    job = json.loads(body)
    job_id = job["job_id"]
    name = job["name"]
    version = job.get("version", "latest")

    logger.info("Processando job %s: instancia '%s' v%s", job_id, name, version)
    set_state(job_id, "running")

    try:
        # 1. Verificar duplicata
        try:
            get_container(name)
            push_event(job_id, {"status": "error", "message": f"Instancia '{name}' ja existe"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        except docker.errors.NotFound:
            pass

        encryption_key = generate_encryption_key()

        # 2. Criar container (pull + run via n8n.create_container)
        push_event(job_id, {"status": "info", "message": "Baixando imagem e criando container..."})
        try:
            ct = create_container(name, version, encryption_key)
        except Exception as e:
            push_event(job_id, {"status": "error", "message": f"Erro ao criar container: {e}"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        push_event(job_id, {"status": "info", "message": "Container criado, aguardando N8N..."})

        # 3. Aguardar startup — testa URL publica via Traefik
        n8n_ready = False
        public_url = instance_url(name)

        for i in range(READINESS_MAX_ATTEMPTS):
            time.sleep(READINESS_POLL_INTERVAL)
            ct.reload()

            if ct.status == "exited":
                logs = ct.logs(tail=30).decode("utf-8", errors="replace")
                push_event(job_id, {"status": "error", "message": f"Container parou.\n{logs}"})
                set_state(job_id, "error")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

            if ct.status != "running":
                continue

            # HTTP check na URL publica (via Traefik)
            try:
                req = urllib.request.Request(public_url, method="GET")
                resp = urllib.request.urlopen(req, timeout=5)
                if resp.status == 200:
                    n8n_ready = True
                    push_event(job_id, {"status": "info", "message": "N8N acessivel!"})
                    break
            except ssl.SSLError:
                pass  # Certificado ainda nao emitido pelo Traefik
            except Exception:
                pass

            # Feedback a cada 20s
            if i % 10 == 0:
                push_event(job_id, {"status": "info", "message": f"Aguardando N8N ({i * READINESS_POLL_INTERVAL}s)..."})

        if not n8n_ready:
            push_event(job_id, {"status": "error", "message": "Timeout: N8N nao ficou acessivel em 3 minutos"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # 4. SSL via Traefik
        push_event(job_id, {"status": "info", "message": "Configurando SSL via Traefik..."})
        time.sleep(SSL_WAIT_SECONDS)

        # 5. Sucesso
        push_event(job_id, {
            "status": "complete",
            "message": "Instancia N8N criada com sucesso!",
            "instance_id": name,
            "url": instance_url(name),
            "location": "vinhedo",
            "container_status": "running",
        })
        set_state(job_id, "complete")
        logger.info("Job %s concluido: instancia '%s' criada", job_id, name)

    except Exception as e:
        push_event(job_id, {"status": "error", "message": f"Erro inesperado: {e}"})
        set_state(job_id, "error")
        logger.error("Job %s falhou: %s", job_id, e)
        # Limpar container parcialmente criado
        try:
            get_container(name).remove(force=True)
        except Exception:
            pass

    ch.basic_ack(delivery_tag=method.delivery_tag)


def _consume_loop():
    """Loop principal do consumer — reconecta automaticamente."""
    while not _stop_event.is_set():
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    port=RABBITMQ_PORT,
                    credentials=credentials,
                    heartbeat=600,
                )
            )
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=_process_job)

            logger.info("Aguardando jobs de criacao de instancia...")
            while not _stop_event.is_set():
                connection.process_data_events(time_limit=1)

            connection.close()

        except pika.exceptions.AMQPConnectionError as e:
            logger.warning("Conexao RabbitMQ perdida: %s. Reconectando em 5s...", e)
            time.sleep(5)
        except Exception as e:
            logger.error("Erro inesperado: %s. Reconectando em 10s...", e)
            time.sleep(10)


def start_worker() -> threading.Thread:
    """Inicia thread daemon do worker."""
    t = threading.Thread(target=_consume_loop, daemon=True, name="instance-worker")
    t.start()
    logger.info("Thread de worker iniciada")
    return t


def stop_worker():
    """Sinaliza o worker para parar."""
    _stop_event.set()
    logger.info("Sinal de parada enviado")
