# DevMesh Platform - Next Steps

**Last Updated**: December 23, 2025

This document outlines the remaining tasks to complete the multi-node deployment.

---

## Current Status

### Completed
- [x] Phase 1 log collection running on dev-services
- [x] 203,017 logs accumulated (~26 days of data)
- [x] API and shipper services stable
- [x] Deployment scripts created (`deploy/`)
- [x] Error handling architecture implemented
- [x] Code review against design principles completed

### Pending
- [ ] Deploy shipper to 7 additional nodes
- [ ] Set up SSH key-based access for automation
- [ ] Enable SSH on gpu-node and monitoring-vm

---

## Node Deployment Status

| Node | IP | SSH Status | Shipper Status |
|------|-----|------------|----------------|
| dev-services | 10.0.0.20 | N/A | **Running** |
| gpu-node | 10.0.0.19 | Blocked | Pending |
| mariadb-vm | 10.0.0.18 | Open (needs key) | Pending |
| monitoring-vm | 10.0.0.17 | Blocked | Pending |
| gpu-node-3060 | 10.0.0.14 | Open (needs key) | Pending |
| electrical-estimator | 10.0.0.13 | Open (needs key) | Pending |
| postgres-vm | 192.168.1.220 | Open (needs key) | Pending |
| teaching | 192.168.1.227 | Open (needs key) | Pending |

---

## Deployment Instructions

### Prerequisites for Each Node

1. **Linux with systemd** (journald for log source)
2. **Python 3.8+** with pip
3. **Network access** to DevMesh API:
   - 10.0.0.x nodes → `http://10.0.0.20:8000`
   - 192.168.x nodes → `http://192.168.1.184:8000`

### Option A: Automated Deployment (Requires SSH Keys)

#### 1. Generate SSH key on dev-services
```bash
ssh-keygen -t ed25519 -f ~/.ssh/devmesh_deploy -N ""
```

#### 2. Copy public key to each node
```bash
# For nodes with tadeu718 user
ssh-copy-id -i ~/.ssh/devmesh_deploy.pub tadeu718@10.0.0.14
ssh-copy-id -i ~/.ssh/devmesh_deploy.pub tadeu718@192.168.1.227

# For nodes with root user
ssh-copy-id -i ~/.ssh/devmesh_deploy.pub root@10.0.0.18
ssh-copy-id -i ~/.ssh/devmesh_deploy.pub root@10.0.0.13
ssh-copy-id -i ~/.ssh/devmesh_deploy.pub root@192.168.1.220
```

#### 3. Deploy using scripts
```bash
cd /home/tadeu718/devmesh-platform/deploy

# Deploy to single node
./deploy_to_node.sh gpu-node-3060

# Or deploy to all configured nodes
./deploy_all.sh

# Check status across all nodes
./check_status.sh
```

### Option B: Manual Deployment (No SSH Keys)

#### 1. Create deployment package on dev-services
```bash
cd /home/tadeu718/devmesh-platform
tar czf /tmp/devmesh-shipper.tar.gz \
    shipper/log_shipper_daemon.py \
    shipper/filter_config.py \
    shipper/filter_config.yaml \
    deploy/install_shipper.sh
```

#### 2. Copy to target node (will prompt for password)
```bash
scp /tmp/devmesh-shipper.tar.gz user@NODE_IP:/tmp/
```

#### 3. SSH to target node and install
```bash
ssh user@NODE_IP

# On the remote node:
cd /tmp
tar xzf devmesh-shipper.tar.gz
sudo ./deploy/install_shipper.sh NODE_NAME API_HOST

# Example for gpu-node-3060:
sudo ./deploy/install_shipper.sh gpu-node-3060 10.0.0.20
```

#### 4. Verify installation
```bash
# Check service status
sudo systemctl status devmesh-shipper

# View logs
sudo journalctl -u devmesh-shipper -f

# Verify logs arriving at API
curl "http://API_HOST:8000/query/logs?host=NODE_NAME&limit=5"
```

---

## Node-Specific Notes

### gpu-node (10.0.0.19)
- SSH not accessible - need to enable SSH service or open firewall port 22

### monitoring-vm (10.0.0.17)
- SSH not accessible - need to enable SSH service or open firewall port 22
- Hosts Grafana - consider filtering Grafana internal logs

### postgres-vm (192.168.1.220)
- Not on 10.0.0.x network yet
- Uses API at 192.168.1.184 (dev-services alternate interface)

### teaching (192.168.1.227)
- Micro node, cannot join 10.0.0.x network
- Uses API at 192.168.1.184

---

## Post-Deployment Verification

After deploying to all nodes, verify data is flowing:

```bash
# Check logs per host
curl -s "http://localhost:8000/query/logs?limit=1" | jq '.[] | .host' | sort | uniq -c

# Or query the database directly
mysql -h 10.0.0.18 -u devmesh -p devmesh -e "
SELECT host, COUNT(*) as logs, MAX(timestamp) as latest
FROM log_events
GROUP BY host
ORDER BY latest DESC;
"
```

---

## Troubleshooting

### Shipper not starting
```bash
# Check service status
sudo systemctl status devmesh-shipper

# View detailed logs
sudo journalctl -u devmesh-shipper -n 100

# Common issues:
# - API not reachable: curl http://API_HOST:8000/health
# - Python missing: python3 --version
# - Missing dependencies: /opt/devmesh/venv/bin/pip list
```

### No logs appearing in database
```bash
# Check if shipper is running
sudo systemctl is-active devmesh-shipper

# Check if batches are being sent
sudo journalctl -u devmesh-shipper | grep BATCH

# Test API connectivity from node
curl http://API_HOST:8000/health
```

### SSH access issues
```bash
# Check if SSH is running on target
nc -zv NODE_IP 22

# Check firewall on target node
sudo ufw status
sudo iptables -L -n | grep 22
```

---

## What's After Multi-Node Deployment

Once all nodes are shipping logs:

1. **Update PHASE1_FOUNDATION.md** with multi-node metrics
2. **Phase 2: Embeddings & Semantic Search**
   - Vector embeddings for log messages
   - Similarity search capabilities
3. **Phase 3: Neo4j Integration**
   - Knowledge graph for service relationships
   - GraphRAG queries
4. **Phase 4: LLM Reasoning & Agents**
   - AI-powered incident analysis
   - Automated recommendations
