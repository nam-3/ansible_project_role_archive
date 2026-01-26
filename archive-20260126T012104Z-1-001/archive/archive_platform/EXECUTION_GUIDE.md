# Archive Platform 실행 가이드

## 사전 준비

### 1. Ansible 설치 확인
```bash
ansible --version
# Ansible 2.9 이상 필요
```

### 2. SSH 키 설정
```bash
# Control Node에서 모든 타겟 서버로 SSH 키 복사
ssh-copy-id ansible@192.168.40.1   # Gateway (Gateway-Mgmt)
ssh-copy-id ansible@192.168.40.20  # ALB
ssh-copy-id ansible@192.168.40.30  # Web1
ssh-copy-id ansible@192.168.40.40  # Web2
ssh-copy-id ansible@192.168.40.50  # DBLB1
ssh-copy-id ansible@192.168.40.51  # DBLB2
ssh-copy-id ansible@192.168.40.52  # DCS
ssh-copy-id ansible@192.168.40.60  # DB1
ssh-copy-id ansible@192.168.40.70  # DB2
```

### 3. 연결 테스트
```bash
cd archive_platform
ansible all -i inventory/hosts.ini -m ping
```

---

## 실행 방법

### 1단계: 네트워크 인터페이스 설정 (중요)
Inventory(`hosts.ini`)에 정의된 IP 정보를 기반으로 NIC 설정을 자동화합니다.
**주의**: 이 단계는 서비스망 및 하트비트망 NIC를 활성화합니다.

```bash
ansible-playbook -i inventory/hosts.ini playbooks/setup_network.yml
```

### 2단계: 플랫폼 전체 배포
DB 클러스터, 로드밸런서(ALB/DBLB), CMP 애플리케이션, Gateway(Redis 포함)를 모두 배포합니다.

```bash
ansible-playbook -i inventory/hosts.ini playbooks/site.yml
```

### (선택) 단계별 배포
문제가 발생하거나 특정 계층만 배포할 때 사용합니다.

```bash
# 1. DB 클러스터 (Etcd + Patroni)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags db

# 2. DBLB (DB Load Balancer)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags dblb

# 3. Web 티어 (CMP App + ALB)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags web_tier

# 4. Gateway (Nginx Proxy + Redis)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags gateway
```

---

## 검증 방법

### 1. CMP 애플리케이션
브라우저를 통해 Gateway 외부 IP로 접속하여 CMP 대시보드를 확인합니다.
*   **URL**: `http://172.16.6.77/`
*   **확인 사항**:
    *   로그인 페이지 또는 대시보드 로딩
    *   DB 연결 상태 (우측 상단 또는 상태 페이지)
    *   모니터링 데이터 표시 여부

### 2. 서비스 접속 테스트 (ALB & Web)
**실행 위치**: Ansible Control Node, Gateway, 또는 내부망(40번 대역)의 임의의 VM

10번 대역(서비스망)으로의 라우팅이 설정된 곳에서 ALB VIP로 접속을 시도합니다.
```bash
# ALB 접속 (서비스 VIP)
curl -I http://192.168.10.20
# (HTTP 200 OK 또는 404가 뜨면 정상)
```

### 3. DB 연동 확인 (Web -> DB)
**실행 위치**: Web 서버 (`web1` / 192.168.40.30) **내부**

Web 서버가 DBLB를 통해 실제 DB 데이터를 읽어오는지 확인합니다.

1. **Web 서버로 접속** (Ansible Control Node에서 실행)
   ```bash
   ssh ansible@192.168.40.30
   ```

2. **로그인 API 호출** (Web 서버 내부 쉘에서 실행)
   ```bash
   # 로컬 8000번 포트로 로그인 요청 전송
   curl -X POST "http://127.0.0.1:8000/api/login" \
        -H "Content-Type: application/json" \
        -d '{"user_id": "admin", "password": "1234"}'
   
   # 성공 시: {"status":"success", "message":"Login Approved"} 확인
   ```

### 4. API 헬스 체크
```bash
# Gateway를 통한 API 상태 확인
curl http://172.16.6.77/health
# 예상 응답: {"status": "healthy", "database": "connected", ...}
```

### 3. 실시간 로그 / 터미널 (WebSocket)
*   CMP 웹에서 VM 생성 시 실시간 로그가 올라오는지 확인 (Redis 연동 확인).
*   웹 터미널 접속 시도 (Redis + SSH 연동 확인).

### 4. DBLB 및 DB 검증
```bash
# DBLB VIP(192.168.20.100)를 통한 DB 접속
psql -h 192.168.20.100 -U admin -d cmp_db -c "SELECT inet_server_addr();"
```

---

## 트러블슈팅

### 네트워크 설정 실패 시
`setup_network.yml` 실행 중 오류 발생 시, 각 서버의 인터페이스 이름(`ens224` 등)이 맞는지 확인하세요.
```bash
ansible all -m shell -a "nmcli dev status"
```

### 애플리케이션 실행 오류 (Web)
Web 서버(`web1`, `web2`)에서 서비스 로그를 확인합니다.
```bash
ssh ansible@192.168.40.30
sudo journalctl -u archive-web -f
# 또는 /opt/cmp_app/ 로그 확인
```
일반적인 오류:
*   **DB 연결 실패**: DBLB VIP(`192.168.20.100`)와 통신 되는지 확인 (`telnet 192.168.20.100 5432`).
*   **Redis 연결 실패**: Gateway(`172.16.6.77`)의 6379 포트가 열려있는지 확인.

### 모니터링 데이터 없음
Prometheus 서버(`192.168.40.127`)가 정상 동작 중인지 확인하고, 방화벽 9090 포트를 확인하세요.
