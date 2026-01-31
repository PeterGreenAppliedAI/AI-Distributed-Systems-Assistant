#!/bin/bash
#
# DevMesh Log Shipper - Remote Node Installation Script
#
# Usage: ./install_shipper.sh <NODE_NAME> [API_HOST] [API_PORT] [API_KEY]
# Example: ./install_shipper.sh gpu-node 10.0.0.20 8000 my-secret-key
#
# This script:
# 1. Creates devmesh service user (H4 - non-root)
# 2. Creates /opt/devmesh directory
# 3. Copies shipper files (including shared transforms)
# 4. Creates Python venv and installs deps
# 5. Configures .env with node-specific settings + API key
# 6. Installs and enables systemd service
#

set -e

NODE_NAME="${1:-}"
API_HOST="${2:-10.0.0.20}"
API_PORT="${3:-8000}"
API_KEY="${4:-}"
INSTALL_DIR="/opt/devmesh"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Validate arguments
if [ -z "$NODE_NAME" ]; then
    log_error "Usage: $0 <NODE_NAME> [API_HOST] [API_PORT] [API_KEY]"
    log_error "Example: $0 gpu-node 10.0.0.20 8000 my-secret-key"
    exit 1
fi

log_info "Installing DevMesh Log Shipper"
log_info "  Node Name: $NODE_NAME"
log_info "  API Host: $API_HOST:$API_PORT"
log_info "  API Key: ${API_KEY:+configured}"
log_info "  Install Dir: $INSTALL_DIR"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root (sudo $0 ...)"
    exit 1
fi

# Check Python3
if ! command -v python3 &> /dev/null; then
    log_error "Python3 not found. Please install: apt install python3 python3-venv"
    exit 1
fi

# H4 - Create devmesh service user (non-root)
if ! id -u devmesh &>/dev/null 2>&1; then
    log_info "Creating devmesh service user..."
    useradd --system --no-create-home --shell /usr/sbin/nologin devmesh
    usermod -aG systemd-journal devmesh
else
    log_info "devmesh user already exists"
    usermod -aG systemd-journal devmesh 2>/dev/null || true
fi

# Create install directory
log_info "Creating directory structure..."
mkdir -p "$INSTALL_DIR/shipper"

# Check if shipper files exist in current directory
if [ ! -f "shipper/log_shipper_daemon.py" ]; then
    log_error "Shipper files not found in current directory"
    log_error "Run this script from the devmesh-platform directory"
    exit 1
fi

# Copy shipper files (including shared transforms M4)
log_info "Copying shipper files..."
cp shipper/log_shipper_daemon.py "$INSTALL_DIR/shipper/"
cp shipper/filter_config.py "$INSTALL_DIR/shipper/"
cp shipper/filter_config.yaml "$INSTALL_DIR/shipper/"
cp shipper/transforms.py "$INSTALL_DIR/shipper/"

# Create .env file
log_info "Creating .env configuration..."
cat > "$INSTALL_DIR/.env" << EOF
# DevMesh Shipper Configuration
# Generated: $(date -Iseconds)
# Node: $NODE_NAME

# API Connection
API_HOST=$API_HOST
API_PORT=$API_PORT

# API Authentication (B1)
API_KEY=$API_KEY

# Shipper Settings
SHIPPER_BATCH_SIZE=50
SHIPPER_CURSOR_FILE=$INSTALL_DIR/shipper/cursor.txt
SHIPPER_FAILED_BATCHES_FILE=$INSTALL_DIR/shipper/failed_batches.jsonl

# Node Identity
NODE_NAME=$NODE_NAME
NODE_HOST=$NODE_NAME
EOF

# Set ownership (H4)
chown -R devmesh:devmesh "$INSTALL_DIR"

# Create Python virtual environment
log_info "Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"

# Install dependencies
log_info "Installing Python dependencies..."
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet requests python-dotenv pyyaml

# Create systemd service (H4 - runs as devmesh, not root)
log_info "Installing systemd service..."
cat > /etc/systemd/system/devmesh-shipper.service << EOF
[Unit]
Description=DevMesh Log Shipper ($NODE_NAME)
After=network.target

[Service]
Type=simple
User=devmesh
Group=devmesh
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin:/usr/bin:/bin:/usr/local/bin"
ExecStart=$INSTALL_DIR/venv/bin/python -u $INSTALL_DIR/shipper/log_shipper_daemon.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
log_info "Reloading systemd..."
systemctl daemon-reload

# Test API connectivity before enabling
log_info "Testing API connectivity..."
if curl -s --connect-timeout 5 "http://$API_HOST:$API_PORT/health" > /dev/null 2>&1; then
    log_info "API is reachable at http://$API_HOST:$API_PORT"
else
    log_warn "API not reachable at http://$API_HOST:$API_PORT"
    log_warn "Service will be installed but may fail to start"
fi

# Enable and start service
log_info "Enabling service..."
systemctl enable devmesh-shipper

log_info "Starting service..."
systemctl start devmesh-shipper

# Wait a moment and check status
sleep 2
if systemctl is-active --quiet devmesh-shipper; then
    log_info "Service started successfully!"
else
    log_error "Service failed to start. Check: journalctl -u devmesh-shipper -n 50"
fi

echo ""
log_info "Installation complete!"
echo ""
echo "Useful commands:"
echo "  Check status:  systemctl status devmesh-shipper"
echo "  View logs:     journalctl -u devmesh-shipper -f"
echo "  Restart:       systemctl restart devmesh-shipper"
echo "  Stop:          systemctl stop devmesh-shipper"
echo ""
echo "Config location: $INSTALL_DIR/.env"
echo "Cursor file:     $INSTALL_DIR/shipper/cursor.txt"
