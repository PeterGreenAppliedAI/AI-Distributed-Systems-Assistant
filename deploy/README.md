# DevMesh Shipper Deployment

Scripts for deploying the log shipper to remote nodes.

## Quick Start

1. Copy and configure your nodes:
   ```bash
   cp nodes.conf.example nodes.conf
   # Edit nodes.conf with your node details
   ```

2. Deploy to a single node:
   ```bash
   ./deploy_to_node.sh gpu-node
   ```

3. Deploy to all nodes:
   ```bash
   ./deploy_all.sh
   ```

4. Check status across all nodes:
   ```bash
   ./check_status.sh
   ```

## Configuration

### nodes.conf format

```
# IP          NODE_NAME      API_HOST       SSH_USER
10.0.0.19     gpu-node       10.0.0.20      myuser
192.168.1.50  remote-node    192.168.1.10   admin
```

- **IP**: Node's IP address
- **NODE_NAME**: Identifier for this node (appears in logs)
- **API_HOST**: IP where DevMesh API is running
- **SSH_USER**: User for SSH access (needs sudo)

### Multi-subnet setups

If nodes are on different networks, use the appropriate API IP for each:
- Nodes on `10.0.0.x` might use API at `10.0.0.20`
- Nodes on `192.168.1.x` might use API at `192.168.1.184`

## Requirements

### On deployment machine
- SSH access to target nodes
- SSH key-based auth (recommended)

### On target nodes
- Linux with systemd
- Python 3.8+
- Network access to DevMesh API (port 8000)
- `journalctl` command available

## Manual Installation

If automated deployment doesn't work, copy files manually:

```bash
# On target node
sudo mkdir -p /opt/devmesh/shipper
# Copy shipper files to /opt/devmesh/shipper/
# Run: sudo ./install_shipper.sh <NODE_NAME> <API_HOST>
```

## Troubleshooting

### Check service status
```bash
ssh user@node 'sudo systemctl status devmesh-shipper'
```

### View shipper logs
```bash
ssh user@node 'sudo journalctl -u devmesh-shipper -f'
```

### Test API connectivity from node
```bash
ssh user@node 'curl http://API_HOST:8000/health'
```
