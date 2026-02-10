# 인프라 구축 가능성 및 정합성 분석 보고서 (`final.md`)

본 문서는 프로젝트의 `README.md`에 기술된 인프라 설계와 실제 Ansible 코드(`db_haproxy`, `etcd_db`, `patroni`, `web_ha`)를 비교 분석한 결과입니다. 특히 **IP 주소 체계**와 **컴포넌트 연동 관계**를 중점으로 실제 구축 가능성을 진단합니다.

---

## 1. 🚨 핵심 결론: 구축 불가능 (Major Discrepancy)

**현재 상태로는 `README.md`의 명세를 코드로 구현할 수 없습니다.**
문서와 코드가 지향하는 아키텍처와 네트워크 환경이 완전히 다릅니다.

*   **문서(`README.md`)**: 단일 네트워크(172.16.6.0/24) 기반의 **Simple Master-Slave** 구조.
*   **코드(`db_haproxy`, `patroni`, `etcd`)**: 다중 네트워크(Mnmt, Svc, HB) 기반의 **Patroni 고가용성 클러스터** 구조.

---

## 2. 🔍 상세 비교 분석

### A. IP 주소 및 네트워크 구성 (Critical Mismatch)

가장 큰 차이점은 네트워크 대역과 인터페이스 구성입니다. 코드는 **망 분리**를 전제로 작성되어 있어, README의 단일망 환경에서는 동작하지 않거나 수정이 필요합니다.

| 구분 | README.md (문서 명세) | Actual Code (실제 구현) | 비고 |
| :--- | :--- | :--- | :--- |
| **Network Scheme** | **Single Network** (172.16.6.0/24) | **Multi Network** <br> - Mgmt: `192.168.40.x`<br> - Service (DB): `192.168.30.x`<br> - Heartbeat: `192.168.20.x` | 코드는 고도화된 망분리 환경 전제 |
| **DB Nodes** | - Master: `172.16.6.15`<br> - Slave: `172.16.6.16` | - DB1: `192.168.40.60` (Mgmt) / `30.30` (Svc)<br> - DB2: `192.168.40.70` (Mgmt) / `30.40` (Svc) | IP 대역 및 노드 식별자 불일치 |
| **LB Nodes** | - `172.16.6.121` (Web LB용) | - `192.168.40.50~51` (DB LB용 추정) | 용도와 대역 모두 다름 |
| **Gateway** | `172.16.6.77` (Mgmt Gateway) | 코드상 명시적 Gateway 설정 없음 (각 노드 `dcs` 등 존재) | 아키텍처 상이 |

### B. 아키텍처 및 연동 관계 (Architecture Mismatch)

문서는 고전적인 수동 복제 방식을 기술하고 있으나, 실제 코드는 **Etcd + Patroni**를 활용한 **자동 장애 조치(Auto Failover)** 클러스터를 구현하고 있습니다.

#### 1. Database & HA
*   **README**: `Master` -> `Slave` (Streaming Replication). 장애 시 수동 승격 필요 가능성 높음.
*   **Code (`patroni`, `etcd_db`)**:
    *   **Etcd**: 분산 코디네이터 (`db1`, `db2`, `dcs` 3중화). 클러스터 상태 저장.
    *   **Patroni**: PostgreSQL HA 관리자. Etcd를 통해 리더 선출 및 Failover 자동화.
    *   **연동**: DB 노드들이 Etcd 쿼럼을 구성하고, Patroni가 이를 감시.

#### 2. Load Balancer (DB Layer)
*   **README**: DB 앞단에 로드밸런서 언급 없음 (App이 DB 직접 접속 가정).
*   **Code (`db_haproxy`)**:
    *   DB 전용 HAProxy 존재 (`dblb1`, `dblb2`).
    *   `192.168.20.x` 대역의 VIP(`192.168.20.100`)를 통해 애플리케이션이 접속하도록 구성됨.
    *   Keepalived 등을 통한 VIP 이중화 가능성 내포.

#### 3. Web & App Layer
*   **README**: Nginx (`web-01`, `web-02`)가 존재하며 `haproxy`가 이를 부하분산.
*   **Code (`web_ha`)**:
    *   `web-01`, `web-02`가 아닌 `web1.example.com` 등의 도메인 기반 백엔드 설정.
    *   단순 IP 기반이 아닌 호스트명 기반으로 작성되어 `/etc/hosts` 의존성 있음.

