# Archive Platform Web Application

FastAPI 기반 웹 애플리케이션으로 DBLB(Database Load Balancer)와 Monitoring 서버를 연동합니다.

## 주요 기능

### 1. 데이터베이스 연결 (DBLB VIP)
- **연결 주소**: `192.168.20.100:5432` (DBLB VIP)
- **자동 Failover**: Patroni가 Master DB를 자동으로 선출하고, DBLB가 트래픽을 라우팅
- **Connection Pooling**: SQLAlchemy를 통한 효율적인 연결 관리
- **Health Check**: `pool_pre_ping=True`로 연결 상태 자동 확인

### 2. Monitoring 연동 (Prometheus)
- **Metrics Endpoint**: `/metrics` (Prometheus가 스크랩)
- **수집 메트릭**:
  - HTTP 요청 수 (`http_requests_total`)
  - DB 쿼리 수 (`db_queries_total`)
  - 요청 처리 시간 (`http_request_duration_seconds`)
  - DB 쿼리 시간 (`db_query_duration_seconds`)

## 설치 및 실행

### 1. 의존성 설치
```bash
pip install fastapi uvicorn sqlalchemy psycopg2-binary prometheus-client
```

### 2. 환경 변수 설정 (선택사항)
```bash
export DB_HOST=192.168.20.100
export DB_PORT=5432
export DB_USER=admin
export DB_PASS=Soldesk1.
export DB_NAME=cmp_db
export MONITORING_HOST=172.16.6.127
export MONITORING_ENABLED=true
```

### 3. 애플리케이션 실행
```bash
# 개발 모드
python main.py

# 또는 Uvicorn 직접 실행
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 프로덕션 모드 (여러 워커)
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 4. Systemd 서비스로 등록 (권장)
```bash
sudo tee /etc/systemd/system/archive-web.service > /dev/null <<EOF
[Unit]
Description=Archive Platform Web API
After=network.target

[Service]
Type=simple
User=ansible
WorkingDirectory=/opt/cmp_app
Environment="DB_HOST=192.168.20.100"
Environment="MONITORING_HOST=172.16.6.127"
ExecStart=/usr/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable archive-web
sudo systemctl start archive-web
```

## API 엔드포인트

### 기본 엔드포인트
- `GET /` - 홈페이지 (HTML)
- `GET /health` - 헬스 체크 (ALB가 사용)
- `GET /info` - 시스템 정보
- `GET /docs` - API 문서 (Swagger UI)

### 데이터베이스 엔드포인트
- `GET /db/status` - DB 연결 상태 및 정보
- `GET /db/test` - DB 읽기/쓰기 테스트

### 모니터링 엔드포인트
- `GET /metrics` - Prometheus 메트릭

## 테스트 방법

### 1. 로컬 테스트
```bash
# 홈페이지
curl http://localhost:8000/

# 헬스 체크
curl http://localhost:8000/health

# DB 상태
curl http://localhost:8000/db/status

# DB 테스트
curl http://localhost:8000/db/test

# Prometheus 메트릭
curl http://localhost:8000/metrics
```

### 2. ALB를 통한 테스트
```bash
# ALB VIP를 통한 접속
curl http://192.168.10.20/

# 로드밸런싱 확인 (여러 번 호출하여 다른 Web 서버로 분산되는지 확인)
for i in {1..10}; do
    curl -s http://192.168.10.20/ | grep Hostname
done
```

### 3. Gateway를 통한 테스트
```bash
# 외부에서 Gateway를 통한 접속
curl http://172.16.6.77/
```

## 아키텍처 흐름

```
[Client]
   ↓
[Gateway: 172.16.6.77]
   ↓
[ALB: 192.168.10.20]
   ↓
[Web Servers: 192.168.10.30/40] ← main.py 실행
   ↓                              ↓
   ↓                         [Monitoring: 172.16.6.127]
   ↓                         (Prometheus scrapes /metrics)
   ↓
[DBLB VIP: 192.168.20.100]
   ↓
[PostgreSQL Master] (Patroni가 자동 선출)
```

## Prometheus 설정 예시

Monitoring 서버(`172.16.6.127`)의 Prometheus 설정:

```yaml
# /etc/prometheus/prometheus.yml
scrape_configs:
  - job_name: 'archive-web'
    static_configs:
      - targets:
          - '192.168.10.30:8000'  # web1
          - '192.168.10.40:8000'  # web2
    metrics_path: '/metrics'
    scrape_interval: 15s
```

## 트러블슈팅

### DB 연결 실패
```bash
# DBLB VIP 연결 확인
telnet 192.168.20.100 5432

# DBLB에서 Patroni Master 확인
curl -I http://192.168.30.30:8008/master
curl -I http://192.168.30.40:8008/master
```

### Monitoring 연결 확인
```bash
# Prometheus에서 메트릭 스크랩 확인
curl http://172.16.6.127:9090/targets
```

## 로그 확인

```bash
# Systemd 서비스 로그
sudo journalctl -u archive-web -f

# Uvicorn 로그 (직접 실행 시)
tail -f /var/log/archive-web.log
```
