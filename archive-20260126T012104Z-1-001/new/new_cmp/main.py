import asyncio
import subprocess
import os
import json
import random
import logging
import sys
import httpx
import paramiko
import re
import urllib.parse
import redis.asyncio as redis
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from fastapi import (
    FastAPI, Depends, HTTPException,
    BackgroundTasks, WebSocket, Request, Query, WebSocketDisconnect,
    status
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, JSON, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from cryptography.fernet import Fernet
from jose import JWTError, jwt

# ==========================================
# 0. 암호화 설정
# ==========================================

SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-for-dev")
print(f"🔍 DEBUG: 현재 로드된 SECRET_KEY는 [{SECRET_KEY}] 입니다.")
raw_encrypt_key = os.getenv("ENCRYPT_KEY", "fallback-encrypt-for-dev")
ENCRYPT_KEY = raw_encrypt_key.encode()
cipher_suite = Fernet(ENCRYPT_KEY)
ALGORITHM = "HS256"

if SECRET_KEY == "fallback-secret-for-dev":
    raise RuntimeError("🚨 치명적 에러: 운영 환경에서 SECRET_KEY가 설정되지 않았습니다!")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    try:
        # 상단에서 정의한 SECRET_KEY 변수를 사용합니다.
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        user_role: str = payload.get("role")

        if username is None:
            raise HTTPException(status_code=401, detail="인증 정보 부족")

        return {"sub": username, "role": user_role}
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰")

def encrypt_password(password: str) -> str:
    return cipher_suite.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password: str) -> str:
    return cipher_suite.decrypt(encrypted_password.encode()).decode()

# ==========================================
# 1. 데이터베이스 설정 (SQLite)
# ==========================================

logging.basicConfig(level=logging.INFO)
db_logger = logging.getLogger("uvicorn")

SQLALCHEMY_DATABASE_URL = "postgresql://admin:Soldesk1.@192.168.40.15:5432/cmp_db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    pool_size=20, 
    max_overflow=10, 
    pool_pre_ping=True, 
    connect_args={
        "connect_timeout": 5
    }
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==========================================
# 2. DB 테이블 모델
# ==========================================
class ProjectHistory(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, index=True)
    status = Column(String, default="PROVISIONED")
    assigned_ip = Column(String)
    template_type = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    owner = Column(String, index=True)
    details = Column(JSON) 

class SystemSetting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    vcenter_ip = Column(String)
    esxi_ip = Column(String, default="192.168.0.200")
    maintenance_mode = Column(Boolean, default=False)
    max_vcpu = Column(Integer, default=100)
    max_memory = Column(Integer, default=256)
    system_notice = Column(String, default="") 
    admin_password = Column(String, default="1234")
    vcenter_user = Column(String)
    vcenter_password = Column(String)