---

## 3. 🛠️ 종합 구축 시나리오 및 제언

현재 코드를 활용하여 구축하려면 **README의 설계를 코드에 맞춰 전면 수정**해야 합니다.

### 실제 구현 가능한 아키텍처 (Code Base)
코드를 그대로 사용할 경우 예상되는 최종 인프라 모습은 다음과 같습니다:

1.  **Client/Web** -> **DB HAProxy (VIP: 192.168.20.100)** 로 접속
2.  **DB HAProxy** -> **Patroni Master Node** (R/W) 로 트래픽 라우팅
    *   (Patroni가 Etcd 정보를 바탕으로 현재 Master를 식별하여 연결)
3.  **PostgreSQL Nodes**: `db1`, `db2`, `dcs`(Witness 역할 추정)가 3-Node 클러스터 형성.

### 제언 (Action Items)
1.  **네트워크 환경 통일**: 실제 배포할 환경(VM/Cloud)의 네트워크가 **3개 대역(40, 30, 20)**을 지원하는지 확인하거나, 코드를 수정하여 **단일망(172.16.6.x)**으로 통합해야 합니다.
2.  **인벤토리 최신화**: `README.md`에 적힌 IP(`172.16.x.x`)를 무시하고, 코드의 `inventory.ini`에 맞춰 서버 IP를 할당해야 합니다.
3.  **웹 서버 역할 재정의**: `web_ha`의 Apache 설치 로직을 Nginx로 변경하라는 문서 요구사항을 반영할지, 코드대로 Apache를 사용할지 결정해야 합니다.

---

---

## 4. 💡 Web HA 개선안: Nginx 전환 및 Gateway 기반 Reverse Proxy (Addendum)

사용자의 요청에 따라 **README.md에 기술된 서비스 구조(IP 및 역할)를 그대로 유지**하되, **반드시 Nginx를 Reverse Proxy로 구성**하여 Management Gateway(GW)와의 연동성을 강화하는 방안입니다.

### A. 기본 원칙: README.md 아키텍처 준수
코드(`db_haproxy`, `patroni` 등)에 포함된 복잡한 HA 구조(Etcd, Keepalived VIP 등)는 **배제**하고, 문서에 명시된 직관적인 구성을 따릅니다.
*   **DB**: `db-01`(172.16.6.15), `db-02`(172.16.6.16)
*   **Web**: `web-01`(172.16.6.123), `web-02`(172.16.6.124)
*   **Gateway**: `Gateway-Mgmt`(172.16.6.77)

### B. Web Server (Nginx) 재구성 및 Reverse Proxy 설정
`web_ha` 롤을 수정하여 Apache 대신 **Nginx**를 설치하고, 단순 정적 페이지 호스팅을 넘어 **Gateway로 API 요청을 전달하는 Reverse Proxy** 역할을 수행하도록 설정합니다.

#### 1. Nginx 설치 및 서비스 전환
*   **패키지**: `dnf install nginx` (Apache 제거)
*   **서비스**: `systemctl enable --now nginx`

#### 2. Reverse Proxy 설정 (핵심 변경 사항)
Web 서버는 클라이언트의 `/api/` 요청을 받아, **Management Gateway (`172.16.6.77`)**로 전달합니다. 이는 Gateway가 내부망의 API 진입점(Entrypoint) 역할을 수행하거나, API에 대한 라우팅 로직을 가지고 있다는 가정에 기반합니다.

