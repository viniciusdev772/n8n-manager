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

# Prompt interativo (le de /dev/tty para funcionar com curl | bash)
ask() {
    local prompt="$1" default="$2" varname="$3" input
    if [ -n "$default" ]; then
        read -rp "$(echo -e "  ${CYAN}${prompt}${NC} [${DIM}${default}${NC}]: ")" input < /dev/tty
    else
        read -rp "$(echo -e "  ${CYAN}${prompt}${NC}: ")" input < /dev/tty
    fi
    eval "$varname=\"${input:-$default}\""
}

# Confirmar sim/nao (default = $2, retorna 0 para sim)
confirm() {
    local prompt="$1" default="${2:-N}" input
    read -rp "$(echo -e "  ${CYAN}${prompt}${NC} [${default}]: ")" input < /dev/tty
    input="${input:-$default}"
    [[ "$input" =~ ^[sS]$ ]]
}

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

# Porta da API (le do .env se existir, senao usa default — sera atualizado apos wizard)
FW_API_PORT=$(grep -oP 'SERVER_PORT=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "5050")
FW_API_PORT="${FW_API_PORT:-5050}"

if [ "$FIREWALL_CMD" = "ufw" ]; then
    if command -v ufw > /dev/null 2>&1; then
        ufw default deny incoming > /dev/null 2>&1 || true
        ufw default allow outgoing > /dev/null 2>&1 || true
        ufw allow 22/tcp comment "SSH" > /dev/null 2>&1 || true
        ufw allow 80/tcp comment "HTTP" > /dev/null 2>&1 || true
        ufw allow 443/tcp comment "HTTPS" > /dev/null 2>&1 || true
        ufw allow "${FW_API_PORT}/tcp" comment "N8N Manager API" > /dev/null 2>&1 || true
        # Fechar portas admin que nao devem ficar expostas publicamente
        ufw delete allow 8080/tcp > /dev/null 2>&1 || true
        ufw delete allow 15672/tcp > /dev/null 2>&1 || true
        ufw --force enable > /dev/null 2>&1 || true
        log "UFW configurado (SSH, HTTP, HTTPS, API:${FW_API_PORT})"
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
        firewall-cmd --permanent --add-port="${FW_API_PORT}/tcp" > /dev/null 2>&1 || true
        # Fechar portas admin que nao devem ficar expostas publicamente
        firewall-cmd --permanent --remove-port=8080/tcp > /dev/null 2>&1 || true
        firewall-cmd --permanent --remove-port=15672/tcp > /dev/null 2>&1 || true
        firewall-cmd --reload > /dev/null 2>&1 || true
        log "Firewalld configurado (SSH, HTTP, HTTPS, API:${FW_API_PORT})"
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
    # Preservar .env antes do reset
    [ -f .env ] && cp .env /tmp/n8n-manager-env-backup
    git fetch origin main 2>/dev/null || warn "Falha ao buscar atualizacoes do GitHub"
    git reset --hard origin/main 2>/dev/null || warn "Falha ao atualizar codigo"
    # Restaurar .env
    [ -f /tmp/n8n-manager-env-backup ] && mv /tmp/n8n-manager-env-backup .env
else
    if [ -d "$PROJECT_DIR" ]; then
        rm -rf "$PROJECT_DIR"
    fi
    info "Clonando projeto..."
    if ! git clone https://github.com/viniciusdev772/n8n-manager.git "$PROJECT_DIR"; then
        err "Falha ao clonar repositorio. Verifique a conexao com a internet."
    fi
fi

cd "$PROJECT_DIR"

# Criar venv e instalar dependencias
info "Criando ambiente virtual Python..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1 || warn "Falha ao atualizar pip"
if ! pip install -r requirements.txt > /dev/null 2>&1; then
    deactivate
    err "Falha ao instalar dependencias Python. Verifique requirements.txt e a conexao."
fi
deactivate

log "Dependencias Python instaladas em venv"

# --- Wizard interativo de configuracao ---

run_wizard() {
    echo ""
    echo -e "${CYAN}-----------------------------------------------${NC}"
    echo -e "${CYAN}  Configuracao do N8N Manager${NC}"
    echo -e "${CYAN}-----------------------------------------------${NC}"
    echo ""

    # 1. Dominio
    ask "Dominio base para instancias (ex: n8n.seudominio.com)" "" INPUT_DOMAIN
    while [ -z "$INPUT_DOMAIN" ]; do
        warn "Dominio nao pode ser vazio"
        ask "Dominio base para instancias" "" INPUT_DOMAIN
    done

    # Extrair dominio raiz para sugerir email (ex: n8n.exemplo.com -> exemplo.com)
    ROOT_DOMAIN=$(echo "$INPUT_DOMAIN" | awk -F. '{if(NF>2) print $(NF-1)"."$NF; else print $0}')
    DEFAULT_EMAIL="admin@${ROOT_DOMAIN}"

    # 2. Email SSL
    ask "Email para certificados SSL" "$DEFAULT_EMAIL" INPUT_EMAIL
    while [ -z "$INPUT_EMAIL" ]; do
        warn "Email nao pode ser vazio"
        ask "Email para certificados SSL" "$DEFAULT_EMAIL" INPUT_EMAIL
    done

    # 3. Cloudflare token
    echo ""
    info "O token Cloudflare e necessario para emitir certificados SSL automaticos."
    info "Gere em: https://dash.cloudflare.com/profile/api-tokens"
    info "Permissao necessaria: Zone > DNS > Edit"
    echo ""
    ask "Cloudflare DNS API Token (Enter para pular)" "" INPUT_CF_TOKEN
    if [ -z "$INPUT_CF_TOKEN" ]; then
        warn "SSL nao vai funcionar sem o token. Voce pode configurar depois em:"
        warn "  nano $PROJECT_DIR/.env"
    fi

    # 4. Porta
    ask "Porta da API" "5050" INPUT_PORT
    while ! [[ "$INPUT_PORT" =~ ^[0-9]+$ ]]; do
        warn "Porta deve ser um numero"
        ask "Porta da API" "5050" INPUT_PORT
    done

    # Token fixo da API + senha RabbitMQ gerada
    GEN_API_TOKEN="3ddb61bb99c56a8ef825f303a44b71fd706bd3aa38e6e06b443b7268d221e020"
    GEN_RABBITMQ_PASS=$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 32)

    # Resumo
    echo ""
    echo -e "${CYAN}-----------------------------------------------${NC}"
    echo -e "${CYAN}  Resumo da configuracao${NC}"
    echo -e "${CYAN}-----------------------------------------------${NC}"
    echo -e "  ${BOLD}Dominio:${NC}     $INPUT_DOMAIN"
    echo -e "  ${BOLD}Email SSL:${NC}   $INPUT_EMAIL"
    if [ -n "$INPUT_CF_TOKEN" ]; then
        echo -e "  ${BOLD}CF Token:${NC}    ****${INPUT_CF_TOKEN: -4}"
    else
        echo -e "  ${BOLD}CF Token:${NC}    ${YELLOW}NAO CONFIGURADO${NC}"
    fi
    echo -e "  ${BOLD}Porta API:${NC}   $INPUT_PORT"
    echo -e "  ${BOLD}API Token:${NC}   ${GEN_API_TOKEN:0:12}... (gerado)"
    echo ""

    if ! confirm "Confirma estas configuracoes? (s/N)" "s"; then
        info "Cancelado. Execute o setup novamente."
        exit 0
    fi

    # Detectar IP publico
    SERVER_IP=$(curl -4 -sf --max-time 5 https://ifconfig.me 2>/dev/null \
        || curl -4 -sf --max-time 5 https://api.ipify.org 2>/dev/null \
        || curl -4 -sf --max-time 5 https://icanhazip.com 2>/dev/null \
        || hostname -I | awk '{print $1}')

    # Gerar .env
    cat > "$PROJECT_DIR/.env" << ENVFILE
# === Gerado pelo setup.sh em $(date '+%Y-%m-%d %H:%M:%S') ===

# Token de autenticacao da API (use este mesmo token no Next.js)
API_AUTH_TOKEN=$GEN_API_TOKEN

# Dominio base para instancias (ex: nome.BASE_DOMAIN)
BASE_DOMAIN=$INPUT_DOMAIN

# Email para certificados SSL (Let's Encrypt via Traefik)
ACME_EMAIL=$INPUT_EMAIL

# Cloudflare DNS API Token (para SSL wildcard via DNS Challenge)
CF_DNS_API_TOKEN=$INPUT_CF_TOKEN

# Rede Docker para Traefik + instancias
DOCKER_NETWORK=n8n-public

# RabbitMQ (job queue para criacao de instancias)
RABBITMQ_HOST=127.0.0.1
RABBITMQ_PORT=5672
RABBITMQ_USER=n8n_manager
RABBITMQ_PASSWORD=$GEN_RABBITMQ_PASS

# Redis (status de jobs)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379

# Porta do servidor FastAPI
SERVER_PORT=$INPUT_PORT

# CORS — origens permitidas (separadas por virgula, ou * para todas)
ALLOWED_ORIGINS=*

# Cleanup automatico
CLEANUP_MAX_AGE_DAYS=5
CLEANUP_INTERVAL_SECONDS=3600

# Timezone padrao para instancias N8N
DEFAULT_TIMEZONE=America/Sao_Paulo

# Job status TTL (Redis)
JOB_TTL=600
JOB_CLEANUP_TTL=300
ENVFILE

    log "Arquivo .env gerado"
    info "IP do servidor: $SERVER_IP"
    warn "IMPORTANTE: Copie o API_AUTH_TOKEN para o seu Next.js"
    warn "  cat $PROJECT_DIR/.env | grep API_AUTH_TOKEN"
}