class WorkloadPool(Base):
    __tablename__ = "workload_pool"
    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String, unique=True, index=True)
    vm_name = Column(String)
    #is_used = Column(Boolean, default=False)
    status = Column(String, default="available", index=True)
    owner_tag = Column(String, nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    occupy_user = Column(String(20), nullable=True)

class UserQuota(Base):
    __tablename__ = "user_quotas"
    username = Column(String, primary_key=True, index=True)
    max_vms = Column(Integer, default=5)
    max_cpu = Column(Integer, default=10)
    max_ram = Column(Integer, default=20)
    max_disk = Column(Integer, default=100)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserAccount(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)  # 암호화된 비밀번호 저장
    full_name = Column(String)
    role = Column(String, default="user")
    status = Column(String, default="pending")  # 초기 상태는 승인 대기
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ==========================================
# 3. 데이터 모델 및 웹 소켓
# ==========================================

class ProjectRequest(BaseModel):
    serviceName: str
    userName: str
    config: Dict[str, Any]
    targetInfra: Dict[str, Any]

class LoginRequest(BaseModel):
    user_id: str
    password: str

class SettingsUpdateRequest(BaseModel):
    vcenter_ip: Optional[str] = ""
    esxi_ip: Optional[str] = ""
    maintenance_mode: bool = False
    max_vcpu: int = 100
    max_memory: int = 256
    system_notice: Optional[str] = ""
    admin_password: str 

class ConnectionManager:
    def __init__(self):
        # { project_id: [websocket_list] }
        self.active_connections: dict[int, list[WebSocket]] = {}
        self.redis_host = "172.16.6.77"
        self.redis = redis.from_url(f"redis://{self.redis_host}", decode_responses=True)
        # 프로젝트별 구독 Task를 추적합니다.
        self.listener_tasks: dict[int, asyncio.Task] = {}

    async def connect(self, project_id: int, websocket: WebSocket):
        await websocket.accept()
        if project_id not in self.active_connections:
            self.active_connections[project_id] = []
        self.active_connections[project_id].append(websocket)

        # [개선] 해당 프로젝트에 대한 구독 Task가 없을 때만 새로 생성합니다.
        if project_id not in self.listener_tasks:
            self.listener_tasks[project_id] = asyncio.create_task(self._redis_listener(project_id))
        print(f"✅ 프로젝트 #{project_id} 웹소켓 연결됨")

    def disconnect(self, project_id: int, websocket: WebSocket):
        if project_id in self.active_connections:
            self.active_connections[project_id].remove(websocket)
            if not self.active_connections[project_id]:
                if project_id in self.listener_tasks:
                    self.listener_tasks[project_id].cancel()
                    del self.listener_tasks[project_id]
                del self.active_connections[project_id]

    async def _redis_listener(self, project_id: int):
        # r = redis.from_url(f"redis://{self.redis_host}")
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(f"logs_{project_id}")

        # async for message in pubsub.listen():
        try:
            # 2. 메시지 수신 루프 (반드시 try 문 안에 있어야 함)
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True)
                if message:
                    log_data = message['data']
                    if project_id in self.active_connections:
                        for connection in self.active_connections[project_id]:
                            try:
                                await connection.send_text(log_data)
                            except:
                                pass
                # CPU 과부하 방지를 위한 미세한 대기
                await asyncio.sleep(0.01)
                
        except asyncio.CancelledError:
            # 3. Task가 취소될 때(연결 종료 시) 실행되는 부분
            # 이 라인은 반드시 위의 try와 수직 정렬이 맞아야 합니다.
            print(f"📡 프로젝트 #{project_id} 구독 중단 요청됨")
            await pubsub.unsubscribe(f"logs_{project_id}")
            await pubsub.close()
        except Exception as e:
            # 기타 예외 처리
            print(f"❌ Redis 리스너 에러: {e}")

    async def broadcast(self, project_id: int, message: str):
        try:
            await self.redis.publish(f"logs_{project_id}", message)
            
            # 디버깅용 로그도 'Redis 게시' 기준으로 변경
            print(f"📣 [Redis Publish] Project ID: {project_id}, Msg: {message[:20]}...")
        except Exception as e:
            print(f"❌ Redis 게시 실패: {e}")
        
        # 디버깅을 위해 서버 터미널에 출력
        print(f"📣 [Broadcast 시도] Project ID: {project_id} (Type: {type(project_id)}), Msg: {message[:20]}...")

        # 타입을 강제로 일치시켜 조회합니다.
        p_id = int(project_id) 
        if p_id in self.active_connections:
            print(f"✅ [전송 대상 발견] {len(self.active_connections[p_id])}명의 클라이언트에게 전송 중")
            for connection in self.active_connections[p_id]:
                try:
                    await connection.send_text(message)
                except Exception as e:
                    print(f"❌ 전송 실패: {e}")
                    pass
        else:
            # 이 로그가 찍힌다면 연결된 소켓을 찾지 못한 것입니다.
            print(f"⚠️ [전송 실패] ID {p_id}로 연결된 웹소켓이 없습니다. 현재 연결된 ID들: {list(self.active_connections.keys())}")

manager = ConnectionManager()

