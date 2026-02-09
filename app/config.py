"""Configurações centralizadas carregadas do .env"""

import os

from dotenv import load_dotenv

load_dotenv()

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "n8n.marketcodebrasil.com.br")
ACME_EMAIL = os.getenv("ACME_EMAIL", "admin@marketcodebrasil.com.br")
DOCKER_NETWORK = os.getenv("DOCKER_NETWORK", "n8n-public")
SERVER_PORT = int(os.getenv("SERVER_PORT", "5050"))

# RabbitMQ
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "127.0.0.1")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Cloudflare (para Traefik DNS Challenge quando criamos nosso próprio Traefik)
CF_DNS_API_TOKEN = os.getenv("CF_DNS_API_TOKEN", "")

# Traefik
TRAEFIK_CERT_RESOLVER = os.getenv("TRAEFIK_CERT_RESOLVER", "letsencrypt")

# CORS
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

N8N_IMAGE = "docker.n8n.io/n8nio/n8n"
DEFAULT_N8N_VERSION = "1.123.20"

# Recursos por instância (otimizado — N8N idle ~100MB, CPU não-intensivo)
INSTANCE_MEM_LIMIT = "384m"          # Hard limit (heap max 256MB + overhead)
INSTANCE_MEM_RESERVATION = "192m"    # Soft limit — Docker recupera sob pressão de memória
INSTANCE_CPU_SHARES = 512            # Peso relativo (default=1024). Sem hard cap de CPU.

# Worker — readiness probe
READINESS_MAX_ATTEMPTS = int(os.getenv("READINESS_MAX_ATTEMPTS", "90"))  # 90 x 2s = 3 min
READINESS_POLL_INTERVAL = int(os.getenv("READINESS_POLL_INTERVAL", "2"))
SSL_WAIT_SECONDS = 5

# Cleanup automático
CLEANUP_MAX_AGE_DAYS = int(os.getenv("CLEANUP_MAX_AGE_DAYS", "5"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "3600"))

# Job status (Redis TTL)
JOB_TTL = int(os.getenv("JOB_TTL", "600"))              # 10 minutos
JOB_CLEANUP_TTL = int(os.getenv("JOB_CLEANUP_TTL", "300"))  # 5 minutos apos conclusao

# SSE
SSE_MAX_DURATION = 300  # 5 minutos

# Timezone padrão para instâncias
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/Sao_Paulo")