**Nginx 설정 파일 예시 (`/etc/nginx/conf.d/default.conf`):**
```nginx
server {
    listen 80;
    server_name {{ inventory_hostname }}.cloud.local;

    # 1. 정적 웹 콘텐츠 (Frontend)
    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ =404;
    }

    # 2. API Reverse Proxy -> Gateway
    # "GW의 API 연결 로직"을 고려하여, 모든 API 트래픽을 Gateway로 위임
    location /api/ {
        # Management Gateway IP 지정
        proxy_pass http://172.16.6.77; 
        
        # 헤더 전달 (Client IP 보존 등)
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### C. 기대 효과
이 구성을 통해 다음과 같은 아키텍처가 완성됩니다.
1.  **Frontend**: `web-01`, `web-02`가 정적 페이지 응답.
2.  **Backend Access**: `/api` 요청 발생 시 `Gateway(172.16.6.77)`로 트래픽 전달.
---

---

---

## 5. 🏗️ 차세대 아키텍처 이관 가이드 (Network Flow & Entrypoint)

사용자의 요청에 따라 **Gateway(172.16.6.77)**를 유일한 외부 진입점으로 삼고, **10/20/30/40 대역**을 체계적으로 분리하여 트래픽을 처리하는 흐름을 정의합니다.

### A. 네트워크 대역 재정의 (Subnet Roles)

인벤토리의 `ansible_host`(관리 IP)와 별개로, 실제 **서비스 트래픽**이 흐르는 대역을 다음과 같이 확정합니다.

| 대역 (Subnet) | 역할 (Role) | 포함 구성 요소 (Service IP) | 설명 |
| :--- | :--- | :--- | :--- |
| **192.168.40.x** | **Management** | Ansible, Gateway(Internal), Mgmt Interfaces | 관리자(SSH) 및 배포 전용 대역 |
| **192.168.30.x** | **DB Data** | DB Replication, Etcd | DB 내부 데이터 복제 및 동기화 |
| **192.168.20.x** | **DB VIP** | DBLB VIP (`20.100`), Heartbeat | Web/WAS가 DB를 바라보는 접점 |
| **192.168.10.x** | **Web Service** | **ALB VIP**, Web/WAS Service IP | **Gateway가 트래픽을 던지는 타겟 대역** |

### B. Gateway 진입 및 트래픽 흐름 (Traffic Flow)

외부 사용자(또는 사내망)가 `172.16.6.77`로 접속했을 때, 내부의 `192.168.10.x` 대역으로 연결되는 과정입니다.

#### 1. Gateway의 이중 네트워크 구성 (Dual Homing) & 라우팅
사용자님의 말씀대로 Gateway가 **10번 대역**의 ALB와 통신하려면 물리적/논리적 경로가 있어야 합니다. 현재 설계는 다음과 같은 이유로 **40번 대역(Mgmt)**을 기본으로 하되, **10번 대역(Service)**으로 가는 길을 열어두는 구조입니다.

*   **Q: 왜 Gateway는 40번(Mgmt)에 있나요?**
    *   **A: 관리 목적 우선**: Gateway는 Ansible 배포, 관리자 SSH 접속, DNS 제공 등 **인프라 전체 통제**가 주 목적이므로 관리 대역(40번)에 속하는 것이 보안상 표준입니다.
*   **Q: 그럼 10번 대역 ALB랑은 어떻게 통신하나요?**
    *   **Case 1 (L3 라우팅)**: VMware 상위 라우터(vRouter)가 40번 대역과 10번 대역 간의 통신을 허용해줍니다. (가장 일반적)
    *   **Case 2 (NIC 추가)**: Gateway VM에 **NIC를 하나 더 추가**하여 `192.168.10.1` 같은 IP를 할당, 10번 대역과 직접 붙습니다.
    *   **결론**: `Gateway(40.10)` -> `Router` -> `ALB(10.x)` 로 가거나, Gateway가 직접 10번 IP를 가져야 합니다. 본 가이드에서는 **L3 라우팅**이 되어 있다고 가정하거나, 필요한 경우 NIC를 추가하는 것을 권장합니다.

#### 2. 상세 트래픽 경로 (Step-by-Step)

```mermaid
graph LR
    User((User)) -->|http://172.16.6.77| GW[Gateway Nginx]
    subgraph "Internal Network"
        GW --"Routing (L3) or 2nd NIC"--> ALB[ALB (L4/L7) : 192.168.10.x]
        subgraph "192.168.10.x (Web Service)"
            ALB -->|Round Robin| WEB1[Web-01]
            ALB -->|Round Robin| WEB2[Web-02]
        end
        subgraph "192.168.20.x (DB VIP)"
            WEB1 -->|JDBC/API| DBLB[DBLB VIP: 20.100]
            WEB2 -->|JDBC/API| DBLB
        end
        subgraph "192.168.30.x (DB Data)"
            DBLB -->|Read/Write| DB_Master[PostgreSQL Master]
        end
    end