# ==========================================
# 4. 앱 및 Ansible 설정
# ==========================================
app = FastAPI()
app.mount("/templates", StaticFiles(directory="templates"), name="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#def get_db():
#    db = SessionLocal()
#    try:
#        if not db.query(SystemSetting).first():
#            db.add(SystemSetting())
#            db.commit()
#        yield db
#    finally:
#        db.close()

def get_db():
    db_logger.info("📡 [DB] 연결 시도 중...")
    db = SessionLocal()
    try:
        # 첫 실행 시 초기 데이터 생성 로직에서 멈출 수 있으므로 로그 추가
        db_logger.info("📡 [DB] SystemSetting 조회 중...")
        if not db.query(SystemSetting).first():
            db_logger.info("📡 [DB] 초기 설정 데이터 생성 중...")
            db.add(SystemSetting())
            db.commit()
        yield db
    except Exception as e:
        db_logger.error(f"🚨 [DB 에러 발생]: {str(e)}")
        raise
    finally:
        db_logger.info("📡 [DB] 연결 닫기")
        db.close()


ans_logger = logging.getLogger("uvicorn.error")

def run_ansible_task(playbook_name: str, extra_vars: dict, project_id: int, loop: asyncio.AbstractEventLoop):
    # 1. 변수 추출 및 로그 시작
    project_id = extra_vars.get("project_id")
    target_ips = extra_vars.get("target_ips", [])
    target_vm_names = extra_vars.get("target_vm_names", [])
    ans_logger.info(f"⚡ [Ansible] 실행 시작... 대상 IP: {target_ips}, 플레이북: {playbook_name}")

    # 2. 인벤토리 및 명령어 준비
    extra_vars_json = json.dumps(extra_vars)
    inventory_string = ",".join(target_ips) + "," if target_ips else "localhost,"
    playbook_full_path = os.path.join("/opt/h-cmp", playbook_name)
    
    if not os.path.exists(playbook_full_path):
        ans_logger.error(f"❌ [Ansible] Playbook 파일을 찾을 수 없음: {playbook_full_path}")
        return

    cmd = [
        "ansible-playbook",
        "-i", inventory_string,
        playbook_full_path,
        "--extra-vars", extra_vars_json,
        "-u", "root",
        "--ssh-common-args", "-o StrictHostKeyChecking=no"
    ]

    process = None # 프로세스 변수 초기화
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::STEP_1_OK::"), loop)

        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True,
            bufsize=1,
            env=os.environ.copy()
        )
        ans_logger.info(f"📡 [Ansible] 프로세스 시작 (PID: {process.pid})")

        for line in process.stdout:
            if line:
                sys.stdout.write(f"  [Ansible Log] {line}")
                sys.stdout.flush()

                # 2. 웹소켓 실시간 전송 (추가)
                # strip()으로 불필요한 공백을 제거하여 전송합니다.
                clean_line = line.strip()
                try:
                    if "TASK [Gathering Facts]" in clean_line:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::STEP_2_OK::"), loop)
                    elif "TASK [Wait for VM to boot]" in clean_line:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::STEP_3_OK::"), loop)
                    elif "PLAY RECAP" in clean_line:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::STEP_4_OK::"), loop)
                    # 메인 이벤트 루프를 얻어 비동기 broadcast 함수를 안전하게 실행
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast(int(project_id), clean_line), 
                        loop
                    )
                except Exception:
                    # 로그 전송 오류가 실제 배포 로직에 영향을 주지 않도록 예외 처리
                    pass
        process.stdout.close()
        process.wait()
        
        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::DEPLOY_COMPLETE::"), loop)

        if process.returncode == 0:
            # [수정] target_ip -> target_ips 리스트를 문자열로 변환하여 출력
            ans_logger.info(f"✅ [Ansible] 배포 완료 성공! (IPs: {', '.join(target_ips)})")
        else:
            ans_logger.error(f"🚨 [Ansible] 배포 실패. 종료 코드: {process.returncode}")

    except Exception as e:
        ans_logger.error(f"🚨 [Ansible 실행 중 예외 발생] {str(e)}")
    
    # 3. DB 상태 업데이트 (변경된 스키마 반영)
    db = SessionLocal()
    try:
        project = db.query(ProjectHistory).filter(ProjectHistory.id == project_id).first()
    
        # 해당 프로젝트에 할당된 모든 VM 자원 조회 (필터 조건을 project_id로 잡는 것이 안전합니다)
        vms_in_project = db.query(WorkloadPool).filter(WorkloadPool.project_id == project_id).all()

        # Case A: 배포 성공 (process가 존재하고 returncode가 0인 경우)
        if process and process.returncode == 0:
            if project:
                project.status = "COMPLETED"
        
            # 할당된 VM들의 상태를 'provisioning'에서 'assigned'로 변경
            for vm in vms_in_project:
                vm.status = "assigned"
        
            ans_logger.info(f"✅ [DB] 프로젝트 #{project_id} 배포 성공. 자원 상태를 'assigned'로 확정")

        # Case B: 배포 실패
        else:
            if project:
                project.status = "FAILED"
        
            # 실패 시 모든 자원 초기화 및 회수 (풀에 반납)
            for vm in vms_in_project:
                vm.status = "available"  # 다시 가용 상태로
                vm.project_id = None     # 프로젝트 연결 해제
                vm.owner_tag = None      # 소유주 태그 삭제 (중요)
                ans_logger.warning(f"🔄 [자원 회수] 배포 실패로 {vm.ip_address} 자원을 풀에 반납 (owner_tag 삭제)")

        db.commit()
    except Exception as e:
        ans_logger.error(f"🚨 [DB 업데이트 에러] {str(e)}")
        db.rollback()
    finally:
        db.close()

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=8))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ==========================================
# 5. API 엔드포인트
# ==========================================

