"""Worker — consome jobs de criacao de instancia (N8N/WAHA) do RabbitMQ."""

import json
import ssl
import threading
import time
import urllib.request

import docker
import pika

from .config import (
    BASE_DOMAIN,
    RABBITMQ_HOST,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
    READINESS_MAX_ATTEMPTS,
    READINESS_POLL_INTERVAL,
    SSL_ENABLED,
    SSL_WAIT_SECONDS,
    WAHA_DEFAULT_ENGINE,
)
from .job_status import push_event, set_state
from .logger import get_logger
from .n8n import (
    create_container,
    generate_encryption_key,
    get_container,
    instance_url,
)
from .waha import (
    create_waha_container,
    generate_waha_api_key,
    get_waha_container,
    waha_instance_url,
)

logger = get_logger("worker")

QUEUE_NAME = "instance_creation"
_stop_event = threading.Event()


def _process_job(ch, method, properties, body):
    """Executa a criacao de uma instancia N8N ou WAHA."""
    job = json.loads(body)
    job_id = job["job_id"]
    name = job["name"]
    instance_type = job.get("instance_type", "n8n")
    version = job.get("version", "latest")

    logger.info(
        "Processando job %s: instancia '%s' (%s) v%s",
        job_id,
        name,
        instance_type,
        version,
    )
    set_state(job_id, "running")

    try:
        if instance_type == "waha":
            get_container_fn = get_waha_container
            create_container_fn = create_waha_container
            public_url_fn = waha_instance_url
            secret = generate_waha_api_key()
            success_message = "Instancia WAHA criada com sucesso!"
            service_label = "WAHA"
        else:
            get_container_fn = get_container
            create_container_fn = create_container
            public_url_fn = instance_url
            secret = generate_encryption_key()
            success_message = "Instancia N8N criada com sucesso!"
            service_label = "N8N"

        # 1. Verificar duplicata
        try:
            get_container_fn(name)
            push_event(job_id, {"status": "error", "message": f"Instancia '{name}' ja existe"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        except docker.errors.NotFound:
            pass

        # 2. Criar container (pull + run)
        push_event(job_id, {"status": "info", "message": "Baixando imagem e criando container..."})
        try:
            ct = create_container_fn(name, version, secret)
        except Exception as e:
            push_event(job_id, {"status": "error", "message": f"Erro ao criar container: {e}"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        push_event(job_id, {"status": "info", "message": f"Container criado, aguardando {service_label}..."})

        # 3. Aguardar startup — testa via Traefik
        service_ready = False
        public_url = public_url_fn(name)
        no_ssl_ctx = ssl.create_default_context()
        no_ssl_ctx.check_hostname = False
        no_ssl_ctx.verify_mode = ssl.CERT_NONE

        # Em modo HTTP (local/WSL), acessa via 127.0.0.1 com Host header
        # pois o dominio pode nao resolver para localhost
        if SSL_ENABLED:
            check_url = public_url
            host_header = None
        else:
            if instance_type == "waha":
                host_header = f"{name}-waha.{BASE_DOMAIN}"
            else:
                host_header = f"{name}.{BASE_DOMAIN}"
            check_url = "http://127.0.0.1"

        logger.info("Health check URL: %s (Host: %s)", check_url, host_header)

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

            # Para WAHA, considerar pronto assim que o container estiver em running.
            if instance_type == "waha":
                service_ready = True
                push_event(job_id, {"status": "info", "message": f"{service_label} em running em {public_url}"})
                break

            # HTTP check via Traefik (sem validar SSL)
            try:
                req = urllib.request.Request(check_url, method="GET")
                if host_header:
                    req.add_header("Host", host_header)
                resp = urllib.request.urlopen(req, timeout=5, context=no_ssl_ctx)
                status_code = resp.status

                ready_http_statuses = {200, 201, 202, 204}
                if status_code in ready_http_statuses:
                    service_ready = True
                    push_event(job_id, {"status": "info", "message": f"{service_label} acessivel em {public_url}"})
                    break
            except Exception as exc:
                if i % 10 == 0:
                    logger.debug("Health check tentativa %d falhou: %s", i, exc)

            # Feedback a cada 20s
            if i % 10 == 0:
                push_event(job_id, {"status": "info", "message": f"Aguardando {service_label} ({i * READINESS_POLL_INTERVAL}s)..."})

        if not service_ready:
            push_event(job_id, {"status": "error", "message": f"Timeout: {service_label} nao ficou acessivel em 3 minutos"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # 4. SSL via Traefik (se habilitado)
        if SSL_ENABLED:
            push_event(job_id, {"status": "info", "message": "Configurando SSL via Traefik..."})
            time.sleep(SSL_WAIT_SECONDS)

        # 5. Sucesso
        complete_event = {
            "status": "complete",
            "message": success_message,
            "instance_type": instance_type,
            "instance_id": name,
            "url": public_url_fn(name),
            "location": "vinhedo",
            "container_status": "running",
        }
        if instance_type == "waha":
            complete_event["credentials"] = {
                "api_key": secret,
                "engine": WAHA_DEFAULT_ENGINE,
                "dashboard_username": "admin",
                "dashboard_password": secret,
                "swagger_username": "admin",
                "swagger_password": secret,
            }
        push_event(job_id, complete_event)
        set_state(job_id, "complete")
        logger.info("Job %s concluido: instancia '%s' (%s) criada", job_id, name, instance_type)

    except Exception as e:
        push_event(job_id, {"status": "error", "message": f"Erro inesperado: {e}"})
        set_state(job_id, "error")
        logger.error("Job %s falhou: %s", job_id, e)
        # Limpar container parcialmente criado
        try:
            get_container_fn(name).remove(force=True)
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
