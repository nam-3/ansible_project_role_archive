# Archive Platform Playbook 분석 및 검증 가이드

이 문서는 `archive/archive_platform` 디렉토리 내의 Ansible Playbook과 Role들을 분석하고, 각 구성 요소에 대한 검증 방식을 정리한 것입니다.

## 1. 개요

*   **배포 대상**: Archive Platform
*   **주요 Playbook**:
    *   `playbooks/setup_network.yml`: 네트워크 인터페이스 설정
    *   `playbooks/site.yml`: 전체 서비스 배포 (Gateway, ALB, DBLB, Etcd, Patroni, Web)

---

## 2. Playbook 별 검증 방식

### 2.1. `playbooks/setup_network.yml` (네트워크 설정)

*   **역할**: `ens224` (Service IP) 및 `ens256` (Heartbeat IP) 인터페이스를 설정합니다.
*   **검증 명령어**:
    ```bash
    # 1. 2번/3번 인터페이스(ens224, ens256) IP 설정 확인
    nmcli device status
    ip addr show ens224
    ip addr show ens256
    
    # 2. nmcli 연결 프로필 확인
    nmcli connection show
    ```
*   **확인 사항**:
    *   `ens224`: Service IP 대역 (VLAN 10/30) IP가 할당되어야 함.
    *   `ens256`: Heartbeat IP 대역 (VLAN 20) IP가 할당되어야 함.

---

### 2.2. `playbooks/site.yml` (통합 배포)

이 Playbook은 여러 Role을 순차적으로 호출합니다. 각 단계별 검증 방식은 다음과 같습니다.

#### 2.2.1. Gateway (Role: `gateway`)
*   **설명**: 외부 트래픽을 내부 ALB로 전달하는 Nginx Reverse Proxy 및 Redis 설치.
*   **검증 명령어**:
    ```bash
    # 1. 서비스 상태 확인
    systemctl status nginx
    systemctl status redis
    
    # 2. 포트 및 방화벽 확인 (80, 443, 8080 열려 있어야 함)
    firewall-cmd --list-all
    ss -tlpn | grep -E '80|443|6379|8080'
    
    # 3. Nginx 8080 포트 응답 확인
    curl -I localhost:8080
    ```

#### 2.2.2. ALB (Application Load Balancer) (Role: `alb`)
*   **설명**: Web 서버 부하 분산을 위한 HAProxy 설치.
*   **검증 명령어**:
    ```bash
    # 1. HAProxy 상태 확인
    systemctl status haproxy
    
    # 2. Config 파일 문법 확인
    haproxy -c -f /etc/haproxy/haproxy.cfg
    
    # 3. 로드밸런싱 동작 확인 (Cookie 기반)
    # SERVERID=web1 또는 SERVERID=web2 가 번갈아 나오는지 확인
    for i in {1..10}; do curl -v -s http://192.168.10.20 2>&1 | grep "set-cookie: SERVERID"; done
    ```

#### 2.2.3. DBLB (Database Load Balancer) (Role: `db_haproxy`)
*   **설명**: DB 부하 분산 및 고가용성을 위한 HAProxy + Keepalived 구성.
*   **검증 명령어**:
    ```bash
    # 1. 서비스 상태 확인
    systemctl status haproxy keepalived
    
    # 2. VIP (Virtual IP) 확인 (Master 노드에서만 보여야 함)
    ip addr show | grep <VIP_ADDRESS>
    ```

#### 2.2.4. Etcd Cluster (Role: `etcd_db`)
*   **설명**: Patroni의 DCS(Distributed Configuration Store)로 사용되는 Etcd 클러스터.
*   **검증 명령어**:
    ```bash
    # 1. 서비스 상태 확인
    systemctl status etcd
    
    # 2. 클러스터 멤버 확인
    export ETCDCTL_API=3
    etcdctl member list
    
    # 3. 헬스 체크
    etcdctl endpoint health
    ```

#### 2.2.5. Patroni HA Cluster (Role: `patroni`)
*   **설명**: PostgreSQL 고가용성 클러스터 관리 (Main DB).
*   **검증 명령어**:
    ```bash
    # 1. 서비스 상태 확인
    systemctl status patroni
    
    # 2. 클러스터 상태 및 리더 확인 (가장 중요)
    patronictl -c /etc/patroni/patroni.yml list
    
    # 3. PostgreSQL 프로세스 확인
    ps -ef | grep postgres
    ```

#### 2.2.6. DB Initialization (Role: `db_init`)
*   **설명**: `cmp_db` 데이터베이스 생성 및 `admin` 사용자 생성 (Leader 노드에서만 실행).
*   **검증 명령어**:
    ```bash
    # 1. admin 사용자 존재 확인
    psql -U postgres -c "\du" | grep admin
    
    # 2. cmp_db 생성 확인
    psql -U postgres -c "\l" | grep cmp_db
    ```

#### 2.2.7. Web Server (Role: `web`)
*   **설명**: Nginx 웹 서버 및 Python FastAPI(`archive-web`) 애플리케이션 배포.
*   **검증 명령어**:
    ```bash
    # 1. 서비스 상태 확인 (Nginx, Archive App, Redis)
    systemctl status nginx
    systemctl status archive-web
    systemctl status redis
    
    # 2. 웹 서버 응답 확인
    curl -I localhost
    
    # 3. 앱 디렉토리 확인
    ls -l /opt/cmp_app
    ls -l /opt/h-cmp
    ```

### 2.3. 애플리케이션 로그인 검증 (User Reqeust)
*   **설명**: `admin` / `1234` 계정을 사용한 로그인 테스트 (FastAPI /token 엔드포인트 가정).
*   **검증 명령어**:
    ```bash
    curl -X POST "http://localhost/token" \
         -H "Content-Type: application/x-www-form-urlencoded" \
         -d "username=admin&password=1234"
    ```
    *   **성공 시 예상 응답**: `{"access_token":"...", "token_type":"bearer"}` 또는 `successful` 메시지 포함.