#@app.post("/api/login")
#async def login(req: LoginRequest, db: Session = Depends(get_db)):
#    setting = db.query(SystemSetting).first()
#    real_pw = setting.admin_password if setting else "1234"
#    if req.user_id == "admin" and req.password == real_pw:
#        return {"status": "success", "message": "Login Approved"}
#    raise HTTPException(status_code=401, detail="아이디/비번 불일치")

@app.post("/api/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    # 1. 사용자 조회
    user = db.query(UserAccount).filter(UserAccount.username == req.user_id).first()
    
    if not user:
        raise HTTPException(status_code=401, detail="존재하지 않는 사용자입니다.")
    
    # 2. 비밀번호 검증 (암호화된 값 복호화 후 비교)
    try:
        if decrypt_password(user.password) != req.password:
            raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다.")
    except Exception:
        raise HTTPException(status_code=401, detail="인증에 실패했습니다.")

    # 3. 승인 상태 체크
    if user.status == "pending":
        raise HTTPException(status_code=403, detail="관리자의 승인을 기다리고 있는 계정입니다.")
    elif user.status == "rejected":
        raise HTTPException(status_code=403, detail="가입 신청이 거절되었습니다.")

    # 4. JWT 토큰 발행
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role}
    )
    
    return {
        "status": "success", 
        "access_token": access_token, 
        "token_type": "bearer",
        "role": user.role
    }


@app.post("/api/signup")
async def signup(user_data: dict, db: Session = Depends(get_db)):
    # 1. 중복 사용자 체크
    existing_user = db.query(UserAccount).filter(UserAccount.username == user_data['username']).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")

    # 2. 비밀번호 암호화 및 저장
    encrypted_pw = encrypt_password(user_data['password'])
    
    new_user = UserAccount(
        username=user_data['username'],
        password=encrypted_pw,
        full_name=user_data['full_name'],
        role="user",       # 기본값은 일반 유저
        status="pending"   # 관리자 승인 필요
    )
    
    db.add(new_user)
    db.commit()
    return {"message": "가입 신청이 완료되었습니다. 관리자 승인 후 이용 가능합니다."}


TEMPLATE_MAP = {
    "single": 1,        # All-in-One (WEB+WAS+DB)
    "standard": 3,      # 3-Tier (LB:1, WEB:1, DB:1)
    "enterprise": 5,    # 3-Tier High Availability (LB:1, WEB:2, DB:2)
    "k8s_small": 3,     # K8s (Master:1, Worker:2)
}

# [신규] Prometheus 데이터 조회 함수
async def query_prometheus_async(query: str):
    PROMETHEUS_URL = "http://192.168.40.127:9090/api/v1/query"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(PROMETHEUS_URL, params={'query': query}, timeout=3.0)
            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success':
                    return data['data']['result']
    except Exception as e:
        print(f"⚠️ Prometheus Query Error: {e}")
    return []

