"""Worker — consome jobs de criacao de instancia do RabbitMQ."""

import json
import threading
import time

import docker
import pika

from .config import (
    DOCKER_NETWORK,
    INSTANCE_CPU_PERIOD,
    INSTANCE_CPU_QUOTA,
    INSTANCE_MEM_LIMIT,
    N8N_IMAGE,
    RABBITMQ_HOST,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
)
from .docker_client import get_client
from .job_status import push_event, set_state
from .n8n import (
    build_env,
    build_traefik_labels,
    container_name,
    generate_encryption_key,
    get_container,
    instance_url,
)

QUEUE_NAME = "instance_creation"
_stop_event = threading.Event()


def _process_job(ch, method, properties, body):
    """Executa a criacao de uma instancia N8N."""
    job = json.loads(body)
    job_id = job["job_id"]
    name = job["name"]
    version = job.get("version", "latest")

    print(f"[WORKER] Processando job {job_id}: instancia '{name}' v{version}")
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

        # 2. Baixar imagem Docker
        image_tag = f"{N8N_IMAGE}:{version}"
        push_event(job_id, {"status": "info", "message": f"Baixando imagem {image_tag}..."})
        try:
            get_client().images.pull(N8N_IMAGE, tag=version)
        except Exception as e:
            push_event(job_id, {"status": "error", "message": f"Erro ao baixar imagem: {e}"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return
        push_event(job_id, {"status": "info", "message": "Imagem pronta"})

        # 3. Criar container
        push_event(job_id, {"status": "info", "message": "Criando container N8N..."})
        try:
            env = build_env(name, encryption_key)
            labels = build_traefik_labels(name)
            ct = get_client().containers.run(
                image=image_tag,
                name=container_name(name),
                detach=True,
                restart_policy={"Name": "unless-stopped"},
                environment=env,
                labels=labels,
                mem_limit=INSTANCE_MEM_LIMIT,
                cpu_period=INSTANCE_CPU_PERIOD,
                cpu_quota=INSTANCE_CPU_QUOTA,
                volumes={f"n8n-data-{name}": {"bind": "/home/node/.n8n", "mode": "rw"}},
                network=DOCKER_NETWORK,
            )
        except Exception as e:
            push_event(job_id, {"status": "error", "message": f"Erro ao criar container: {e}"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        push_event(job_id, {"status": "info", "message": "Container criado, iniciando N8N..."})

        # 4. Aguardar startup (verifica logs + HTTP)
        push_event(job_id, {"status": "info", "message": "Aguardando instancia ficar disponivel..."})
        n8n_ready = False
        for i in range(60):
            time.sleep(2)
            ct.reload()
            if ct.status == "running":
                push_event(job_id, {"status": "info", "message": f"Verificando N8N ({i + 1}/60)"})
                try:
                    logs = ct.logs(tail=10).decode("utf-8", errors="replace")
                    if "Editor is now accessible" in logs or "Webhook listener" in logs or "n8n ready" in logs.lower():
                        n8n_ready = True
                        break
                except Exception:
                    pass
                # Fallback: verificar via HTTP interno
                if i >= 15 and i % 5 == 0:
                    try:
                        import urllib.request
                        url_check = f"http://{container_name(name)}:5678/healthz"
                        req = urllib.request.Request(url_check, method="GET")
                        resp = urllib.request.urlopen(req, timeout=3)
                        if resp.status == 200:
                            n8n_ready = True
                            push_event(job_id, {"status": "info", "message": "N8N respondendo via HTTP"})
                            break
                    except Exception:
                        pass
            elif ct.status == "exited":
                logs = ct.logs(tail=30).decode("utf-8", errors="replace")
                push_event(job_id, {"status": "error", "message": f"Container parou.\n{logs}"})
                set_state(job_id, "error")
                ch.basic_ack(delivery_tag=method.delivery_tag)
                return

        if not n8n_ready:
            logs = ct.logs(tail=30).decode("utf-8", errors="replace")
            push_event(job_id, {"status": "error", "message": f"N8N nao iniciou em 2 minutos.\n{logs}"})
            set_state(job_id, "error")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # 5. SSL via Traefik
        push_event(job_id, {"status": "info", "message": "Configurando SSL via Traefik..."})
        time.sleep(5)

        # 6. Sucesso
        push_event(job_id, {
            "status": "complete",
            "message": "Instancia N8N criada com sucesso!",
            "instance_id": name,
            "url": instance_url(name),
            "location": "vinhedo",
            "container_status": "running",
        })
        set_state(job_id, "complete")
        print(f"[WORKER] Job {job_id} concluido: instancia '{name}' criada")

    except Exception as e:
        push_event(job_id, {"status": "error", "message": f"Erro inesperado: {e}"})
        set_state(job_id, "error")
        print(f"[WORKER] Job {job_id} falhou: {e}")

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

            print("[WORKER] Aguardando jobs de criacao de instancia...")
            while not _stop_event.is_set():
                connection.process_data_events(time_limit=1)

            connection.close()

        except pika.exceptions.AMQPConnectionError as e:
            print(f"[WORKER] Conexao RabbitMQ perdida: {e}. Reconectando em 5s...")
            time.sleep(5)
        except Exception as e:
            print(f"[WORKER] Erro inesperado: {e}. Reconectando em 10s...")
            time.sleep(10)


def start_worker() -> threading.Thread:
    """Inicia thread daemon do worker."""
    t = threading.Thread(target=_consume_loop, daemon=True, name="instance-worker")
    t.start()
    print("[WORKER] Thread de worker iniciada")
    return t


def stop_worker():
    """Sinaliza o worker para parar."""
    _stop_event.set()
    print("[WORKER] Sinal de parada enviado")
