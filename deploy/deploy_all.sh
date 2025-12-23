#!/bin/bash
#
# DevMesh Log Shipper - Deploy to All Configured Nodes
#
# Usage: ./deploy_all.sh [--dry-run]
#
# Reads all nodes from nodes.conf and deploys to each
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODES_CONF="$SCRIPT_DIR/nodes.conf"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

DRY_RUN=false
if [ "$1" = "--dry-run" ]; then
    DRY_RUN=true
    log_info "DRY RUN - no changes will be made"
fi

if [ ! -f "$NODES_CONF" ]; then
    log_error "nodes.conf not found"
    log_error "Copy nodes.conf.example to nodes.conf and configure your nodes"
    exit 1
fi

echo "========================================"
echo "DevMesh Log Shipper - Deploy to All"
echo "========================================"
echo ""

# Count nodes
NODE_COUNT=$(grep -v '^#' "$NODES_CONF" | grep -v '^$' | wc -l)
log_info "Found $NODE_COUNT nodes in configuration"
echo ""

# Deploy to each node
FAILED=0
SUCCEEDED=0

grep -v '^#' "$NODES_CONF" | grep -v '^$' | while read ip name api user; do
    echo "----------------------------------------"
    log_info "Deploying to: $name ($ip)"

    if [ "$DRY_RUN" = true ]; then
        log_info "[DRY RUN] Would deploy to $name"
        continue
    fi

    if "$SCRIPT_DIR/deploy_to_node.sh" "$name"; then
        log_info "Successfully deployed to $name"
        ((SUCCEEDED++)) || true
    else
        log_error "Failed to deploy to $name"
        ((FAILED++)) || true
    fi
    echo ""
done

echo "========================================"
if [ "$DRY_RUN" = false ]; then
    log_info "Deployment complete"
    log_info "To verify, run: ./check_status.sh"
fi
echo "========================================"