@app.get("/api/monitoring/my-resources")
async def get_my_resources(db: Session = Depends(get_db), current_user: Any = Depends(get_current_user)):
    """
    Admin: 모든 VM 현황 조회
    일반 유저: 본인 소유 자원만 조회
    """
    if isinstance(current_user, str):
        user_id = current_user
        user_db = db.query(UserAccount).filter(UserAccount.username == user_id).first()
        user_role = user_db.role if user_db else "user"
    else:
        user_id = current_user.get("sub")
        user_role = current_user.get("role", "user")

    # 1. DB 조회 (is_used 대신 status 필드가 있는 WorkloadPool 클래스 사용)
    if str(user_role).lower() == "admin":
        # 관리자는 WorkloadPool 테이블의 모든 데이터를 가져옴
        my_vms = db.query(WorkloadPool).all()
        print(f"👑 관리자 접속: {len(my_vms)}개의 모든 VM을 로드합니다.")
    else:
        # 일반 사용자는 본인이 소유(owner)한 프로젝트의 VM만 조인해서 가져옴
        my_vms = db.query(WorkloadPool).join(
            ProjectHistory, WorkloadPool.project_id == ProjectHistory.id
        ).filter(ProjectHistory.owner == user_id).all()
        print(f"👤 일반 유저({user_id}) 접속: {len(my_vms)}개의 소유 VM을 로드합니다.")
    
    if not my_vms:
        return []

    # 2. Prometheus 쿼리 실행 (기존 비동기 로직 유지)
    queries = {
        'cpu': '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
        'memory': '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
        'disk': '(1 - (node_filesystem_avail_bytes{mountpoint="/"}/node_filesystem_size_bytes{mountpoint="/"})) * 100'
    }

    results = await asyncio.gather(*[query_prometheus_async(q) for q in queries.values()])
    cpu_data, mem_data, disk_data = results

    # 3. 데이터 매핑 로직 (기존 동일)
    metrics_map = {}
    def parse_metrics(res_list, m_type):
        for res in res_list:
            instance = res['metric'].get('instance', '').split(':')[0].lower()
            val = round(float(res['value'][1]), 1)
            if instance not in metrics_map: metrics_map[instance] = {}
            metrics_map[instance][m_type] = val

    parse_metrics(cpu_data, 'cpu')
    parse_metrics(mem_data, 'memory')
    parse_metrics(disk_data, 'disk')

    # 4. 결과 데이터 조립
    final_result = []

    for vm in my_vms:
        # 프로젝트 이름 조회
        project_name = "Ready to use"
        proj = None
        #is_allowed = False
        
        if vm.project_id:
            proj = db.query(ProjectHistory).filter(ProjectHistory.id == vm.project_id).first()
            if proj: 
                project_name = proj.service_name

        #if current_user.get("role") == "admin":
         #   is_allowed = True # 관리자는 무조건 통과
        #elif proj and hasattr(proj, 'owner') and proj.owner == current_user.get("sub"):
         #   is_allowed = True # 일반 사용자는 본인 소유일 때만 통과
    
        # 허용되지 않은 VM은 결과 목록에 넣지 않고 건너뜁니다.
        #if not is_allowed:
         #   continue

        # 메트릭 매핑 (IP 우선)
        ip_key = vm.ip_address.lower() if vm.ip_address else ""
        usage = metrics_map.get(ip_key, {})

        # [수정] 실제 DB의 status 값을 기반으로 상태 텍스트 결정
        # assigned 또는 provisioning 상태일 때 'Running'으로 표시합니다.
        if vm.status == "assigned":
            status_display = "Running"
        elif vm.status == "provisioning":
            status_display = "Provisioning"
        else:
            status_display = "Available"

        final_result.append({
            "vm_name": vm.vm_name,
            "ip_address": vm.ip_address,
            "project_name": project_name,
            "owner": vm.occupy_user or "-",
            "cpu_usage": usage.get('cpu', 0),
            "memory_usage": usage.get('memory', 0),
            "disk_usage": usage.get('disk', 0),
            "status": status_display  # 계산된 상태값 적용
        })

    return final_result


