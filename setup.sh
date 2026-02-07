#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  N8N Manager — Setup do Servidor VPS
#  Compatível com: Ubuntu, Debian, CentOS, Fedora, AlmaLinux, Rocky
#  Executa: curl -fsSL https://raw.githubusercontent.com/.../setup.sh | bash
# ═══════════════════════════════════════════════════════════

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info() { echo -e "${CYAN}[i]${NC} $1"; }

# ─── Verificações iniciais ────────────────────────────────

[[ $EUID -ne 0 ]] && err "Execute como root: sudo bash setup.sh"

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo -e "${CYAN}  N8N Instance Manager — Setup VPS${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════${NC}"
echo ""

# ─── Detectar distro ──────────────────────────────────────

detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO=$ID
        DISTRO_VERSION=$VERSION_ID
        DISTRO_NAME=$PRETTY_NAME
    elif [ -f /etc/redhat-release ]; then
        DISTRO="centos"
        DISTRO_VERSION=$(rpm -q --qf '%{VERSION}' centos-release 2>/dev/null || echo "unknown")
        DISTRO_NAME=$(cat /etc/redhat-release)
    else
        err "Distribuição Linux não detectada"
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
        warn "Distro '$DISTRO' não mapeada, tentando com apt..."
        PKG="apt"
        PKG_UPDATE="apt-get update -qq"
        PKG_INSTALL="apt-get install -y -qq"
        FIREWALL_CMD="ufw"
        ;;
esac

log "Gerenciador de pacotes: $PKG"

# ─── 1. Atualizar sistema ─────────────────────────────────

info "Atualizando pacotes do sistema..."
$PKG_UPDATE > /dev/null 2>&1

case "$PKG" in
    apt)    apt-get upgrade -y -qq > /dev/null 2>&1 ;;
    yum)    yum update -y -q > /dev/null 2>&1 ;;
    dnf)    dnf upgrade -y -q > /dev/null 2>&1 ;;
    pacman) pacman -Syu --noconfirm > /dev/null 2>&1 ;;
    zypper) zypper update -y -q > /dev/null 2>&1 ;;
esac
log "Sistema atualizado"

# ─── 2. Instalar dependências básicas ─────────────────────

info "Instalando dependências..."
BASIC_DEPS="curl wget git htop nano unzip"

case "$PKG" in
    apt)    $PKG_INSTALL $BASIC_DEPS ca-certificates gnupg lsb-release software-properties-common > /dev/null 2>&1 ;;
    yum)    $PKG_INSTALL $BASIC_DEPS yum-utils > /dev/null 2>&1 ;;
    dnf)    $PKG_INSTALL $BASIC_DEPS dnf-plugins-core > /dev/null 2>&1 ;;
    pacman) $PKG_INSTALL $BASIC_DEPS > /dev/null 2>&1 ;;
    zypper) $PKG_INSTALL $BASIC_DEPS > /dev/null 2>&1 ;;
esac
log "Dependências instaladas"

# ─── 3. Configurar Swap (se < 4GB RAM) ────────────────────

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
    log "Swap já configurado (${SWAP_CURRENT}MB)"
fi

# ─── 4. Instalar Docker ───────────────────────────────────

if command -v docker &> /dev/null; then
    DOCKER_VER=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
    log "Docker já instalado (v$DOCKER_VER)"
else
    info "Instalando Docker via script oficial..."
    curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
    log "Docker instalado"
fi

# Garantir que Docker está rodando e habilitado
systemctl enable docker > /dev/null 2>&1
systemctl start docker > /dev/null 2>&1
log "Docker ativo e habilitado no boot"

# Verificar Docker Compose (plugin)
if docker compose version &> /dev/null; then
    COMPOSE_VER=$(docker compose version --short 2>/dev/null)
    log "Docker Compose plugin: v$COMPOSE_VER"
