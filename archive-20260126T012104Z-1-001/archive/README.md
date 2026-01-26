# Cloud Platform Infrastructure with Ansible

이 프로젝트는 Ansible을 사용하여 PostgreSQL 이중화(DB), Nginx 웹 서버(Web), HAProxy 로드밸런서(LB), 그리고 Management Gateway를 구축하고 관리하는 자동화 구성을 담고 있습니다.

## 🏗️ 아키텍처 구성

전체 인프라 구성은 다음과 같습니다:

1.  **Database Cluster (DB)**
    -   **Master**: `db-01` (192.168.40.15) - Read/Write
    -   **Slave**: `db-02` (192.168.40.16) - Read Only (Streaming Replication)
    -   **Version**: PostgreSQL 15
    -   **OS**: CentOS 9 Stream / RHEL 9

2.  **Web Server Cluster (Web)**
    -   **Nodes**: `web-01`, `web-02`
    -   **Service**: Nginx
    -   **Content**: 각 서버의 호스트네임을 표시하는 식별용 페이지

3.  **Load Balancer (LB)**
    -   **Node**: `lb`
    -   **Service**: HAProxy
    -   **Role**: 80번 포트 트래픽을 `web-01`, `web-02`로 라운드로빈 부하 분산

4.  **Management Gateway**
    -   **Node**: `Gateway-Mgmt` (172.16.6.77)
    -   **Role**: 내부망 DNS 및 네트워크 게이트웨이 역할
    -   **Deployment**: vCenter를 통해 VM 형태로 자동 배포

---

## 🚀 Playbook 상세 내용

### 1. DB 서버: PostgreSQL 이중화

*   **`deploy_postgresql.yml`**: PostgreSQL 설치 및 기본 설정
    *   PostgreSQL 15 리포지토리 및 패키지 설치
    *   `postgresql.conf`: 외부 접속 허용 (`listen_addresses = '*'`)
    *   `pg_hba.conf`: 내부 네트워크(172.16.6.0/24) 접근 및 복제(Replication) 허용
    *   계정 생성: `admin` (Superuser), `replicator` (Replication role)

*   **`setup_replication.yml`**: Slave 노드 복제 구성
    *   기존 데이터 디렉토리 초기화
    *   `pg_basebackup`을 사용하여 Master(db-01)로부터 데이터 동기화
    *   서비스 재시작하여 Replica 모드로 동작

#### 🧪 DB 테스트 방법
```bash
# Master에서 데이터 입력
ansible db-01 -m shell -a "cd /tmp && sudo -u postgres psql -c \"INSERT INTO replication_test (message) VALUES ('Hello from Master');\""

# Slave에서 데이터 복제 확인
ansible db-02 -m shell -a "cd /tmp && sudo -u postgres psql -c \"SELECT * FROM replication_test;\""
```

### 2. WEB 서버: Nginx 설치

*   **`setup_web.yml`**: 웹 서버 구성
    *   Nginx 패키지 설치 및 서비스 시작
    *   `index.html` 생성 (서버 호스트네임 포함)
    *   Firewall 80번 포트 허용

#### 🧪 Web 테스트 방법
```bash
ansible web -m shell -a "curl -s http://localhost"
```

### 3. LB 서버: HAProxy 설치

*   **`setup_lb.yml`**: 로드밸런서 구성
    *   HAProxy 설치
    *   `haproxy.cfg`: Round Robin 방식으로 `web-01`, `web-02`에 트래픽 분산 설정
    *   Firewall 80번 포트 허용

#### 🧪 LB 테스트 방법
```bash
# 반복 요청을 통해 로드밸런싱 확인
curl http://172.16.6.121
curl http://172.16.6.121
```

### 4. Management Cluster: Gateway VM 생성

*   **`create_gateway_vm.yml`**: Gateway VM 배포 (VMware vCenter 연동)
    *   `community.vmware` 모듈 사용
    *   지정된 템플릿(Base-CentOS9)을 사용하여 `Gateway-Mgmt` VM 생성
    *   IP(172.16.6.77) 및 리소스(CPU 2, RAM 4GB) 설정

*   **`setup_mgmt_gateway.yml`**: Gateway 내부 설정
    *   `dnsmasq` 설치 및 설정 (로컬 DNS 역할)
    *   `/etc/hosts`: Ansible 인벤토리의 모든 호스트 정보를 자동으로 등록하여 이름 풀이 지원
    *   Nginx 설치

---

## 📋 필수 요구 사항

*   **Ansible Controller**: Ansible이 설치된 제어 노드
*   **Collections**:
    *   `community.postgresql`
    *   `community.vmware`
*   **Inventory**: 대상 호스트(`db`, `web`, `lb`, `localhost`)가 정의된 인벤토리 파일
