# Service Migration Guide: DBLB Integration (`service.md`)

본 문서는 `README.md`에서 정의한 인프라 사양(DBLB + Patroni HA)을 실제 서비스 애플리케이션(`main.py`)에 적용하기 위해 필요한 **코드 변경 사항**을 기술합니다.

---

## 1. 🚨 현황 분석 (As-Is)
현재 `platform - 모니터링-터미널통합/main.py` 코드는 **로컬 개발 환경** 또는 **단일 장애점(SPOF)**을 가정한 형태로 작성되어 있어, 고가용성 DB 환경(DBLB)에 바로 붙일 수 없습니다.

### 주요 문제점
1.  **DB 접속 방식**: `localhost:15432`로 접속을 시도합니다. 이는 **SSH 터널링**을 통해 DB에 붙는 방식입니다.
    *   실제 운영/배포 환경(DBLB)에서는 DBLB가 제공하는 **VIP(`192.168.20.100`)**로 직접 붙어야 합니다.
    *   SSH 터널링 코드가 불필요하게 포함되어 있거나, 혹은 외부에서 수동으로 터널을 뚫어줘야만 앱이 켜지는 구조입니다.
2.  **Hardcoded Credentials**: 비밀번호가 코드에 노출되어 있습니다 (`Soldesk1.`).

---

## 2. 🛠️ 코드 변경 가이드 (To-Be)

애플리케이션이 DBLB를 통해 **항상 Active 상태인 Master DB**에 접속하도록 `main.py`를 수정해야 합니다.

### 파일: `platform - 모니터링-터미널통합/main.py`

#### A. DB 접속 정보 변경 (Line 49)
기존의 로컬호스트/SSH 터널링 주소를 DBLB VIP 주소로 변경하여, 애플리케이션이 로드밸런서를 바라보게 합니다.

```python
# [AS-IS] SSH 터널링을 통한 로컬 접속 (15432 포트)
SQLALCHEMY_DATABASE_URL = "postgresql://admin:Soldesk1.@localhost:15432/cmp_db"

# [TO-BE] DBLB VIP 접속 (20번 대역, 5432 포트)
# HAProxy가 현재 리더(Master) DB로 트래픽을 자동 라우팅합니다.
SQLALCHEMY_DATABASE_URL = "postgresql://admin:Soldesk1.@192.168.20.100:5432/cmp_db"
```

#### B. 불필요한 SSH 터널 의존성 제거 확인
만약 코드 내(혹은 실행 스크립트)에 `subprocess` 등을 이용해 `ssh -L 15432:localhost:5432 ...` 와 같은 터널링 로직이 있다면 **삭제**해야 합니다. DBLB 환경에서는 애플리케이션 서버(`192.168.30.x` 또는 `40.x`)가 DBLB VIP(`192.168.20.100`)와 직접 통신 가능해야 합니다.

---

## 3. 🎨 Frontend (`templates/*.html`) 영향도 분석

분석 결과, HTML/JS 파일들은 **DB 연결 로직과 완전히 분리**되어 있습니다.
*   `omakase_final.html`, `monitoring.html` 등은 `/api/...` 엔드포인트를 호출할 뿐, DB가 어디에 있는지 알지 못합니다.
*   따라서 **Backend(`main.py`)만 수정하면 Frontend는 코드 변경 없이 그대로 동작**합니다.

### 확인된 템플릿 파일
*   `omakase_final.html`: 서비스 신청 UI (영향 없음)
*   `monitoring.html`: 리소스 차트 (영향 없음)
*   `terminal.html`: 웹 터미널 (영향 없음, 별도 SSH 로직 사용)
*   `history.html`: 내역 조회 (영향 없음)

---

## 4. ✅ 최종 점검 리스트

코드를 수정한 후, 다음 사항을 반드시 확인해야 합니다.

1.  **네트워크 방화벽 (Firewall)**
    *   CMP(App) 서버 -> DBLB VIP (`192.168.20.100:5432`) 통신이 허용되어 있는가?
    *   (특히 30번/40번 대역에서 20번 대역으로의 아웃바운드 허용 필요)

2.  **DBLB 상태 확인**
    *   HAProxy 상태 페이지(보통 `:8008` 또는 설정된 포트)에서 `postgres_back`이 **UP** 상태이고, 리더 DB를 제대로 가리키고 있는지 확인.

3.  **애플리케이션 재기동**
    *   `main.py` (FastAPI) 재실행 후 로그에 DB 연결 에러(`Connection refused` 등)가 없는지 확인.
