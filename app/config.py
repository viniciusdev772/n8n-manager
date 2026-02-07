"""Configurações centralizadas carregadas do .env"""

import os

from dotenv import load_dotenv

load_dotenv()

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "changeme")
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "n8n.marketcodebrasil.com.br")
ACME_EMAIL = os.getenv("ACME_EMAIL", "admin@marketcodebrasil.com.br")
PG_HOST = os.getenv("PG_HOST", "postgres")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_USER = os.getenv("PG_USER", "n8n")
PG_PASSWORD = os.getenv("PG_PASSWORD", "n8n")
PG_ADMIN_DB = os.getenv("PG_ADMIN_DB", "postgres")
DOCKER_NETWORK = os.getenv("DOCKER_NETWORK", "n8n-public")
SERVER_PORT = int(os.getenv("SERVER_PORT", "5050"))

# RabbitMQ
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "127.0.0.1")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "guest")

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

N8N_IMAGE = "docker.n8n.io/n8nio/n8n"

# Recursos por instância (mínimo N8N - plano gratuito)
INSTANCE_MEM_LIMIT = "512m"
INSTANCE_CPU_PERIOD = 100000
INSTANCE_CPU_QUOTA = 50000  # 0.5 vCPU
