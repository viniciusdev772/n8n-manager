#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  N8N Instance Manager — Setup do Servidor VPS
#  Compatível com: Ubuntu, Debian, CentOS, Fedora, AlmaLinux, Rocky
#  Executa: curl -fsSL https://raw.githubusercontent.com/.../setup.sh | bash
# ═══════════════════════════════════════════════════════════

set -eo pipefail

export DEBIAN_FRONTEND=noninteractive

REPO_RAW="https://raw.githubusercontent.com/viniciusdev772/n8n-manager/main"
REPO_API="https://api.github.com/repos/viniciusdev772/n8n-manager/commits/main"
PROJECT_DIR="/opt/n8n-manager"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[X]${NC} $1"; exit 1; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

# --- Verificacoes iniciais ---

if [ "$EUID" -ne 0 ]; then
    err "Execute como root: sudo bash setup.sh"
fi

echo ""
echo -e "${CYAN}===============================================${NC}"
echo -e "${CYAN}  N8N Instance Manager - Setup VPS${NC}"

# Mostrar versao local instalada (se existir)
LOCAL_HASH=""
if [ -d "$PROJECT_DIR/.git" ]; then
    LOCAL_HASH=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo "")
fi

# Buscar versao mais recente do GitHub
REMOTE_HASH=""
REMOTE_HASH_FULL=""
REMOTE_DATE=""
info "Verificando versao mais recente no GitHub..."
API_RESPONSE=$(curl -sf --max-time 10 "$REPO_API" 2>/dev/null || echo "")
if [ -n "$API_RESPONSE" ]; then
    REMOTE_HASH_FULL=$(echo "$API_RESPONSE" | grep -oP '"sha"\s*:\s*"\K[a-f0-9]{40}' | head -1 || echo "")
    REMOTE_HASH="${REMOTE_HASH_FULL:0:7}"
    REMOTE_DATE=$(echo "$API_RESPONSE" | grep -oP '"date"\s*:\s*"\K[^"]+' | head -1 || echo "")
fi

if [ -n "$LOCAL_HASH" ] && [ -n "$REMOTE_HASH" ]; then
    if [ "$LOCAL_HASH" = "$REMOTE_HASH" ]; then
        echo -e "${DIM}  Versao instalada: ${LOCAL_HASH} (atualizado)${NC}"
    else
        echo -e "${YELLOW}  Instalado: ${LOCAL_HASH} -> Atualizando para: ${REMOTE_HASH}${NC}"
    fi
elif [ -n "$LOCAL_HASH" ]; then
    echo -e "${DIM}  Versao instalada: ${LOCAL_HASH}${NC}"
elif [ -n "$REMOTE_HASH" ]; then
    echo -e "${DIM}  Versao remota: ${REMOTE_HASH} (${REMOTE_DATE})${NC}"
else
    echo -e "${DIM}  Primeira instalacao${NC}"
fi

echo -e "${CYAN}===============================================${NC}"
echo ""

# --- Auto-update do proprio script ---

if [ -n "$REMOTE_HASH_FULL" ]; then
    # Se esta rodando via pipe (curl | bash), baixar versao mais recente e re-executar
    SELF_PATH="${BASH_SOURCE[0]:-}"
    if [ -z "$SELF_PATH" ] || [ "$SELF_PATH" = "bash" ] || [ "$SELF_PATH" = "/dev/stdin" ]; then
        # Rodando via pipe - baixar script mais recente para /tmp e re-executar
        TMPFILE="/tmp/n8n-setup-${REMOTE_HASH}.sh"
        if [ ! -f "$TMPFILE" ]; then
            info "Baixando script mais recente (${REMOTE_HASH})..."
            curl -fsSL "${REPO_RAW}/setup.sh?t=$(date +%s)" -o "$TMPFILE" 2>/dev/null || true
        fi
        # Nao re-executar para evitar loop - o curl ja pega o mais recente
    fi
fi

# --- Detectar distro ---

detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO="$ID"
        DISTRO_VERSION="${VERSION_ID:-unknown}"
        DISTRO_NAME="${PRETTY_NAME:-$ID}"
    elif [ -f /etc/redhat-release ]; then
        DISTRO="centos"
        DISTRO_VERSION=$(rpm -q --qf '%{VERSION}' centos-release 2>/dev/null || echo "unknown")
        DISTRO_NAME=$(cat /etc/redhat-release)
    else
        err "Distribuicao Linux nao detectada"
    fi

    DISTRO=$(echo "$DISTRO" | tr '[:upper:]' '[:lower:]')
    info "Distro detectada: $DISTRO_NAME"
}

detect_distro

# Determinar gerenciador de pacotes
case "$DISTRO" in
    ubuntu|debian|pop|linuxmint|elementary|zorin)
        PKG="apt"
        PKG_UPDATE="apt-get update -qq"
        PKG_INSTALL="apt-get install -y -qq"
        FIREWALL_CMD="ufw"
        ;;
    centos|rhel|rocky|almalinux|ol|amzn)
        PKG="yum"
        PKG_UPDATE="yum makecache -q"
        PKG_INSTALL="yum install -y -q"
        FIREWALL_CMD="firewalld"
        ;;
    fedora)
        PKG="dnf"
        PKG_UPDATE="dnf makecache -q"
        PKG_INSTALL="dnf install -y -q"
        FIREWALL_CMD="firewalld"
        ;;
    arch|manjaro)
        PKG="pacman"
        PKG_UPDATE="pacman -Sy --noconfirm"
        PKG_INSTALL="pacman -S --noconfirm"
        FIREWALL_CMD="ufw"
        ;;
    opensuse*|sles)
        PKG="zypper"
        PKG_UPDATE="zypper refresh -q"
        PKG_INSTALL="zypper install -y -q"
        FIREWALL_CMD="firewalld"
        ;;
    *)
        warn "Distro '$DISTRO' nao mapeada, tentando com apt..."
        PKG="apt"
        PKG_UPDATE="apt-get update -qq"
        PKG_INSTALL="apt-get install -y -qq"
        FIREWALL_CMD="ufw"
        ;;
esac

log "Gerenciador de pacotes: $PKG"

# --- 1. Atualizar sistema ---

info "Atualizando pacotes do sistema..."
$PKG_UPDATE > /dev/null 2>&1 || true

case "$PKG" in
    apt)    apt-get upgrade -y -qq -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" > /dev/null 2>&1 || true ;;
    yum)    yum update -y -q > /dev/null 2>&1 || true ;;
    dnf)    dnf upgrade -y -q > /dev/null 2>&1 || true ;;
    pacman) pacman -Syu --noconfirm > /dev/null 2>&1 || true ;;
    zypper) zypper update -y -q > /dev/null 2>&1 || true ;;
esac
log "Sistema atualizado"

# --- 2. Instalar dependencias basicas ---

info "Instalando dependencias..."
BASIC_DEPS="curl wget git htop nano unzip"

case "$PKG" in
    apt)    $PKG_INSTALL $BASIC_DEPS ca-certificates gnupg lsb-release software-properties-common > /dev/null 2>&1 || true ;;
    yum)    $PKG_INSTALL $BASIC_DEPS yum-utils > /dev/null 2>&1 || true ;;
    dnf)    $PKG_INSTALL $BASIC_DEPS dnf-plugins-core > /dev/null 2>&1 || true ;;
    pacman) $PKG_INSTALL $BASIC_DEPS > /dev/null 2>&1 || true ;;
    zypper) $PKG_INSTALL $BASIC_DEPS > /dev/null 2>&1 || true ;;
esac
log "Dependencias instaladas"

# --- 3. Configurar Swap (se < 4GB RAM) ---

TOTAL_RAM_MB=$(free -m | awk '/Mem:/ {print $2}')
SWAP_CURRENT=$(free -m | awk '/Swap:/ {print $2}')

info "RAM total: ${TOTAL_RAM_MB}MB | Swap atual: ${SWAP_CURRENT}MB"

