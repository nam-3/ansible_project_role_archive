# H-CMP `main.py` 실행 흐름 분석 (Execution Flow)

이 문서는 `main.py`가 실행될 때의 전체적인 동작 과정과 핵심 비즈니스 로직의 흐름을 분석한 내용입니다.

---

## 1. 🚀 서버 초기화 (Server Initialization)

`python main.py` 명령어로 애플리케이션이 시작되면 다음과 같은 순서로 초기화가 진행됩니다.

1.  **환경 설정 로드**:
    *   `SECRET_KEY`, `ENCRYPT_KEY` 등 환경 변수 확인 (없을 경우 기본값 사용 및 경고 로그 출력).
    *   `Fernet` 객체를 생성하여 양방향 암호화(비밀번호 등) 준비.
2.  **데이터베이스 연결 (SQLAlchemy)**:
    *   PostgreSQL 연결 (`postgresql://...`).
    *   `Base.metadata.create_all(bind=engine)`을 호출하여 테이블(`projects`, `settings`, `workload_pool` 등)이 없으면 자동 생성.
3.  **FastAPI 앱 생성**:
    *   `app = FastAPI()` 인스턴스 생성.
    *   `StaticFiles`: `/templates` 경로에 정적 파일(HTML/JS/CSS) 마운트.
    *   `CORSMiddleware`: 모든 도메인(`*`)에서의 접근 허용.
4.  **Redis 연결 (ConnectionManager)**:
    *   `ConnectionManager`가 초기화되며 Redis 클라이언트 생성 (로그 스트리밍 Pub/Sub용).

---

## 2. 🔐 주요 API 흐름 (Key API Workflows)

### A. 로그인 및 인증 (Authentication)
1.  **POST `/api/login`**:
    *   사용자가 ID/PW 전송.
    *   DB(`UserAccount`) 조회 후 `decrypt_password()`로 복호화하여 비밀번호 검증.
    *   상태(`status`)가 `active`인지 확인 (관리자 승인 여부).
    *   검증 성공 시 **JWT Access Token** 생성 및 반환.

### B. 인프라 프로비저닝 (Core: Provisioning)
사용자가 "프로젝트 생성"을 클릭했을 때의 흐름입니다.

1.  **POST `/api/provision` 요청 수신**:
    *   요청 파라미터: 서비스명, 템플릿 종류(Single/3-Tier/HA), 패키지 목록.
2.  **가용 자원 확인**:
    *   `WorkloadPool` 테이블에서 `status="available"`인 VM을 필요한 수만큼 조회 (Locking 등은 코드상 명시 안 됨).
    *   자원 부족 시 에러 반환.
3.  **데이터베이스 업데이트**:
    *   `ProjectHistory`에 'CONFIGURING' 상태로 레코드 생성 (프로젝트 ID 발급).
    *   조회된 VM들의 상태를 `provisioning`으로 변경하고 `project_id` 매핑.
4.  **백그라운드 작업 시작 (BackgroundTasks)**:
    *   `ansible_vars` 딕셔너리 구성 (vCenter 정보, 타겟 IP, 설치할 패키지 목록 등).
    *   **응답 즉시 반환**: 사용자에게는 `project_id`와 함께 "성공" 응답을 먼저 보내고, 실제 작업은 백그라운드에서 실행.
5.  **비동기 작업 실행 (`run_ansible_task`)**:
    *   `ansible-playbook` 명령어를 `subprocess`로 실행.
    *   **실시간 로그 전송**: 프로세스의 `stdout`을 한 줄씩 읽어서 Redis Pub/Sub(`manager.broadcast`)으로 전송.
    *   **단계별 상태 알림**: 로그 내용에 따라 `::STEP_1_OK::` 등의 특수 메시지를 웹소켓으로 전송하여 UI 진행바 업데이트.
    *   **완료 처리**:
        *   성공 시: DB 상태 `COMPLETED`, 자원 상태 `assigned`.
        *   실패 시: DB 상태 `FAILED`, 자원 상태 `available` (롤백).

### C. 자원 모니터링 (Monitoring)
1.  **GET `/api/monitoring/my-resources`**:
    *   `get_current_user` 의존성을 통해 JWT 토큰 검증.
    *   **관리자**: 전체 VM 조회.
    *   **일반 사용자**: 본인 소유(`owner`) 프로젝트의 VM만 조회.
2.  **Prometheus 쿼리 (Async)**:
    *   `httpx`를 사용하여 비동기로 Prometheus API에 CPU, Memory, Disk 쿼리 전송.
    *   `asyncio.gather`로 병렬 처리하여 응답 속도 최적화.
    *   조회된 메트릭 데이터를 VM 리스트와 매핑하여 반환.

---

## 3. 📡 웹소켓 통신 (WebSocket)

### A. 로그 스트리밍 (`/ws/logs/{project_id}`)
1.  **연결 (Connect)**:
    *   클라이언트가 접속하면 `ConnectionManager`에 등록.
    *   해당 `project_id`에 대한 Redis Sub(구독) Task가 없으면 생성.
2.  **메시지 중계 (Relay)**:
    *   백그라운드 태스크(`run_ansible_task`)가 Redis에 로그를 Publish.
    *   `_redis_listener`가 메시지를 수신하여 연결된 모든 웹소켓 클라이언트에게 `send_text`.
3.  **종료 (Disconnect)**:
    *   연결이 끊기면 리스트에서 제거하고, 구독자가 없으면 Redis 구독 취소.

### B. 웹 SSH 터미널 (`/ws/ssh/{ip}`)
1.  **연결**: 클라이언트 - FastAPI(WebSocket) - 타겟 VM(SSH) 구조.
2.  **Paramiko 세션 생성**:
    *   웹소켓을 통해 사용자로부터 ID/PW를 입력받음.
    *   `paramiko.SSHClient`로 타겟 VM에 SSH 접속.
3.  **양방향 통신**:
    *   **수신 (Recv)**: SSH 채널에서 데이터를 읽어 웹소켓으로 전송 (ANSI 코드 포함).
    *   **송신 (Send)**: 웹소켓에서 키 입력을 받아 SSH 채널로 전송.
    *   `resize_pty` 등을 처리하여 터미널 크기 동기화 (코드상 고정 크기).

---

## 4. ⚙️ 관리자 설정 (Admin Settings)
*   시스템 설정(`SystemSetting`)은 DB에 단일 레코드로 관리됩니다.
*   `/api/admin/settings` 엔드포인트를 통해 vCenter IP, ESXi IP, 유지보수 모드 등을 변경할 수 있습니다.
*   **Factory Reset**: `/api/admin/reset` 호출 시 모든 프로젝트 및 자원 데이터를 삭제합니다.

---

## 5. 🛑 예외 처리 흐름
*   **DB 연결 실패**: `get_db`에서 예외 발생 시 500 에러 및 로그 기록.
*   **Ansible 실패**: 프로세스 리턴 코드가 0이 아니면 DB 상태를 `FAILED`로 업데이트하고 VM을 다시 `available` 상태로 되돌림(자원 회수).
*   **인증 실패**: JWT 토큰 만료 또는 조작 시 401 Unauthorized 반환.