else
    warn "Docker Compose plugin não encontrado, instalando..."
    $PKG_INSTALL docker-compose-plugin > /dev/null 2>&1 || true
fi

# ─── 5. Hardening Docker ──────────────────────────────────

info "Aplicando hardening no Docker..."

mkdir -p /etc/docker

# daemon.json com boas práticas de produção
cat > /etc/docker/daemon.json << 'DAEMON_JSON'
{
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
  "no-new-privileges": true,
  "live-restore": true,
  "userland-proxy": false,
  "storage-driver": "overlay2"
}
DAEMON_JSON

systemctl restart docker > /dev/null 2>&1
log "Docker hardening aplicado (log rotation, ulimits, no-new-privileges)"

# ─── 6. Instalar Python 3.11+ ─────────────────────────────

if command -v python3 &> /dev/null; then
    PY_VER=$(python3 --version 2>/dev/null | grep -oP '\d+\.\d+')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
        log "Python $PY_VER já instalado"
    else
        info "Python $PY_VER muito antigo, instalando 3.11+..."
        case "$PKG" in
            apt) $PKG_INSTALL python3.11 python3.11-venv python3-pip > /dev/null 2>&1 || $PKG_INSTALL python3 python3-venv python3-pip > /dev/null 2>&1 ;;
            yum|dnf) $PKG_INSTALL python3.11 python3-pip > /dev/null 2>&1 || $PKG_INSTALL python3 python3-pip > /dev/null 2>&1 ;;
            *) $PKG_INSTALL python3 python3-pip > /dev/null 2>&1 ;;
        esac
    fi
else
    info "Instalando Python 3..."
    case "$PKG" in
        apt) $PKG_INSTALL python3 python3-venv python3-pip > /dev/null 2>&1 ;;
        yum|dnf) $PKG_INSTALL python3 python3-pip > /dev/null 2>&1 ;;
        pacman) $PKG_INSTALL python python-pip > /dev/null 2>&1 ;;
        zypper) $PKG_INSTALL python3 python3-pip > /dev/null 2>&1 ;;
    esac
fi

log "Python $(python3 --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+')"

# ─── 7. Firewall ──────────────────────────────────────────

info "Configurando firewall..."

if [ "$FIREWALL_CMD" = "ufw" ]; then
    if command -v ufw &> /dev/null; then
        ufw --force reset > /dev/null 2>&1
        ufw default deny incoming > /dev/null 2>&1
        ufw default allow outgoing > /dev/null 2>&1
        ufw allow 22/tcp comment "SSH" > /dev/null 2>&1
        ufw allow 80/tcp comment "HTTP" > /dev/null 2>&1
        ufw allow 443/tcp comment "HTTPS" > /dev/null 2>&1
        ufw allow 5050/tcp comment "N8N Manager API" > /dev/null 2>&1
        ufw allow 8080/tcp comment "Traefik Dashboard" > /dev/null 2>&1
        ufw --force enable > /dev/null 2>&1
        log "UFW configurado (SSH, HTTP, HTTPS, API, Traefik)"
    else
        $PKG_INSTALL ufw > /dev/null 2>&1 || warn "Não foi possível instalar UFW"
    fi
elif [ "$FIREWALL_CMD" = "firewalld" ]; then
    if command -v firewall-cmd &> /dev/null; then
        systemctl enable firewalld > /dev/null 2>&1
        systemctl start firewalld > /dev/null 2>&1
        firewall-cmd --permanent --add-service=ssh > /dev/null 2>&1
        firewall-cmd --permanent --add-service=http > /dev/null 2>&1
        firewall-cmd --permanent --add-service=https > /dev/null 2>&1
        firewall-cmd --permanent --add-port=5050/tcp > /dev/null 2>&1
        firewall-cmd --permanent --add-port=8080/tcp > /dev/null 2>&1
        firewall-cmd --reload > /dev/null 2>&1
        log "Firewalld configurado (SSH, HTTP, HTTPS, API, Traefik)"
    else
        $PKG_INSTALL firewalld > /dev/null 2>&1 || warn "Não foi possível instalar firewalld"
    fi