# Decidir se roda wizard ou mantem .env existente
if [ ! -f "$PROJECT_DIR/.env" ]; then
    run_wizard
else
    log "Arquivo .env ja existe"
    echo ""
    if confirm "Deseja reconfigurar o .env? (s/N)" "N"; then
        run_wizard
    else
        info "Mantendo .env existente. Validando..."

        # Validar variaveis obrigatorias
        ENV_WARNINGS=0
        _val=$(grep -oP 'API_AUTH_TOKEN=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "")
        if [ -z "$_val" ]; then
            warn ".env: API_AUTH_TOKEN esta vazio — API nao vai funcionar"
            ENV_WARNINGS=$((ENV_WARNINGS + 1))
        fi
        _val=$(grep -oP 'CF_DNS_API_TOKEN=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "")
        if [ -z "$_val" ]; then
            warn ".env: CF_DNS_API_TOKEN esta vazio — SSL nao vai funcionar"
            ENV_WARNINGS=$((ENV_WARNINGS + 1))
        fi
        _val=$(grep -oP 'RABBITMQ_PASSWORD=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "")
        if [ -z "$_val" ] || [ "$_val" = "guest" ]; then
            warn ".env: RABBITMQ_PASSWORD esta vazio ou inseguro"
            ENV_WARNINGS=$((ENV_WARNINGS + 1))
        fi
        if [ "$ENV_WARNINGS" -eq 0 ]; then
            log "Variaveis obrigatorias do .env: OK"
        else
            warn "Edite o .env: nano $PROJECT_DIR/.env"
        fi

        # Propagar variaveis novas (append sem sobrescrever existentes)
        NEW_VARS_ADDED=0
        declare -A DEFAULT_VARS=(
            ["ALLOWED_ORIGINS"]="*"
            ["CLEANUP_MAX_AGE_DAYS"]="5"
            ["CLEANUP_INTERVAL_SECONDS"]="3600"
            ["DEFAULT_TIMEZONE"]="America/Sao_Paulo"
            ["JOB_TTL"]="600"
            ["JOB_CLEANUP_TTL"]="300"
        )

        for var in "${!DEFAULT_VARS[@]}"; do
            if ! grep -q "^${var}=" "$PROJECT_DIR/.env" 2>/dev/null; then
                echo "${var}=${DEFAULT_VARS[$var]}" >> "$PROJECT_DIR/.env"
                NEW_VARS_ADDED=$((NEW_VARS_ADDED + 1))
            fi
        done
        if [ "$NEW_VARS_ADDED" -gt 0 ]; then
            log "$NEW_VARS_ADDED nova(s) variavel(is) adicionada(s) ao .env"
        fi
    fi
fi

# --- 10. Criar usuario dedicado + servico systemd ---

info "Configurando usuario dedicado..."
if ! id -u n8n-manager > /dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin n8n-manager
    log "Usuario n8n-manager criado"
else
    log "Usuario n8n-manager ja existe"
fi

# Adicionar ao grupo docker para acesso ao socket
usermod -aG docker n8n-manager 2>/dev/null || true

# Dar permissao ao projeto
chown -R n8n-manager:n8n-manager "$PROJECT_DIR"

info "Criando servico systemd..."

cat > /etc/systemd/system/n8n-manager.service << SERVICE
[Unit]
Description=N8N Instance Manager
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
User=n8n-manager
Group=n8n-manager
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

# --- 12. Garantir Traefik na rede correta ---

DOCKER_NET=$(grep -oP 'DOCKER_NETWORK=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "n8n-public")
DOCKER_NET="${DOCKER_NET:-n8n-public}"
info "Verificando rede Docker '${DOCKER_NET}'..."

# Criar rede se nao existir
if ! docker network inspect "$DOCKER_NET" > /dev/null 2>&1; then
    docker network create "$DOCKER_NET" > /dev/null 2>&1
    log "Rede '$DOCKER_NET' criada"
else
    log "Rede '$DOCKER_NET' existe"
fi

# Detectar qualquer container Traefik rodando (EasyPanel usa nomes como traefik.1.xxx)
TRAEFIK_CONTAINER=""
for cname in $(docker ps --format '{{.Names}}' 2>/dev/null); do
    if echo "$cname" | grep -qi "traefik"; then
        TRAEFIK_CONTAINER="$cname"
        break
    fi
done

if [ -n "$TRAEFIK_CONTAINER" ]; then
    info "Traefik encontrado: $TRAEFIK_CONTAINER"
    TRAEFIK_IMAGE=$(docker inspect "$TRAEFIK_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo "unknown")
    info "Imagem atual: $TRAEFIK_IMAGE"

    # Verificar se Traefik e um servico Swarm (EasyPanel)
    IS_SWARM_SERVICE=$(docker service ls --format '{{.Name}}' 2>/dev/null | grep -i traefik || echo "")

    if [ -n "$IS_SWARM_SERVICE" ]; then
        info "Traefik e um servico Swarm (EasyPanel). Atualizando imagem para v3.6..."
        docker service update --image traefik:v3.6 "$IS_SWARM_SERVICE" > /dev/null 2>&1 || warn "Falha ao atualizar servico Swarm"
        sleep 5

        # Re-detectar container apos update
        TRAEFIK_CONTAINER=""
        for cname in $(docker ps --format '{{.Names}}' 2>/dev/null); do
            if echo "$cname" | grep -qi "traefik"; then
                TRAEFIK_CONTAINER="$cname"
                break
            fi
        done
        [ -n "$TRAEFIK_CONTAINER" ] && log "Traefik Swarm atualizado para v3.6: $TRAEFIK_CONTAINER"
    fi

    # Conectar na rede n8n-public se nao estiver
    if [ -n "$TRAEFIK_CONTAINER" ]; then
        TRAEFIK_NETS=$(docker inspect "$TRAEFIK_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || echo "")
        if echo "$TRAEFIK_NETS" | grep -q "$DOCKER_NET"; then
            log "Traefik ja esta na rede '$DOCKER_NET'"
        else
            info "Conectando Traefik na rede '$DOCKER_NET'..."
            docker network connect "$DOCKER_NET" "$TRAEFIK_CONTAINER" > /dev/null 2>&1 || warn "Falha ao conectar Traefik na rede"
            log "Traefik conectado na rede '$DOCKER_NET'"
        fi
    fi
else
    # Nenhum Traefik encontrado — criar via config_traefik.py
    info "Nenhum Traefik encontrado. Criando com Cloudflare DNS Challenge..."

    # Aguardar portas liberarem
    for i in $(seq 1 10); do
        if ! ss -tlnp | grep -qE ':80\s|:443\s'; then
            break
        fi
        sleep 1
    done

    # Exportar variaveis do .env para o config_traefik.py
    CF_DNS_API_TOKEN=$(grep -oP 'CF_DNS_API_TOKEN=\K.*' "$PROJECT_DIR/.env" 2>/dev/null) || true
    ACME_EMAIL=$(grep -oP 'ACME_EMAIL=\K.*' "$PROJECT_DIR/.env" 2>/dev/null) || true
    ACME_EMAIL="${ACME_EMAIL:-lojasketchware@gmail.com}"
    export CF_DNS_API_TOKEN ACME_EMAIL

    if [ -z "$CF_DNS_API_TOKEN" ]; then
        warn "CF_DNS_API_TOKEN nao configurado no .env — Traefik nao conseguira emitir certificados SSL"
        warn "Configure em: nano $PROJECT_DIR/.env"
        warn "Depois execute: cd $PROJECT_DIR && python3 config_traefik.py"
    else
        cd "$PROJECT_DIR"
        $PROJECT_DIR/venv/bin/python config_traefik.py || warn "Falha ao criar Traefik (verifique config_traefik.py)"
        if docker ps --format '{{.Names}}' | grep -q "traefik"; then
            log "Traefik criado via config_traefik.py"
        fi
        cd "$PROJECT_DIR"
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
    # Verificar CF_DNS_API_TOKEN (critico para SSL)
    _cf_val=$(grep -oP 'CF_DNS_API_TOKEN=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "")
    if [ -z "$_cf_val" ]; then
        warn "CF_DNS_API_TOKEN: NAO CONFIGURADO (SSL nao vai funcionar)"
        ERRORS=$((ERRORS + 1))
    else
        log "CF_DNS_API_TOKEN: configurado"
    fi
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
FINAL_PORT=$(grep -oP 'SERVER_PORT=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo "5050")
FINAL_PORT="${FINAL_PORT:-5050}"
sleep 2
if curl -sf "http://localhost:${FINAL_PORT}/health" > /dev/null 2>&1; then
    log "API health check: OK (porta ${FINAL_PORT})"
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
    echo -e "  ${GREEN}Servico rodando!${NC} Teste com: ${CYAN}curl http://localhost:${FINAL_PORT}/health${NC}"
    echo -e "  Logs: ${CYAN}journalctl -u n8n-manager -f${NC}"
fi
echo ""
echo -e "  ${YELLOW}Portas abertas:${NC} 22 (SSH), 80 (HTTP), 443 (HTTPS), ${FINAL_PORT} (API)"
echo ""
