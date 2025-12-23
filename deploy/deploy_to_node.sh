#!/bin/bash
#
# DevMesh Log Shipper - Deploy to Remote Node
#
# Usage: ./deploy_to_node.sh <node_name_or_ip>
#
# Reads node configuration from nodes.conf
# Copies shipper files and runs install script on remote node
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
NODES_CONF="$SCRIPT_DIR/nodes.conf"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

TARGET="${1:-}"

if [ -z "$TARGET" ]; then
    echo "Usage: $0 <node_name_or_ip>"
    echo ""
    echo "Deploys DevMesh log shipper to a remote node."
    echo "Node must be configured in nodes.conf"
    echo ""
    if [ -f "$NODES_CONF" ]; then
        echo "Available nodes:"
        grep -v '^#' "$NODES_CONF" | grep -v '^$' | while read ip name api user; do
            echo "  - $name ($ip)"
        done
    else
        log_error "nodes.conf not found. Copy nodes.conf.example to nodes.conf and configure."
    fi
    exit 1
fi

# Check nodes.conf exists
if [ ! -f "$NODES_CONF" ]; then
    log_error "nodes.conf not found"
    log_error "Copy nodes.conf.example to nodes.conf and configure your nodes"
    exit 1
fi

# Find node in config (match by name or IP)
NODE_LINE=$(grep -v '^#' "$NODES_CONF" | grep -v '^$' | grep -E "^$TARGET\s|^\S+\s+$TARGET\s" | head -1)

if [ -z "$NODE_LINE" ]; then
    log_error "Node '$TARGET' not found in nodes.conf"
    exit 1
fi

# Parse node config
NODE_IP=$(echo "$NODE_LINE" | awk '{print $1}')
NODE_NAME=$(echo "$NODE_LINE" | awk '{print $2}')
API_HOST=$(echo "$NODE_LINE" | awk '{print $3}')
SSH_USER=$(echo "$NODE_LINE" | awk '{print $4}')

log_info "Deploying to: $NODE_NAME"
log_info "  IP: $NODE_IP"
log_info "  API Host: $API_HOST"
log_info "  SSH User: $SSH_USER"
echo ""

# Create temp directory for deployment package
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

log_step "Creating deployment package..."
mkdir -p "$TEMP_DIR/shipper"
cp "$PROJECT_DIR/shipper/log_shipper_daemon.py" "$TEMP_DIR/shipper/"
cp "$PROJECT_DIR/shipper/filter_config.py" "$TEMP_DIR/shipper/"
cp "$PROJECT_DIR/shipper/filter_config.yaml" "$TEMP_DIR/shipper/"
cp "$SCRIPT_DIR/install_shipper.sh" "$TEMP_DIR/"

log_step "Copying files to $NODE_IP..."
scp -r "$TEMP_DIR"/* "${SSH_USER}@${NODE_IP}:/tmp/devmesh-deploy/"

log_step "Running install script on remote node..."
ssh -t "${SSH_USER}@${NODE_IP}" "cd /tmp/devmesh-deploy && sudo ./install_shipper.sh '$NODE_NAME' '$API_HOST' 8000"

log_step "Cleaning up remote temp files..."
ssh "${SSH_USER}@${NODE_IP}" "rm -rf /tmp/devmesh-deploy"

echo ""
log_info "Deployment complete!"
log_info "Check status: ssh ${SSH_USER}@${NODE_IP} 'sudo systemctl status devmesh-shipper'"