if [ "$SWAP_CURRENT" -lt 1024 ]; then
    SWAP_SIZE="2G"
    if [ "$TOTAL_RAM_MB" -le 2048 ]; then
        SWAP_SIZE="4G"
    elif [ "$TOTAL_RAM_MB" -le 4096 ]; then
        SWAP_SIZE="3G"
    fi

    info "Criando swap de $SWAP_SIZE..."

    if [ -f /swapfile ]; then
        swapoff /swapfile 2>/dev/null || true
        rm -f /swapfile
    fi

    fallocate -l "$SWAP_SIZE" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=$((${SWAP_SIZE%G} * 1024)) status=none
    chmod 600 /swapfile
    mkswap /swapfile > /dev/null 2>&1
    swapon /swapfile

    # Persistir no fstab
    grep -q "/swapfile" /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab

    # Otimizar swap (usar menos, preferir RAM)
    sysctl -w vm.swappiness=10 > /dev/null 2>&1
    sysctl -w vm.vfs_cache_pressure=50 > /dev/null 2>&1
    grep -q "vm.swappiness" /etc/sysctl.conf || echo "vm.swappiness=10" >> /etc/sysctl.conf
    grep -q "vm.vfs_cache_pressure" /etc/sysctl.conf || echo "vm.vfs_cache_pressure=50" >> /etc/sysctl.conf

    log "Swap de $SWAP_SIZE configurado (swappiness=10)"
else
    log "Swap ja configurado (${SWAP_CURRENT}MB)"
fi

# --- 4. Instalar Docker ---

if command -v docker > /dev/null 2>&1; then
    DOCKER_VER=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "unknown")
    log "Docker ja instalado (v$DOCKER_VER)"
else
    info "Instalando Docker via script oficial..."
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
    log "Docker instalado"
fi

# Garantir que Docker esta rodando e habilitado
systemctl enable docker > /dev/null 2>&1 || true
systemctl start docker > /dev/null 2>&1 || true
log "Docker ativo e habilitado no boot"

# Verificar Docker Compose (plugin)
if docker compose version > /dev/null 2>&1; then
    COMPOSE_VER=$(docker compose version --short 2>/dev/null || echo "unknown")
    log "Docker Compose plugin: v$COMPOSE_VER"
else
    warn "Docker Compose plugin nao encontrado, instalando..."
    $PKG_INSTALL docker-compose-plugin > /dev/null 2>&1 || true
fi

# --- 5. Hardening Docker + Auto-repair ---

SAFE_DAEMON_JSON='{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 65536,
      "Soft": 32768
    },
    "nproc": {
      "Name": "nproc",
      "Hard": 4096,
      "Soft": 2048
    }
  },
  "live-restore": true
}'

# Funcao: esperar Docker ficar saudavel
wait_docker() {
    local max_wait=${1:-30}
    for i in $(seq 1 "$max_wait"); do
        if docker info > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

mkdir -p /etc/docker

# Verificar se Docker esta saudavel AGORA
if ! docker info > /dev/null 2>&1; then
    warn "Docker nao esta respondendo! Iniciando auto-repair..."

    # Tentar iniciar normalmente
    systemctl reset-failed docker > /dev/null 2>&1 || true
    systemctl start docker > /dev/null 2>&1 || true

    if ! wait_docker 10; then
        # Docker nao sobe — daemon.json pode ser o problema
        if [ -f /etc/docker/daemon.json ]; then
            warn "Removendo daemon.json potencialmente corrompido..."
            cp /etc/docker/daemon.json /etc/docker/daemon.json.broken.$(date +%s) 2>/dev/null || true
            rm -f /etc/docker/daemon.json
            systemctl reset-failed docker > /dev/null 2>&1 || true
            systemctl restart docker > /dev/null 2>&1 || true

            if wait_docker 15; then
                log "Docker reparado (daemon.json removido)"
            else
                err "Docker nao consegue iniciar. Verifique: journalctl -u docker --no-pager -n 30"
            fi
        else
            # Sem daemon.json — problema e outro
            systemctl restart docker > /dev/null 2>&1 || true
            if ! wait_docker 15; then
                err "Docker nao consegue iniciar. Verifique: journalctl -u docker --no-pager -n 30"
            fi
        fi
    fi
fi

log "Docker saudavel e respondendo"

# Aplicar hardening seguro (se ainda nao tem daemon.json)
if [ ! -f /etc/docker/daemon.json ] || [ ! -s /etc/docker/daemon.json ]; then
    info "Aplicando hardening seguro no Docker..."
    echo "$SAFE_DAEMON_JSON" > /etc/docker/daemon.json
    systemctl restart docker > /dev/null 2>&1 || true

    if wait_docker 30; then
        log "Docker hardening aplicado (log rotation, ulimits, live-restore)"
    else
        warn "Docker nao reiniciou com hardening, revertendo..."
        rm -f /etc/docker/daemon.json
        systemctl reset-failed docker > /dev/null 2>&1 || true
        systemctl restart docker > /dev/null 2>&1 || true
        wait_docker 15
        log "Docker restaurado (sem hardening customizado)"
    fi
else
    # daemon.json existe e Docker esta saudavel — nao mexer
    log "Docker hardening ja configurado (mantido)"
fi

# --- 6. Instalar Python 3 + venv ---

info "Verificando Python 3..."

# Garantir python3-venv instalado (Ubuntu 24.04 nao inclui por padrao)
case "$PKG" in
    apt)
        $PKG_INSTALL python3 python3-venv python3-pip python3-full > /dev/null 2>&1 || true
        ;;
    yum|dnf)
        $PKG_INSTALL python3 python3-pip > /dev/null 2>&1 || true
        ;;
    pacman)
        $PKG_INSTALL python python-pip > /dev/null 2>&1 || true
        ;;
    zypper)
        $PKG_INSTALL python3 python3-pip > /dev/null 2>&1 || true
        ;;
