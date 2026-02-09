"""Gera docker-compose.yml do Traefik com Cloudflare DNS Challenge.

Uso: python config_traefik.py
"""

import os
import subprocess

cf_token = os.getenv("CF_DNS_API_TOKEN", "")
acme_email = os.getenv("ACME_EMAIL", "lojasketchware@gmail.com")

if not cf_token:
    print("ERRO: CF_DNS_API_TOKEN nao configurado. Defina no .env ou variavel de ambiente.")
    exit(1)
network_name = "n8n-public"
resolver_name = "letsencrypt"

traefik_folder = "./traefik"

# Cria pastas
os.makedirs(traefik_folder, exist_ok=True)
os.makedirs(f"{traefik_folder}/letsencrypt", exist_ok=True)

# Gera .env para o docker-compose
with open(f"{traefik_folder}/.env", "w") as env_file:
    env_file.write(f"CF_DNS_API_TOKEN={cf_token}\n")

# Gera docker-compose.yml
docker_compose = f"""\
version: '3'

networks:
  {network_name}:
    external: true

services:
  traefik:
    image: traefik:v3.6
    container_name: traefik
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      - CF_DNS_API_TOKEN=${{CF_DNS_API_TOKEN}}
    networks:
      - {network_name}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --providers.docker.network={network_name}
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --certificatesresolvers.{resolver_name}.acme.dnschallenge=true
      - --certificatesresolvers.{resolver_name}.acme.dnschallenge.provider=cloudflare
      - --certificatesresolvers.{resolver_name}.acme.email={acme_email}
      - --certificatesresolvers.{resolver_name}.acme.storage=/letsencrypt/acme.json
"""

with open(f"{traefik_folder}/docker-compose.yml", "w") as dc_file:
    dc_file.write(docker_compose)

print(f"Configuracao atualizada em '{traefik_folder}/'.")

# Cria a rede Docker se nao existir
try:
    subprocess.run(
        ["docker", "network", "create", network_name],
        capture_output=True,
    )
except Exception:
    pass

# Sobe o Traefik
try:
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=traefik_folder,
        check=True,
    )
    print(f"""
Traefik rodando com Cloudflare DNS Challenge.

  Rede:     {network_name}
  Resolver: {resolver_name}
  Email:    {acme_email}
  Imagem:   traefik:v3.6

Certificados preservados em {traefik_folder}/letsencrypt/
""")
except subprocess.CalledProcessError as e:
    print(f"ERRO: Falha ao iniciar Traefik (exit code {e.returncode}). Verifique 'docker compose version'.")
    print("Tente manualmente: cd traefik && docker compose up -d")
