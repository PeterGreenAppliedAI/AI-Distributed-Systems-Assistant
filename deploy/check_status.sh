#!/bin/bash
#
# DevMesh Log Shipper - Check Status on All Nodes
#
# Usage: ./check_status.sh
#
# Shows shipper status across all configured nodes
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODES_CONF="$SCRIPT_DIR/nodes.conf"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ ! -f "$NODES_CONF" ]; then
    echo -e "${RED}[ERROR]${NC} nodes.conf not found"
    exit 1
fi

echo "========================================"
echo "DevMesh Log Shipper - Status Check"
echo "========================================"
echo ""
printf "%-25s %-15s %-10s\n" "NODE" "IP" "STATUS"
printf "%-25s %-15s %-10s\n" "----" "--" "------"

grep -v '^#' "$NODES_CONF" | grep -v '^$' | while read ip name api user; do
    # Check if service is running
    STATUS=$(ssh -o ConnectTimeout=5 -o BatchMode=yes "${user}@${ip}" \
        "systemctl is-active devmesh-shipper 2>/dev/null" 2>/dev/null || echo "unreachable")

    case "$STATUS" in
        active)
            STATUS_COLOR="${GREEN}running${NC}"
            ;;
        inactive|failed)
            STATUS_COLOR="${RED}$STATUS${NC}"
            ;;
        *)
            STATUS_COLOR="${YELLOW}$STATUS${NC}"
            ;;
    esac

    printf "%-25s %-15s ${STATUS_COLOR}\n" "$name" "$ip"
done

echo ""
echo "========================================"