esac

PY_VER=$(python3 --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo "nao encontrado")
log "Python $PY_VER"

# --- 7. Firewall ---

info "Configurando firewall..."

if [ "$FIREWALL_CMD" = "ufw" ]; then
    if command -v ufw > /dev/null 2>&1; then
        ufw --force reset > /dev/null 2>&1 || true
        ufw default deny incoming > /dev/null 2>&1 || true
        ufw default allow outgoing > /dev/null 2>&1 || true
        ufw allow 22/tcp comment "SSH" > /dev/null 2>&1 || true
        ufw allow 80/tcp comment "HTTP" > /dev/null 2>&1 || true
        ufw allow 443/tcp comment "HTTPS" > /dev/null 2>&1 || true
        ufw allow 5050/tcp comment "N8N Manager API" > /dev/null 2>&1 || true
        ufw allow 8080/tcp comment "Traefik Dashboard" > /dev/null 2>&1 || true
        ufw allow 15672/tcp comment "RabbitMQ Management" > /dev/null 2>&1 || true
        ufw --force enable > /dev/null 2>&1 || true
        log "UFW configurado (SSH, HTTP, HTTPS, API, Traefik)"
    else
        $PKG_INSTALL ufw > /dev/null 2>&1 || warn "Nao foi possivel instalar UFW"
    fi
elif [ "$FIREWALL_CMD" = "firewalld" ]; then
    if command -v firewall-cmd > /dev/null 2>&1; then
        systemctl enable firewalld > /dev/null 2>&1 || true
        systemctl start firewalld > /dev/null 2>&1 || true
        firewall-cmd --permanent --add-service=ssh > /dev/null 2>&1 || true
        firewall-cmd --permanent --add-service=http > /dev/null 2>&1 || true
        firewall-cmd --permanent --add-service=https > /dev/null 2>&1 || true
        firewall-cmd --permanent --add-port=5050/tcp > /dev/null 2>&1 || true
        firewall-cmd --permanent --add-port=8080/tcp > /dev/null 2>&1 || true
        firewall-cmd --permanent --add-port=15672/tcp > /dev/null 2>&1 || true
        firewall-cmd --reload > /dev/null 2>&1 || true
        log "Firewalld configurado (SSH, HTTP, HTTPS, API, Traefik)"
    else
        $PKG_INSTALL firewalld > /dev/null 2>&1 || warn "Nao foi possivel instalar firewalld"
    fi
fi

# --- 8. Otimizacoes de kernel (idempotente) ---

info "Aplicando otimizacoes de kernel..."

SYSCTL_MARKER="# N8N-Manager-Tuning"

if ! grep -q "$SYSCTL_MARKER" /etc/sysctl.conf 2>/dev/null; then
    cat >> /etc/sysctl.conf << SYSCTL

$SYSCTL_MARKER
net.core.somaxconn=65535
net.ipv4.tcp_max_syn_backlog=65535
net.ipv4.ip_local_port_range=1024 65535
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_fin_timeout=15
net.core.netdev_max_backlog=65535
fs.file-max=2097152
fs.inotify.max_user_watches=524288
SYSCTL
    log "Kernel otimizado (rede, file descriptors)"