```

#### 3. 구성 가이드
1.  **Gateway Nginx 설정 (`/etc/nginx/conf.d/proxy.conf`)**:
    *   외부에서 들어온 요청을 내부망의 **ALB(`192.168.10.x`)**로 전달합니다.
    ```nginx
    server {
        listen 80;
        server_name 172.16.6.77;

        location / {
            # ALB(로드밸런서)의 서비스 IP로 토스
            proxy_pass http://192.168.10.20; 
            proxy_set_header Host $host;
        }
    }
    ```

2.  **ALB (HAProxy) 설정**:
    *   `192.168.10.20` (예시)에서 리스닝하며, `Web-01/02`의 서비스 IP(`192.168.10.30/40`)로 부하분산.

3.  **App (Web/WAS) 설정**:
    *   `main.py` 등에서 DB 연결 시 **DBLB VIP(`192.168.20.100`)**를 바라보도록 설정.

## 6. 🌩️ CMP 프로젝트 컨텍스트 및 확장 서비스 (Project Concept)

본 인프라 구축의 상위 목표는 **VMware vSphere**를 기반으로 한 **Cloud Management Platform (CMP)** 구축입니다. 사용자는 `프레젠테이션1.pptx`의 컨셉을 바탕으로, 단순 인프라 구축을 넘어 **통합 모니터링 및 웹 터미널** 환경 조성을 목표로 하고 있습니다.

### A. CMP 인프라 컨셉 (Referencing `프레젠테이션1.pptx`)
*   **Core**: VMware vSphere 가상화 환경 위에서 동작.
*   **Goal**: Management Gateway를 통한 단일 진입점 관리 및 Hybrid/Private Cloud 통합 관리 지향.
*   **Management Layer**: Ansible을 통한 자동화 배포 및 관리.

### B. 서비스 상세 구현: 모니터링 및 통합 (Referencing `prmths` Directory)
사용자가 언급한 **"platform-모니터링-터미널통합"**의 실제 구현체로 추정되는 `prmths` 디렉토리를 분석한 결과, 다음과 같은 **Obervability Stack**이 구축되어 있습니다.

| 구분 | 컴포넌트 | 역할 |
| :--- | :--- | :--- |
| **Metric** | **Prometheus** | 서버 및 애플리케이션의 시계열 데이터 수집 |
| **Log** | **Loki + Promtail** | 시스템 로그 및 애플리케이션 로그의 중앙 집권화 및 검색 |
| **Visual** | **Grafana** | Prometheus와 Loki 데이터를 단일 대시보드에서 시각화 |
| **Agent** | **Node Exporter** | OS 레벨의 하드웨어 리소스(CPU/Mem/Disk) 메트릭 제공 |

#### 1. 인프라 연동 방안
*   **Target Inventory**: 기존 `db-01`, `web-01` 등의 `/etc/hosts` 또는 `inventory`를 `prometheus.yml`의 `static_configs` 또는 `file_sd_configs`에 연동하여 자동 모니터링 대상 등록.
*   **Grafana Dashboard**: PostgreSQL(Patroni) 및 Nginx, HAProxy 전용 대시보드를 프로비저닝하여 통합 뷰 제공.

### C. 터미널 통합 (Terminal Integration)
현재 분석된 코드(`ansible_project_role_archive`) 내에서는 구체적인 터미널 통합 솔루션(예: Cockpit, Apache Guacamole, Wetty 등)의 자동화 코드가 발견되지 않았습니다. 그러나 CMP 컨셉 완성을 위해 다음과 같은 구성을 제안합니다.

*   **Web-based Shell**: **Gateway 서버**(`192.168.40.10` or `172.16.6.77`)에 **Cockpit** 또는 **TTYD**를 설치.
*   **Access Flow**: [User] -> [Web Browser] -> [Gateway (Nginx Proxy)] -> [Terminal Service] -> [Internal SSH to Nodes]
*   **통합**: Grafana 대시보드 내에 `iframe` 형태나 링크로 터미널 접근를 통합하여, **"모니터링 하다가 문제 발생 시 즉시 터미널 접속"**이 가능한 환경 구현.

이러한 **CMP + Monitoring + Terminal** 통합 모델은 `README.md`의 인프라 위에 얹혀지는 **Platform Layer**로서 기능하게 됩니다.
