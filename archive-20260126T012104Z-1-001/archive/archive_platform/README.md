# Archive Platform Ansible Collection

Multi-Tier High Availability Platform Collection for VMware vSphere CMP

## Architecture Overview

```
[External] 172.16.6.77 (Gateway)
    ↓
[10.x Network] ALB (HAProxy) → Web Servers
    ↓
[20.x Network] DBLB VIP (192.168.20.100)
    ↓
[30.x Network] PostgreSQL Cluster (Patroni + Etcd)
```

## Network Topology

| Subnet | Role | Components |
|--------|------|------------|
| 192.168.40.x | Management | Ansible, SSH Access |
| 192.168.30.x | DB Data | PostgreSQL Replication, Etcd |
| 192.168.20.x | DB VIP | DBLB Heartbeat, VIP (20.100) |
| 192.168.10.x | Web Service | ALB, Web Servers |

## Installation

```bash
# Build collection
ansible-galaxy collection build

# Install collection
ansible-galaxy collection install custom-archive_platform-1.0.0.tar.gz
```

## Usage

### Deploy Entire Platform
```bash
ansible-playbook -i inventory/hosts.ini playbooks/site.yml
```

### Deploy Specific Components (Using Tags)

```bash
# Deploy only Gateway
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags gateway

## Key Configurations & Recent Fixes (2026-01-26)

### 1. Network & Firewall
*   **DBLB**: Added firewalld rules for TCP 5432 (PostgreSQL) and VRRP protocol (Keepalived).
*   **Patroni**: Added firewalld rules for TCP 5432 and 8008 (API).
*   **Monitoring**: Confirmed ports 9090, 3000, 9100 are open.

### 2. Application Configuration (`cmp/main.py`)
*   **DB Connection**: Switched from hardcoded IP to `DB_HOST` env var (DBLB VIP `192.168.20.100`).
*   **Monitoring**: Switched from hardcoded IP to `MONITORING_HOST` env var.
*   **Security**: Added `ENCRYPT_KEY` and `SECRET_KEY` injection via systemd service.

### 3. Troubleshooting Commands
**If DB Cluster is stuck (Zombie State) after snapshot revert:**
```bash
# Force clean Etcd data and re-bootstrap cluster
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags db -e "force_etcd_clean=true"
```

**If Web App fails to start (502 Bad Gateway):**
```bash
# Re-deploy Web role to apply config updates
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags web
```

# Deploy Web Tier (ALB + Web Servers)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags web_tier

# Deploy DB Tier (DB Cluster + DBLB)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags db_tier
# Etcd 데이터 초기화 + DB 클러스터 재구성 + 방화벽 적용
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags db -e "force_etcd_clean=true"

# Deploy Frontend Components (Gateway + ALB + Web)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags frontend

# Deploy Backend Components (DB + DBLB)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags backend

# Deploy multiple specific components
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags gateway,alb,web

# Skip DB deployment
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --skip-tags db
```

### Available Tags

| Tag | Components | Description |
|-----|------------|-------------|
| `gateway` | Gateway | Nginx reverse proxy |
| `alb` | ALB | Application load balancer |
| `web` | Web Servers | Nginx web servers |
| `dblb` | DBLB | Database load balancer |
| `db` | DB Cluster | Etcd + Patroni + PostgreSQL |
| `web_tier` | ALB + Web | Complete web tier |
| `db_tier` | DBLB + DB | Complete database tier |
| `frontend` | Gateway + ALB + Web | All frontend components |
| `backend` | DBLB + DB | All backend components |
| `all` | Everything | All components (default) |


## Roles

### Frontend Roles
- **gateway**: Nginx reverse proxy (External → Internal)
  - Dual-NIC configuration (172.16.6.77 + 192.168.40.10)
  - Proxy pass to ALB VIP
  
- **alb**: HAProxy load balancer for web tier
  - Listens on 192.168.10.20 (ALB VIP)
  - Balances traffic to web1/web2
  
- **web**: Nginx web servers
  - Service IPs: 192.168.10.30/40
  - Serves static content and proxies API requests

### Backend Roles
- **db_haproxy**: Database load balancer (DBLB)
  - HAProxy + Keepalived for VIP (192.168.20.100)
  - Health checks Patroni API (:8008/master)
  - Automatic failover on master change
  
- **etcd_db**: Etcd distributed configuration store
  - 3-node cluster for consensus
  - Service IPs: 192.168.30.30/40/50
  - Used by Patroni for leader election
  
- **patroni**: PostgreSQL High Availability
  - Automatic failover and recovery
  - Streaming replication
  - REST API for health checks

## Collection Structure

```
archive_platform/
├── galaxy.yml                 # Collection metadata
├── ansible.cfg               # Ansible configuration
├── README.md                 # This file
├── EXECUTION_GUIDE.md        # Detailed execution guide
├── deploy.sh                 # Interactive deployment script
├── inventory/
│   └── hosts.ini            # Inventory with actual IPs
├── playbooks/
│   └── site.yml             # Main orchestration playbook
└── roles/
    ├── gateway/             # Gateway role
    ├── alb/                 # Application LB role
    ├── web/                 # Web server role
    ├── db_haproxy/          # Database LB role (copied)
    ├── etcd_db/             # Etcd cluster role (copied)
    └── patroni/             # Patroni HA role (copied)
```

**Note**: All roles are now self-contained within the collection. No external dependencies required.

## Example Application

See `cmp_app/` directory for example FastAPI application with DBLB integration.

## Requirements

- Ansible 2.9+
- RHEL/Rocky Linux 8+
- VMware vSphere environment
