# 🍣 Solution Omakase (H-CMP)

**Solution Omakase**는 하이브리드 클라우드 환경을 위한 **자동화된 인프라 프로비저닝 및 관리 플랫폼**입니다.  
복잡한 인프라 설정 없이 "오마카세"처럼 원하는 구성을 주문하면, **Web/WAS/DB 3-Tier 아키텍처**부터 **고가용성(HA) 구성**까지 자동으로 설계하고 배포해 줍니다.

---

## 🚀 주요 기능 (Key Features)

### 1. 🏗️ 자동화된 인프라 프로비저닝 (Automated Provisioning)
- **템플릿 기반 배포**:
  - **Single Tier**: 개발 및 테스트용 올인원 서버
  - **Standard 3-Tier**: WEB - App - DB가 분리된 표준 아키텍처
  - **Enterprise HA**: L4 스위치(HAProxy) 및 DB 이중화를 포함한 고가용성 아키텍처
- **커스텀 소프트웨어 스택**: Nginx, Tomcat, Python(Django), PostgreSQL, Redis, ELK Stack 등 원하는 패키지를 선택하여 설치 가능
- **Ansible 연동**: 백그라운드에서 Ansible Playbook을 실행하여 VM 설정 및 애플리케이션 설치 자동화

### 2. 📺 실시간 로그 스트리밍 (Real-time Console)
- **WebSocket**을 통해 Ansible 배포 로그를 브라우저에서 실시간으로 확인
- 배포 단계를 시각화하여 진행 상황(vCenter 인증 → VM 할당 → 네트워크 설정 → 패키지 설치) 모니터링

### 3. 📊 자원 모니터링 (Resource Monitoring)
- **Prometheus**와 연동하여 할당된 VM의 **CPU, Memory, Disk 사용량**을 실시간 차트로 제공
- 사용자별 할당된 자원(VM)만 필터링하여 조회 가능

### 4. 💻 웹 터미널 (Web SSH)
- 별도의 SSH 클라이언트 없이 브라우저에서 바로 VM에 접속
- **xterm.js** 기반의 웹 터미널 제공 (WebSocket 통한 양방향 통신)

### 5. 👥 사용자 관리 및 승인 시스템
- **회원가입/승인제**: 사용자가 가입 신청을 하면 관리자가 승인 후 자원 쿼터 할당
- **Role 기반 접근 제어 (RBAC)**: 일반 사용자(본인 자원만 조회) vs 관리자(전체 자원 및 시스템 설정 관리)

---

## 🛠️ 기술 스택 (Tech Stack)

### Backend
- **Framework**: Python FastAPI (비동기 처리, WebSocket 지원)
- **Database**: PostgreSQL (SQLAlchemy ORM)
- **Infrastructure Code**: Ansible, VMware Modules
- **SSH/Remote**: Paramiko
- **Cache/Msg Broker**: Redis (Pub/Sub for Log Streaming)

### Frontend
- **Core**: HTML5, Vanilla JavaScript
- **Styling**: TailwindCSS (CDN), Pretendard Font
- **Icons**: Lucide Icons
- **Visualization**: Chart.js (Monitoring), xterm.js (Web Terminal)

---

## 📂 파일 구조 (File Structure)

```
new/
├── main.py                   # Backend API 서버 진입점 (FastAPI)
├── configure_workload.yml    # Ansible Playbook (VM 설정 및 패키지 설치)
└── template/                 # Frontend HTML 파일들
    ├── omakase_final.html    # 메인 대시보드 (주문 및 프로비저닝)
    ├── monitoring.html       # 자원 모니터링 페이지
    ├── history.html          # 주문 내역 및 배포 이력 조회
    ├── terminal.html         # 웹 SSH 터미널
    ├── admin_users.html      # 관리자용 사용자 승인 관리
    └── signup.html           # 회원가입 페이지
```

### 주요 파일 설명
*   **`main.py`**: 
    *   API 엔드포인트 정의 (`/api/provision`, `/api/login` 등)
    *   WebSocket 핸들러 (`/ws/logs`, `/ws/ssh`)
    *   데이터베이스 모델 (`ProjectHistory`, `WorkloadPool` 등)
    *   Ansible 실행 로직 (`run_ansible_task`) 포함
*   **`configure_workload.yml`**:
    *   Ansible Playbook 파일
    *   VM 전원 관리, 패키지 설치(Yum), 서비스 기동(Systemd), DB 초기화 등 수행

---

## ⚙️ 설정 및 실행 (Setup & Usage)

### 1. 사전 요구 사항 (Prerequisites)
*   **Python 3.9+**
*   **Redis Server** (로그 스트리밍용)
*   **PostgreSQL** (데이터 저장용)
*   **Ansible** (서버 프로비저닝용)
*   **VMware vCenter** 환경 (VM 제어용)

### 2. 환경 변수 및 설정
`main.py` 상단 또는 환경 변수 설정을 확인하세요.
*   `SQLALCHEMY_DATABASE_URL`: DB 연결 정보
*   `SECRET_KEY`, `ENCRYPT_KEY`: 보안 키
*   Prometheus URL: `query_prometheus_async` 함수 내부 확인
*   Redis Host: `ConnectionManager` 클래스 내부 확인

### 3. 서버 실행
```bash
# 의존성 설치 (예시)
pip install fastapi uvicorn sqlalchemy psycopg2-binary redis paramiko python-jose cryptography httpx

# 서버 시작
python main.py
# 또는
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. 접속
브라우저를 열고 `http://localhost:8000` 접속

---

## 📝 관리자 계정 정보
*   기본 관리자 설정은 DB나 `main.py`의 초기화 로직을 따릅니다.
*   초기 Admin 비밀번호(Settings Modal): `1234` (기본값)

---

**Made with ❤️ by H-CMP Team**