---

## 3. Role별 상세 분석 (Based on Actual Code)

실제 Playbook 및 Template 코드(`templates/*.j2`, `tasks/main.yml`)를 기반으로 분석한 내용입니다.

### 3.1. Gateway Role (`gateway`)
*   **코드 파일**: `roles/gateway/tasks/main.yml`, `templates/nginx_proxy.conf.j2`
*   **실제 구현**:
    1.  **Nginx Proxy**: `80`번 포트로 들어온 요청을 내부의 `{{ alb_vip }}`로 `proxy_pass` 합니다.
    2.  **Parallel Config**: `new_archive_platform.conf`를 배포하여 `8080`번 포트도 동일하게 내부 ALB로 연결합니다.
    3.  **Firewall**: `firewalld`를 통해 `http`, `https`, `redis`, `8080/tcp` 포트를 엽니다. (별도의 Zone 설정 로직은 없으므로 기본 Zone 사용)
    4.  **IP Forwarding**: `net.ipv4.ip_forward`를 1로 설정하여 커널 레벨의 패킷 포워딩을 활성화합니다.
*   **주의사항**:
    *   Firewall Zone 분리(`public`/`internal`) 코드는 존재하지 않으므로, 보안 강화 시 추가 구성이 필요합니다.

### 3.2. Application Load Balancer (`alb`)
*   **코드 파일**: `roles/alb/templates/haproxy.cfg.j2`
*   **실제 구현**:
    1.  **Backend 설정**: `groups['web']` 인벤토리를 루프 돌며 서버를 추가합니다.
    2.  **Health Check**: HTTP GET `/` 요청으로 200 OK를 기대하며, `check inter 3s` (3초 주기)로 감시합니다.
    3.  **Persistence**: `cookie SERVERID insert` 설정을 통해, 클라이언트가 처음 접속한 Web 서버에 계속 연결되도록(Sticky Session) 구성되어 있습니다.
    4.  **Admin Stats**: `8080` 포트로 접속 시 `/stats` 페이지를 제공합니다. (`admin:admin`)

### 3.3. Web Server (`web`)
*   **코드 파일**: `roles/web/templates/archive-web.service.j2`, `nginx.conf.j2`
*   **실제 구현**:
    1.  **FastAPI 구동**: `uvicorn main:app`을 `archive-web.service`로 등록하며, 워커 수는 4개(`--workers 4`)입니다.
    2.  **Reverse Proxy**: 로컬 Nginx가 80번 포트를 받아 로컬 8000번(Uvicorn)으로 전달합니다.
    3.  **DB 연결**: Env Var `DB_HOST`에 `{{ dblb_vip }}`가 주입되므로, 앱은 DBLB를 통해 DB에 접속합니다.
    4.  **Redis 연동**: `127.0.0.1` 로컬 Redis를 바라보도록 설정되어 있습니다 (`REDIS_HOST`).

### 3.4. DB Load Balancer (`db_haproxy`)
*   **코드 파일**: `roles/db_haproxy/templates/haproxy.cfg.j2`, `keepalived.conf.j2`
*   **실제 구현**:
    1.  **Patroni Health Check**: 단순히 포트만 보는 것이 아니라 `http-check GET /master` (Port 8008)를 호출하여 **현재 Leader인 노드**로만 트래픽을 보냅니다.
    2.  **Keepalived VRRP**: `vrrp_script`로 `killall -0 haproxy`를 수행하며 프로세스 생존 여부를 체크합니다.
    3.  **Unicast Peering**: 멀티캐스트가 아닌 Unicast(`unicast_peer`) 방식을 사용하여 네트워크 장비 호환성을 높였습니다.

### 3.5. Etcd Cluster (`etcd_db`)
*   **코드 파일**: `roles/etcd_db/templates/etcd.conf.j2`
*   **실제 구현**:
    1.  **Static Cluster**: `ETCD_INITIAL_CLUSTER` 변수에 `db1`, `db2`, `dcs` 3개 노드가 하드코딩되어 주입됩니다.
    2.  **Listen URL**: 클러스터 통신(2380)과 클라이언트 통신(2379) 포트를 모두 엽니다.

### 3.6. Patroni HA (`patroni`)
*   **코드 파일**: `roles/patroni/templates/patroni.yml.j2`, `patroni.service.j2`
*   **실제 구현**:
    1.  **No Auto-Restart**: 서비스 파일에 `Restart=no`로 설정되어 있어, Patroni가 죽으면(의도치 않은 상황 포함) 자동으로 살아나지 않도록 설계됨 (Split-brain 방지 목적 추정).
    2.  **Etcd v3**: DCS로 local etcd(`127.0.0.1:2379`)를 바라보도록 설정되어 있습니다. (각 노드마다 로컬 Etcd가 떠있거나, etcd proxy를 사용하는 구조로 보임)
    3.  **Watchdog**: `softdog` 등의 Watchdog 설정 코드는 명시적으로 보이지 않으나, 패키지 설치 목록(`yum install watchdog`)에는 포함되어 있습니다.

### 3.7. DB Initializer (`db_init`)
*   **코드 파일**: `roles/db_init/tasks/main.yml`
*   **실제 구현**:
    1.  **Leader Detection**: `uri` 모듈로 `http://{{ service_ip }}:8008/cluster`를 호출해 Leader 여부를 확인합니다.
    2.  **Conditional Check**: `leader_check.status == 200`인 경우에만 SQL(`CREATE ROLE`, `CREATE DATABASE`)을 실행하여, Replica 노드에서의 실행 오류를 방지합니다.


---