@app.post("/api/provision")
async def create_infrastructure(
    request: ProjectRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user)
):

    # 1. 템플릿 정보 및 필요 수량 파악
    user_template = request.config.get('template', 'single')

    if user_template not in TEMPLATE_MAP:
        ans_logger.error(f"❌ 지원하지 않는 템플릿: {user_template}")
        return {"status": "error", "message": f"지원하지 않는 템플릿 유형입니다: {user_template}"}

    needed_count = TEMPLATE_MAP.get(user_template, 1)
    ans_logger.info(f"🚀 [주문 분석] 템플릿: {user_template} | 필요 수량: {needed_count}대")

   # 2. 가용 VM 조회
    vms = db.query(WorkloadPool).filter(WorkloadPool.status == "available").order_by(WorkloadPool.id.asc()).limit(needed_count).all()
    if len(vms) < needed_count:
        return {"status": "error", "message": f"가용한 자원이 부족합니다. (필요: {needed_count}, 가용: {len(vms)})"}
    
    assigned_ips = [vm.ip_address for vm in vms]
    target_vm_names = [vm.vm_name for vm in vms]
    ip_string = ", ".join(assigned_ips) # DB 저장용 콤마 구분 문자열

    lb_hosts, web_hosts, db_hosts = [], [], []

    if user_template == "standard":
        lb_hosts = [assigned_ips[0]]      # 1번 IP: Load Balancer
        web_hosts = [assigned_ips[1]]     # 2번 IP: Web/App
        db_hosts = [assigned_ips[2]]      # 3번 IP: Database
    elif user_template == "enterprise":
        lb_hosts = [assigned_ips[0]]      # 1번 IP: Load Balancer
        web_hosts = assigned_ips[1:3]     # 2, 3번 IP: Web Server 1, 2
        db_hosts = assigned_ips[3:5]      # 4, 5번 IP: DB Server 1, 2
    else: # single 등
        lb_hosts = web_hosts = db_hosts = assigned_ips

    ans_logger.info(f"\n🚀 [멀티 주문] 서비스명: {request.serviceName} | 템플릿: {user_template} ({needed_count}대)")
    
    # 3. vCenter 정보 및 패키지 분석 (데이터 수집 단계)
    settings = db.query(SystemSetting).first()
    if not settings:
        return {"status": "error", "message": "시스템 설정이 없습니다."}
    
    try:
        vcenter_pw = decrypt_password(settings.vcenter_password)
        vcenter_user = settings.vcenter_user
        vcenter_ip = settings.vcenter_ip
        
        selected_packages = request.config.get('packages', [])
        lower_packages = [str(p).lower().strip() for p in selected_packages]
    except Exception as e:
        ans_logger.error(f"🚨 [준비 실패] {e}")
        return {"status": "error", "message": "데이터 준비 중 오류 발생"}

    # 4. DB 이력 및 자원 상태 업데이트 (ID 생성 단계)
    new_project = ProjectHistory(
        service_name=request.serviceName,
        status="CONFIGURING",
        assigned_ip=ip_string,
        template_type=user_template,
        owner=current_user.get("sub"),
        details={
            "config": request.config, 
            "infra": request.targetInfra, 
            "packages": request.config.get('packages', []), 
            "vm_names": target_vm_names
        }
    )
    db.add(new_project)
    db.commit() # 여기서 new_project.id가 확정됨
    db.refresh(new_project)

    user_tag = request.userName
    ans_logger.info(f"👤 주문자 확인: {user_tag}")

    # 5. VM 사용 중으로 변경 및 프로젝트 ID 연결
    for vm in vms:
        vm.status = "provisioning" # '사용 중'이 아니라 '설치 중'임을 명시
        vm.owner_tag = request.userName # 또는 사용자의 이메일/ID
        vm.project_id = new_project.id
    db.commit()
    ans_logger.info(f"📍 [자원 할당] {', '.join(target_vm_names)} ({ip_string}) -> 프로젝트 #{new_project.id}")

    # 6. [중요] 모든 값이 준비된 후 ansible_vars 생성 (선언 시점 최적화)
    target_playbook = "configure_workload.yml"
    ansible_vars = {
        "vcenter_hostname": vcenter_ip,
        "vcenter_username": vcenter_user,
        "vcenter_password": vcenter_pw,
        "target_ips": assigned_ips,
        "target_vm_names": target_vm_names, # vCenter 제어용
        "lb_hosts": lb_hosts,
        "web_hosts": web_hosts,
        "db_hosts": db_hosts,
        "template_type": user_template,
        "service_name": request.serviceName,
        "packages_to_install": [p.lower().strip() for p in request.config.get('packages', [])],
        "env_type": request.config.get('environment', 'dev'),
        "project_id": new_project.id
    }

    loop = asyncio.get_running_loop()

    # 7. 백그라운드 작업 실행
    background_tasks.add_task(run_ansible_task, target_playbook, ansible_vars, new_project.id, loop)

    return {
        "status": "success",
        "project_id": new_project.id,
        "message": f"주문 #{new_project.id} 분석 완료. {ip_string} 서버 구성을 시작합니다."
    }


@app.delete("/api/provision/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectHistory).filter(ProjectHistory.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Not Found")
    
    # 2. [핵심] 점유 중인 워크로드 자원 회수
    # 프로젝트에 기록된 할당 IP를 기반으로 조회
    vm_entry = db.query(WorkloadPool).filter(WorkloadPool.ip_address == project.assigned_ip).first()
    if vm_entry:
        vm_entry.is_used = False
        vm_entry.project_id = None
        ans_logger.info(f"♻️ [자원 반납] 프로젝트 #{project_id} 삭제로 인해 {project.assigned_ip} 자원을 회수함")

    db.delete(project)
    db.commit()
    return {"status": "success", "message": f"프로젝트 #{project_id} 및 할당 자원이 성공적으로 삭제/회수되었습니다."}


@app.get("/")
async def read_index():
    return FileResponse('templates/omakase_final.html')

@app.get("/api/history")
async def get_history(db: Session = Depends(get_db)):
    return db.query(ProjectHistory).order_by(ProjectHistory.id.desc()).all()