else
    log "Kernel ja otimizado (configuracao existente)"
fi

sysctl -p > /dev/null 2>&1 || true

# Limites de arquivos para o usuario
cat > /etc/security/limits.d/docker.conf << 'LIMITS'
*    soft    nofile    65536
*    hard    nofile    65536
*    soft    nproc     4096
*    hard    nproc     4096
LIMITS

log "Limites de arquivos configurados"

# --- 9. Clonar e configurar o projeto ---

if [ -d "$PROJECT_DIR/.git" ]; then
    info "Projeto ja existe em $PROJECT_DIR, atualizando..."
    cd "$PROJECT_DIR"
    git pull origin main > /dev/null 2>&1 || true
else
    if [ -d "$PROJECT_DIR" ]; then
        rm -rf "$PROJECT_DIR"
    fi
    info "Clonando projeto..."
    git clone https://github.com/viniciusdev772/n8n-manager.git "$PROJECT_DIR" > /dev/null 2>&1
fi

cd "$PROJECT_DIR"

# Criar venv e instalar dependencias
info "Criando ambiente virtual Python..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1
deactivate

log "Dependencias Python instaladas em venv"

# Auto-configurar .env (gera senhas seguras automaticamente)
if [ ! -f "$PROJECT_DIR/.env" ]; then
    info "Gerando configuracao automatica (.env)..."

    # Detectar IP publico do servidor
    SERVER_IP=$(curl -sf --max-time 5 https://ifconfig.me 2>/dev/null \
        || curl -sf --max-time 5 https://api.ipify.org 2>/dev/null \
        || curl -sf --max-time 5 https://icanhazip.com 2>/dev/null \
        || hostname -I | awk '{print $1}')

    # Gerar token seguro
    GEN_API_TOKEN=$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 64)

    cat > "$PROJECT_DIR/.env" << ENVFILE
# === Auto-gerado pelo setup.sh em $(date '+%Y-%m-%d %H:%M:%S') ===

# Token de autenticacao da API (use este mesmo token no Next.js)
API_AUTH_TOKEN=$GEN_API_TOKEN

# Dominio base para instancias (ex: nome.BASE_DOMAIN)
BASE_DOMAIN=n8n.marketcodebrasil.com.br

# Email para certificados SSL (Let's Encrypt via Traefik)
ACME_EMAIL=admin@marketcodebrasil.com.br

# Cloudflare DNS API Token (para SSL wildcard via DNS Challenge)
CF_DNS_API_TOKEN=HwJjOXXzv59DSvXPcJ794Ml894d7yPEmkYmtZn3V

# Rede Docker para Traefik + instancias
DOCKER_NETWORK=n8n-public

# RabbitMQ (job queue para criacao de instancias)
RABBITMQ_HOST=127.0.0.1
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# Redis (status de jobs)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379

# Porta do servidor FastAPI
SERVER_PORT=5050
ENVFILE

    log "Arquivo .env gerado automaticamente"
    info "IP do servidor: $SERVER_IP"
    info "API Token: ${GEN_API_TOKEN:0:12}... (salvo no .env)"
    info "PG Password: gerada automaticamente"
    warn "IMPORTANTE: Copie o API_AUTH_TOKEN para o seu Next.js (.env do n8n-vendas)"
    warn "  cat $PROJECT_DIR/.env | grep API_AUTH_TOKEN"
else
    log "Arquivo .env ja existe (verificado)"
fi

# --- 10. Criar servico systemd ---

info "Criando servico systemd..."

cat > /etc/systemd/system/n8n-manager.service << SERVICE
[Unit]
Description=N8N Instance Manager
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable n8n-manager > /dev/null 2>&1 || true
log "Servico n8n-manager criado e habilitado no boot"

# --- 11. Auto-start do servico ---

info "Garantindo Docker ativo antes de iniciar..."
if ! docker info > /dev/null 2>&1; then
    systemctl reset-failed docker > /dev/null 2>&1 || true
    systemctl start docker > /dev/null 2>&1 || true
    wait_docker 15 || warn "Docker pode nao estar respondendo"
fi

