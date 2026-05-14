#!/usr/bin/env bash
# ==============================================================================
# NDI Controller – Ubuntu Studio deployment setup
#
# Usage:
#   1. Copy the project to /opt/ndi-controller
#   2. chmod +x deploy/setup.sh
#   3. sudo ./deploy/setup.sh
# ==============================================================================
set -euo pipefail

# ── paths ─────────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/ndi-controller"
SERVICE_USER="${SUDO_USER:-$(whoami)}"
SERVICE_NAME="ndi-controller"

BACKEND_DIR="$INSTALL_DIR/backend/ndi-controller-api"
FRONTEND_DIR="$INSTALL_DIR/frontend"

# ── colours ───────────────────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${G}[INFO]${NC}  $*"; }
warn()  { echo -e "${Y}[WARN]${NC}  $*"; }
error() { echo -e "${R}[ERROR]${NC} $*"; exit 1; }

# ── pre-flight checks ────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Run with sudo:  sudo $0"
[[ "$SERVICE_USER" == "root" ]] && error "Do not run as root directly. Use: sudo ./setup.sh"

info "Install dir : $INSTALL_DIR"
info "Service user: $SERVICE_USER"

# ── 1. System packages ───────────────────────────────────────────────────────
info "Installing system packages…"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    build-essential pkg-config \
    ffmpeg \
    avahi-daemon libndi5 \
    curl git

# Node 20 via NodeSource (skip if already present)
if ! command -v node &>/dev/null || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]]; then
    info "Installing Node.js 20 LTS…"
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -qq nodejs
fi

info "Python : $(python3 --version)"
info "Node   : $(node --version)"
info "npm    : $(npm --version)"

# ── 2. Validate project directory ────────────────────────────────────────────
[[ ! -d "$BACKEND_DIR" ]] && error "Backend not found at $BACKEND_DIR\nCopy the project to $INSTALL_DIR first."
[[ ! -f "$BACKEND_DIR/main.py" ]] && error "main.py not found in $BACKEND_DIR"
[[ ! -d "$FRONTEND_DIR" ]] && error "Frontend not found at $FRONTEND_DIR"

# ── 3. Python virtual-env + deps ─────────────────────────────────────────────
info "Setting up Python venv…"
sudo -u "$SERVICE_USER" python3 -m venv "$BACKEND_DIR/.venv"
sudo -u "$SERVICE_USER" "$BACKEND_DIR/.venv/bin/pip" install --upgrade pip wheel

if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
    info "Installing Python dependencies…"
    sudo -u "$SERVICE_USER" "$BACKEND_DIR/.venv/bin/pip" install -r "$BACKEND_DIR/requirements.txt"
else
    warn "No requirements.txt found — installing common defaults"
    sudo -u "$SERVICE_USER" "$BACKEND_DIR/.venv/bin/pip" install \
        fastapi uvicorn[standard] websockets python-osc obsws-python aiofiles
fi

# ── 4. Frontend build ────────────────────────────────────────────────────────
info "Installing Node dependencies…"
cd "$FRONTEND_DIR"
sudo -u "$SERVICE_USER" npm ci --prefer-offline 2>/dev/null || sudo -u "$SERVICE_USER" npm install

info "Building React frontend…"
sudo -u "$SERVICE_USER" npm run build

[[ ! -d "$FRONTEND_DIR/dist" ]] && error "Frontend build failed — dist/ not created."
info "Frontend built → $FRONTEND_DIR/dist"

# ── 5. Ownership ─────────────────────────────────────────────────────────────
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

# ── 6. Systemd service ───────────────────────────────────────────────────────
info "Installing systemd service…"
cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=NDI Controller (FastAPI + React)
After=network-online.target avahi-daemon.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${BACKEND_DIR}
Environment="PATH=${BACKEND_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin"

ExecStart=${BACKEND_DIR}/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=${BACKEND_DIR}/data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl start  "${SERVICE_NAME}.service"

# ── 7. Summary ───────────────────────────────────────────────────────────────
echo ""
info "=============================="
info " Deployment complete!"
info "=============================="
info ""
info " Service status  : systemctl status ${SERVICE_NAME}"
info " View logs       : journalctl -u ${SERVICE_NAME} -f"
info " Restart         : sudo systemctl restart ${SERVICE_NAME}"
info " Stop            : sudo systemctl stop ${SERVICE_NAME}"
info ""
info " App URL         : http://$(hostname -I | awk '{print $1}'):8000"
info ""