@app.get("/history")
async def read_history():
    return FileResponse('templates/history.html')

@app.get("/monitoring")
async def read_monitoring(): 
    return FileResponse('templates/monitoring.html')

@app.get("/terminal")
async def read_terminal():
    return FileResponse("templates/terminal.html")

@app.get("/signup")
async def get_signup_page():
    return FileResponse("templates/signup.html")

@app.get("/admin_users")
async def get_admin_approve_page():
    return FileResponse("templates/admin_users.html")

@app.get("/api/admin/stats")
async def get_stats(db: Session = Depends(get_db)):
    projects = db.query(ProjectHistory).all()
    total_count = len(projects)
    total_vcpu = 0
    total_mem = 0
    for p in projects:
        try:
            traffic = p.details.get('config', {}).get('traffic', 'mid')
            if traffic == 'low':
                total_vcpu += 1
                total_mem += 2
            elif traffic == 'high':
                total_vcpu += 8
                total_mem += 16
            else:
                total_vcpu += 4
                total_mem += 8
        except:
            pass 
    return {"total_projects": total_count, "used_vcpu": total_vcpu, "used_memory": total_mem}

@app.get("/api/projects")
async def get_my_projects(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    # 1. 쿼리 시작
    query = db.query(ProjectHistory)
    
    # 2. 관리자가 아니면 본인이 만든 것만 필터링
    if current_user.get("role") != "admin":
        query = query.filter(ProjectHistory.owner == current_user.get("sub"))
        
    return query.all()

@app.get("/api/public/settings")
async def get_public_settings(db: Session = Depends(get_db)):
    s = db.query(SystemSetting).first()
    return {"system_notice": s.system_notice if s else "", "maintenance_mode": s.maintenance_mode if s else False}

@app.get("/api/admin/settings")
async def get_admin_settings(db: Session = Depends(get_db)):
    return db.query(SystemSetting).first()

@app.post("/api/admin/settings")
async def update_settings(
    req: SettingsUpdateRequest, 
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user) # [추가] JWT 권한 확인
):
    # 1. JWT 상의 역할 확인
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")

    s = db.query(SystemSetting).first()
    if not s:
        s = SystemSetting()
        db.add(s)

    # 2. 2중 보안: 관리자 비밀번호 재확인 (세팅 모달 하단 입력값)
    if req.admin_password != s.admin_password:
        raise HTTPException(status_code=403, detail="관리자 비밀번호가 일치하지 않습니다.")

    # 3. 데이터 업데이트 (DB 모델 필드와 일치시키기)
    s.vcenter_ip = req.vcenter_ip
    s.esxi_ip = req.esxi_ip
    s.max_vcpu = req.max_vcpu
    s.max_memory = req.max_memory
    s.maintenance_mode = req.maintenance_mode
    s.system_notice = req.system_notice

    db.commit()
    return {"status": "success", "message": "설정이 저장되었습니다."}

@app.post("/api/admin/reset")
async def factory_reset(req: LoginRequest, db: Session = Depends(get_db)):
    s = db.query(SystemSetting).first()
    if req.user_id == "admin" and req.password == s.admin_password:
        db.query(ProjectHistory).delete()
        db.query(WorkloadPool).delete()
        db.commit()
        return {"status": "success"}
    raise HTTPException(status_code=403, detail="권한 없음")

# --- 관리자 전용 API 구역 ---