info "Iniciando N8N Manager..."
systemctl restart n8n-manager > /dev/null 2>&1 || true
sleep 5

# --- 12. Garantir Traefik na rede correta ---

DOCKER_NET="n8n-public"
info "Verificando rede Docker '${DOCKER_NET}'..."

# Criar rede se nao existir
if ! docker network inspect "$DOCKER_NET" > /dev/null 2>&1; then
    docker network create "$DOCKER_NET" > /dev/null 2>&1
    log "Rede '$DOCKER_NET' criada"
else
    log "Rede '$DOCKER_NET' existe"
fi

# Verificar Traefik
TRAEFIK_NET=$(docker inspect traefik --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || echo "")

if docker inspect traefik > /dev/null 2>&1; then
    if echo "$TRAEFIK_NET" | grep -q "$DOCKER_NET"; then
        TRAEFIK_STATUS=$(docker inspect traefik --format '{{.State.Status}}' 2>/dev/null)
        if [ "$TRAEFIK_STATUS" = "running" ]; then
            log "Traefik: rodando na rede '$DOCKER_NET'"
        else
            info "Traefik existe mas nao esta rodando (status: $TRAEFIK_STATUS). Removendo para recriar..."
            docker rm -f traefik > /dev/null 2>&1 || true
            sleep 3
        fi
    else
        warn "Traefik esta na rede errada (redes: ${TRAEFIK_NET:-nenhuma}). Recriando..."
        docker rm -f traefik > /dev/null 2>&1 || true
        sleep 5
    fi
fi