fi

# ─── 8. Otimizações de kernel ─────────────────────────────

info "Aplicando otimizações de kernel..."

cat >> /etc/sysctl.conf << 'SYSCTL'

# ── N8N Manager: otimizações de rede ──
net.core.somaxconn=65535
net.ipv4.tcp_max_syn_backlog=65535
net.ipv4.ip_local_port_range=1024 65535
net.ipv4.tcp_tw_reuse=1
net.ipv4.tcp_fin_timeout=15
net.core.netdev_max_backlog=65535

# ── Limites de arquivos ──
fs.file-max=2097152
fs.inotify.max_user_watches=524288
SYSCTL

sysctl -p > /dev/null 2>&1
log "Kernel otimizado (rede, file descriptors)"

# Limites de arquivos para o usuário
cat > /etc/security/limits.d/docker.conf << 'LIMITS'
*    soft    nofile    65536
*    hard    nofile    65536
*    soft    nproc     4096
*    hard    nproc     4096
LIMITS

log "Limites de arquivos configurados"

# ─── 9. Clonar e configurar o projeto ─────────────────────

PROJECT_DIR="/opt/n8n-manager"

if [ -d "$PROJECT_DIR" ]; then
    info "Projeto já existe em $PROJECT_DIR, atualizando..."
    cd "$PROJECT_DIR"
    git pull origin main > /dev/null 2>&1 || true
else
    info "Clonando projeto..."
    git clone https://github.com/viniciusdev772/n8n-manager.git "$PROJECT_DIR" > /dev/null 2>&1
fi

cd "$PROJECT_DIR"

# Criar venv e instalar dependências
info "Criando ambiente virtual Python..."
python3 -m venv venv > /dev/null 2>&1
source venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1
deactivate

log "Dependências Python instaladas em venv"

# Criar .env se não existir
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    warn "Arquivo .env criado a partir do .env.example — EDITE COM SUAS CREDENCIAIS!"
    warn "  nano $PROJECT_DIR/.env"
fi

# ─── 10. Criar serviço systemd ────────────────────────────

info "Criando serviço systemd..."

cat > /etc/systemd/system/n8n-manager.service << SERVICE
[Unit]
Description=N8N Instance Manager
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

# Hardening do serviço
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$PROJECT_DIR /var/run/docker.sock
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable n8n-manager > /dev/null 2>&1
log "Serviço n8n-manager criado e habilitado no boot"

# ─── Resumo final ─────────────────────────────────────────

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Setup concluído com sucesso!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Distro:${NC}       $DISTRO_NAME"
echo -e "  ${CYAN}RAM:${NC}          ${TOTAL_RAM_MB}MB"
echo -e "  ${CYAN}Swap:${NC}         $(free -m | awk '/Swap:/ {print $2}')MB"
echo -e "  ${CYAN}Docker:${NC}       $(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)"
echo -e "  ${CYAN}Python:${NC}       $(python3 --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+')"
echo -e "  ${CYAN}Projeto:${NC}      $PROJECT_DIR"
echo ""
echo -e "  ${YELLOW}Próximos passos:${NC}"
echo -e "  1. Edite as credenciais:  ${CYAN}nano $PROJECT_DIR/.env${NC}"
echo -e "  2. Inicie o serviço:      ${CYAN}systemctl start n8n-manager${NC}"
echo -e "  3. Veja os logs:          ${CYAN}journalctl -u n8n-manager -f${NC}"
echo -e "  4. Teste:                 ${CYAN}curl http://localhost:5050/health${NC}"
echo ""
echo -e "  ${YELLOW}Portas abertas:${NC} 22 (SSH), 80 (HTTP), 443 (HTTPS), 5050 (API), 8080 (Traefik)"
echo ""