@app.get("/api/admin/pending-users")
async def get_pending_users(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")

    return db.query(UserAccount).filter(UserAccount.status == "pending").all()

@app.post("/api/admin/approve-user/{username}")
async def approve_user(username: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user['role'] != 'admin':
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    
    user = db.query(UserAccount).filter(UserAccount.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    # [핵심 로직]
    # 1. 유저 상태 변경
    user.status = "active"
    
    # 2. 기본 쿼터 할당 (이전에 만든 UserQuota 테이블 사용)
    existing_quota = db.query(UserQuota).filter(UserQuota.username == username).first()
    new_quota = UserQuota(
        username=user.username,
        max_vms=5,    # 기본값 설정
        max_cpu=10,
        max_ram=20,
        max_disk=100
    )
    db.add(new_quota)
    db.commit()
    return {"message": f"{username} 사용자가 승인되었으며 기본 쿼터가 할당되었습니다."}

@app.websocket("/ws/logs/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: int):
    await manager.connect(project_id, websocket)

    await websocket.send_text(f"[System] 프로젝트 #{project_id} 로그 스트리밍 서버에 연결되었습니다.")
    try:
        while True:
            await websocket.receive_text() # 연결 유지를 위해 대기
    except:
        manager.disconnect(project_id, websocket)
        
# ==========================================
# WebSocket SSH (Added)
# ==========================================
@app.websocket("/ws/ssh/{ip}")
async def websocket_ssh(websocket: WebSocket, ip: str):
    await websocket.accept()
    
    # 1. 터미널 초기 화면
    await websocket.send_text("\r\n")
    await websocket.send_text(f"\x1b[36mConnecting to {ip}...\x1b[0m\r\n")
    await websocket.send_text("\x1b[33mWelcome to H-CMP Console Service\x1b[0m\r\n")
    await websocket.send_text("========================================\r\n")

    # [내부 함수] 사용자 입력 처리 (로그인 ID/PW 입력받을 때 사용)
    async def read_input(echo=True):
        buffer = ""
        while True:
            data = await websocket.receive_text()
            for char in data:
                # 엔터키 처리
                if char == "\r" or char == "\n":
                    await websocket.send_text("\r\n")
                    return buffer.strip()
                # 백스페이스 처리
                elif char == "\x7f" or char == "\x08":
                    if len(buffer) > 0:
                        buffer = buffer[:-1]
                        await websocket.send_text("\b \b")
                # 일반 글자 처리
                else:
                    buffer += char
                    if echo:
                        await websocket.send_text(char)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 로그인 루프 (최대 3회 시도)
    attempts = 0
    max_attempts = 3

    while attempts < max_attempts:
        try:
            # 1. 아이디 입력
            await websocket.send_text("login: ")
            username_input = await read_input(echo=True)
            if not username_input: continue

            # 2. 비밀번호 입력 (화면에 안 보이게 echo=False)
            await websocket.send_text("Password: ")
            password_input = await read_input(echo=False)

            await websocket.send_text("\r\nVerifying credentials...\r\n")

            # 3. SSH 접속 시도 (Timeout 10초로 단축)
            # Blocking I/O를 별도 스레드에서 실행하여 서버 멈춤 방지
            await asyncio.to_thread(client.connect, ip, username=username_input, password=password_input, timeout=10)
            
            # 성공하면 루프 탈출
            break
            
        except paramiko.AuthenticationException:
            attempts += 1
            remaining = max_attempts - attempts
            if remaining > 0:
                await websocket.send_text(f"\r\n\x1b[31mLogin incorrect. ({remaining} attempts remaining)\x1b[0m\r\n\r\n")
            else:
                await websocket.send_text("\r\n\x1b[31mToo many authentication failures. Connection closed.\x1b[0m\r\n")
                await websocket.close()
                return

        except WebSocketDisconnect:
            return

        except Exception as e:
            # 기타 연결 에러 (타임아웃 등)는 즉시 종료
            try:
                error_msg = str(e)
                if "10060" in error_msg:
                    error_msg = "Connection Timeout (Check IP or Firewall)"
                await websocket.send_text(f"\r\n\x1b[31mConnection Error: {error_msg}\x1b[0m\r\n\r\n")
            except: pass
            await websocket.close()
            return

    # 3. 연결 성공 후 쉘 실행
    channel = client.invoke_shell()
    
    # 쉘 크기 조정
    try:
        channel.resize_pty(width=80, height=24)
    except:
        pass

    await websocket.send_text(f"\x1b[32mLast login: {datetime.now().strftime('%a %b %d %H:%M:%S')} from WebConsole\x1b[0m\r\n")

    # SSH 출력을 받아서 ?2004h 제거 후 전송
    async def recv():
        try:
            while True:
                if channel.recv_ready():
                    # 1. SSH로부터 Raw 데이터 수신
                    raw_data = channel.recv(1024).decode(errors="ignore")
                    
                    # 2. 정규표현식으로 Bracketed Paste Mode 제어 문자 제거
                    clean_data = re.sub(r'\x1b\[\?2004[hl]', '', raw_data)
                    
                    # 3. 깨끗해진 데이터를 웹소켓으로 전송
                    await websocket.send_text(clean_data)
                
                if channel.exit_status_ready():
                    break
                await asyncio.sleep(0.01)
        except: pass

    async def send():
        try:
            while True:
                data = await websocket.receive_text()
                # 엔터키 처리
                if "\r" in data: data = data.replace("\r", "\n")
                channel.send(data)
        except: pass

    await asyncio.gather(recv(), send())
    
    try: client.close()
    except: pass



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)