# Se Traefik nao existe, recriar — o bootstrap_infra vai cuidar no proximo restart
if ! docker inspect traefik > /dev/null 2>&1; then
    info "Recriando Traefik na rede '$DOCKER_NET'..."

    # Aguardar portas liberarem
    for i in $(seq 1 10); do
        if ! ss -tlnp | grep -qE ':80\s|:443\s'; then
            break
        fi
        sleep 1
    done

    # Ler config do .env
    ACME_EMAIL_ENV=$(grep -oP 'ACME_EMAIL=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "admin@marketcodebrasil.com.br")
    CF_TOKEN_ENV=$(grep -oP 'CF_DNS_API_TOKEN=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "")

    docker run -d \
        --name traefik \
        --restart unless-stopped \
        --network "$DOCKER_NET" \
        -p 80:80 -p 443:443 \
        -e CF_DNS_API_TOKEN="$CF_TOKEN_ENV" \
        -v /var/run/docker.sock:/var/run/docker.sock:ro \
        -v traefik-certs:/certs \
        traefik:v3.6 \
        --providers.docker=true \
        --providers.docker.exposedbydefault=false \
        --providers.docker.network="$DOCKER_NET" \
        --entrypoints.web.address=:80 \
        --entrypoints.websecure.address=:443 \
        --entrypoints.web.http.redirections.entrypoint.to=websecure \
        --entrypoints.web.http.redirections.entrypoint.scheme=https \
        --certificatesresolvers.letsencrypt.acme.dnschallenge=true \
        --certificatesresolvers.letsencrypt.acme.dnschallenge.provider=cloudflare \
        --certificatesresolvers.letsencrypt.acme.email="$ACME_EMAIL_ENV" \
        --certificatesresolvers.letsencrypt.acme.storage=/certs/acme.json \
        > /dev/null 2>&1

    if docker inspect traefik > /dev/null 2>&1; then
        TRAEFIK_STATE=$(docker inspect traefik --format '{{.State.Status}}' 2>/dev/null)
        if [ "$TRAEFIK_STATE" = "running" ]; then
            log "Traefik recriado e rodando na rede '$DOCKER_NET'"
        else
            warn "Traefik criado mas status: $TRAEFIK_STATE"
        fi
    else
        warn "Falha ao criar Traefik (portas podem estar ocupadas)"
    fi
fi

# Reiniciar servico apos garantir Traefik correto
info "Reiniciando N8N Manager apos fix do Traefik..."
systemctl restart n8n-manager > /dev/null 2>&1 || true
sleep 5

# --- 13. Verificacao final de qualidade ---

info "Executando verificacao final..."
ERRORS=0

# Docker
if docker info > /dev/null 2>&1; then
    log "Docker: funcionando"
else
    warn "Docker: FALHOU"
    ERRORS=$((ERRORS + 1))
fi

# Python venv
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    log "Python venv: OK ($($PROJECT_DIR/venv/bin/python --version 2>&1))"
else
    warn "Python venv: NAO ENCONTRADO"
    ERRORS=$((ERRORS + 1))
fi

# Dependencias Python
if $PROJECT_DIR/venv/bin/python -c "import fastapi, uvicorn, docker" 2>/dev/null; then
    log "Dependencias Python: OK (fastapi, uvicorn, docker)"
else
    warn "Dependencias Python: FALTANDO"
    ERRORS=$((ERRORS + 1))
fi

# .env
if [ -f "$PROJECT_DIR/.env" ]; then
    log "Arquivo .env: presente"
else
    warn "Arquivo .env: AUSENTE (servico nao vai funcionar sem credenciais)"
    ERRORS=$((ERRORS + 1))
fi

# Servico systemd
if systemctl is-active --quiet n8n-manager 2>/dev/null; then
    log "Servico n8n-manager: ATIVO"
else
    warn "Servico n8n-manager: INATIVO (verifique .env e logs)"
    ERRORS=$((ERRORS + 1))
fi

# Health check na API
sleep 2
if curl -sf http://localhost:5050/health > /dev/null 2>&1; then
    log "API health check: OK (porta 5050)"
else
    warn "API health check: SEM RESPOSTA (pode precisar configurar .env primeiro)"
    ERRORS=$((ERRORS + 1))
fi

# Firewall
if command -v ufw > /dev/null 2>&1 && ufw status | grep -q "Status: active" 2>/dev/null; then
    log "Firewall UFW: ativo"
elif command -v firewall-cmd > /dev/null 2>&1 && firewall-cmd --state 2>/dev/null | grep -q "running"; then
    log "Firewall firewalld: ativo"
else
    warn "Firewall: nao detectado ou inativo"
fi

# Swap
SWAP_FINAL=$(free -m | awk '/Swap:/ {print $2}')
if [ "$SWAP_FINAL" -gt 0 ]; then
    log "Swap: ${SWAP_FINAL}MB configurado"
else
    warn "Swap: nao configurado"
fi

# --- Resumo final ---

echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GREEN}===============================================${NC}"
    echo -e "${GREEN}  Setup concluido - TUDO OK!${NC}"
    echo -e "${GREEN}===============================================${NC}"
else
    echo -e "${YELLOW}===============================================${NC}"
    echo -e "${YELLOW}  Setup concluido com $ERRORS aviso(s)${NC}"
    echo -e "${YELLOW}===============================================${NC}"
fi
echo ""
echo -e "  ${CYAN}Distro:${NC}       $DISTRO_NAME"
echo -e "  ${CYAN}RAM:${NC}          ${TOTAL_RAM_MB}MB"
echo -e "  ${CYAN}Swap:${NC}         ${SWAP_FINAL}MB"
echo -e "  ${CYAN}Docker:${NC}       $(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo 'N/A')"
echo -e "  ${CYAN}Python:${NC}       $(python3 --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' || echo 'N/A')"
echo -e "  ${CYAN}Projeto:${NC}      $PROJECT_DIR"
echo -e "  ${CYAN}Servico:${NC}      $(systemctl is-active n8n-manager 2>/dev/null || echo 'inativo')"
echo ""
if [ "$ERRORS" -gt 0 ]; then
    echo -e "  ${YELLOW}Acao necessaria:${NC}"
    echo -e "  1. Edite as credenciais:  ${CYAN}nano $PROJECT_DIR/.env${NC}"
    echo -e "  2. Reinicie o servico:    ${CYAN}systemctl restart n8n-manager${NC}"
    echo -e "  3. Veja os logs:          ${CYAN}journalctl -u n8n-manager -f${NC}"
else
    echo -e "  ${GREEN}Servico rodando!${NC} Teste com: ${CYAN}curl http://localhost:5050/health${NC}"
    echo -e "  Logs: ${CYAN}journalctl -u n8n-manager -f${NC}"
fi
echo ""
echo -e "  ${YELLOW}Portas abertas:${NC} 22 (SSH), 80 (HTTP), 443 (HTTPS), 5050 (API), 8080 (Traefik)"
echo ""
