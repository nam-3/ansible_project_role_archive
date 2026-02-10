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
        # { key: [websocket_list] } -> key는 project_id(int) 또는 user_id(str)
        self.active_connections: dict[Any, list[WebSocket]] = {}
        self.redis_host = "172.16.6.77"
        self.redis = redis.from_url(f"redis://{self.redis_host}", decode_responses=True)
        self.listener_tasks: dict[Any, asyncio.Task] = {}

    async def connect(self, key: Any, websocket: WebSocket):
        await websocket.accept()
        if key not in self.active_connections:
            self.active_connections[key] = []
        self.active_connections[key].append(websocket)

        if key not in self.listener_tasks:
            self.listener_tasks[key] = asyncio.create_task(self._redis_listener(key))
        print(f"✅ 웹소켓 연결됨: Key={key}")

    def disconnect(self, key: Any, websocket: WebSocket):
        if key in self.active_connections:
            if websocket in self.active_connections[key]:
                self.active_connections[key].remove(websocket)
            if not self.active_connections[key]:
                if key in self.listener_tasks:
                    self.listener_tasks[key].cancel()
                    del self.listener_tasks[key]
                del self.active_connections[key]

    async def _redis_listener(self, key: Any):
        # [수정] 키 타입에 따라 채널 분기 (int=로그, str=알람)
        channel_name = f"logs_{key}" if isinstance(key, int) else f"alarms_{key}"
        
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel_name)

        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True)
                if message:
                    data = message['data']
                    if key in self.active_connections:
                        for connection in self.active_connections[key]:
                            try:
                                await connection.send_text(data)
                            except:
                                pass
                await asyncio.sleep(0.01)
                
        except asyncio.CancelledError:
            print(f"📡 구독 중단: {channel_name}")
            await pubsub.unsubscribe(channel_name)
            await pubsub.close()
        except Exception as e:
            print(f"❌ Redis 리스너 에러: {e}")

    async def broadcast(self, key: Any, message: str):
        # [수정] 키 타입에 따라 채널 분기
        channel_name = f"logs_{key}" if isinstance(key, int) else f"alarms_{key}"
        try:
            await self.redis.publish(channel_name, message)
        except Exception as e:
            print(f"❌ Redis 게시 실패: {e}")
        
        # 로컬 소켓 전송 (백업)
        if key in self.active_connections:
            for connection in self.active_connections[key]:
                try:
                    await connection.send_text(message)
                except Exception as e:
                    pass

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

def get_db():
    db_logger.info("📡 [DB] 연결 시도 중...")
    db = SessionLocal()
    try:
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

# [수정] user_id 파라미터 추가
def run_ansible_task(playbook_name: str, extra_vars: dict, project_id: int, loop: asyncio.AbstractEventLoop, user_id: str):
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

    process = None
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

                clean_line = line.strip()
                try:
                    if "TASK [Gathering Facts]" in clean_line:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::STEP_2_OK::"), loop)
                    elif "TASK [Wait for VM to boot]" in clean_line:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::STEP_3_OK::"), loop)
                    elif "PLAY RECAP" in clean_line:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::STEP_4_OK::"), loop)
                    
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast(int(project_id), clean_line), 
                        loop
                    )
                except Exception:
                    pass
        process.stdout.close()
        process.wait()
        
        asyncio.run_coroutine_threadsafe(manager.broadcast(project_id, "::DEPLOY_COMPLETE::"), loop)

        # [추가] 알람 전송 로직
        current_time = datetime.now().strftime('%H:%M:%S')
        alarm_payload = {
            "type": "real_alarm",
            "timestamp": current_time,
            "message": "",
            "level": ""
        }

        if process.returncode == 0:
            ans_logger.info(f"✅ [Ansible] 배포 완료 성공! (IPs: {', '.join(target_ips)})")
            alarm_payload["message"] = f"✅ [성공] 프로젝트 #{project_id} 프로비저닝 완료"
            alarm_payload["level"] = "success"
        else:
            ans_logger.error(f"🚨 [Ansible] 배포 실패. 종료 코드: {process.returncode}")
            alarm_payload["message"] = f"❌ [실패] 프로젝트 #{project_id} 프로비저닝 오류"
            alarm_payload["level"] = "error"
        
        # 유저 ID가 있으면 알람 전송
        if user_id:
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(str(user_id), json.dumps(alarm_payload)), 
                loop
            )

    except Exception as e:
        ans_logger.error(f"🚨 [Ansible 실행 중 예외 발생] {str(e)}")
        # 에러 발생 시에도 알람 전송 시도
        if user_id:
            asyncio.run_coroutine_threadsafe(
                manager.broadcast(str(user_id), json.dumps({
                    "type": "real_alarm",
                    "timestamp": datetime.now().strftime('%H:%M:%S'),
                    "message": f"❌ [시스템 에러] {str(e)}",
                    "level": "error"
                })), loop
            )
    
    # 3. DB 상태 업데이트
    db = SessionLocal()
    try:
        project = db.query(ProjectHistory).filter(ProjectHistory.id == project_id).first()
        vms_in_project = db.query(WorkloadPool).filter(WorkloadPool.project_id == project_id).all()

        if process and process.returncode == 0:
            if project:
                project.status = "COMPLETED"
            for vm in vms_in_project:
                vm.status = "assigned"
            ans_logger.info(f"✅ [DB] 프로젝트 #{project_id} 배포 성공. 자원 상태를 'assigned'로 확정")
        else:
            if project:
                project.status = "FAILED"
            for vm in vms_in_project:
                vm.status = "available"
                vm.project_id = None
                vm.owner_tag = None
                ans_logger.warning(f"🔄 [자원 회수] 배포 실패로 {vm.ip_address} 자원을 풀에 반납")

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

@app.post("/api/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(UserAccount).filter(UserAccount.username == req.user_id).first()
    
    if not user:
        raise HTTPException(status_code=401, detail="존재하지 않는 사용자입니다.")
    
    try:
        if decrypt_password(user.password) != req.password:
            raise HTTPException(status_code=401, detail="비밀번호가 일치하지 않습니다.")
    except Exception:
        raise HTTPException(status_code=401, detail="인증에 실패했습니다.")

    if user.status == "pending":
        raise HTTPException(status_code=403, detail="관리자의 승인을 기다리고 있는 계정입니다.")
    elif user.status == "rejected":
        raise HTTPException(status_code=403, detail="가입 신청이 거절되었습니다.")

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
    existing_user = db.query(UserAccount).filter(UserAccount.username == user_data['username']).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")

    encrypted_pw = encrypt_password(user_data['password'])
    
    new_user = UserAccount(
        username=user_data['username'],
        password=encrypted_pw,
        full_name=user_data['full_name'],
        role="user",
        status="pending"
    )
    
    db.add(new_user)
    db.commit()
    return {"message": "가입 신청이 완료되었습니다. 관리자 승인 후 이용 가능합니다."}


TEMPLATE_MAP = {
    "single": 1,
    "standard": 3,
    "enterprise": 5,
    "k8s_small": 3,
}

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
    if isinstance(current_user, str):
        user_id = current_user
        user_db = db.query(UserAccount).filter(UserAccount.username == user_id).first()
        user_role = user_db.role if user_db else "user"
    else:
        user_id = current_user.get("sub")
        user_role = current_user.get("role", "user")

    if str(user_role).lower() == "admin":
        my_vms = db.query(WorkloadPool).all()
        print(f"👑 관리자 접속: {len(my_vms)}개의 모든 VM을 로드합니다.")
    else:
        my_vms = db.query(WorkloadPool).join(
            ProjectHistory, WorkloadPool.project_id == ProjectHistory.id
        ).filter(ProjectHistory.owner == user_id).all()
        print(f"👤 일반 유저({user_id}) 접속: {len(my_vms)}개의 소유 VM을 로드합니다.")
    
    if not my_vms:
        return []

    queries = {
        'cpu': '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)',
        'memory': '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
        'disk': '(1 - (node_filesystem_avail_bytes{mountpoint="/"}/node_filesystem_size_bytes{mountpoint="/"})) * 100'
    }

    results = await asyncio.gather(*[query_prometheus_async(q) for q in queries.values()])
    cpu_data, mem_data, disk_data = results

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

    final_result = []

    for vm in my_vms:
        project_name = "Ready to use"
        proj = None
        
        if vm.project_id:
            proj = db.query(ProjectHistory).filter(ProjectHistory.id == vm.project_id).first()
            if proj: 
                project_name = proj.service_name

        ip_key = vm.ip_address.lower() if vm.ip_address else ""
        usage = metrics_map.get(ip_key, {})

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
            "status": status_display
        })

    return final_result


@app.post("/api/provision")
async def create_infrastructure(
    request: ProjectRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db),
    current_user: Any = Depends(get_current_user)
):

    user_template = request.config.get('template', 'single')

    if user_template not in TEMPLATE_MAP:
        ans_logger.error(f"❌ 지원하지 않는 템플릿: {user_template}")
        return {"status": "error", "message": f"지원하지 않는 템플릿 유형입니다: {user_template}"}

    needed_count = TEMPLATE_MAP.get(user_template, 1)
    ans_logger.info(f"🚀 [주문 분석] 템플릿: {user_template} | 필요 수량: {needed_count}대")

    vms = db.query(WorkloadPool).filter(WorkloadPool.status == "available").order_by(WorkloadPool.id.asc()).limit(needed_count).all()
    if len(vms) < needed_count:
        return {"status": "error", "message": f"가용한 자원이 부족합니다. (필요: {needed_count}, 가용: {len(vms)})"}
    
    assigned_ips = [vm.ip_address for vm in vms]
    target_vm_names = [vm.vm_name for vm in vms]
    ip_string = ", ".join(assigned_ips)

    lb_hosts, web_hosts, db_hosts = [], [], []

    if user_template == "standard":
        lb_hosts = [assigned_ips[0]]
        web_hosts = [assigned_ips[1]]
        db_hosts = [assigned_ips[2]]
    elif user_template == "enterprise":
        lb_hosts = [assigned_ips[0]]
        web_hosts = assigned_ips[1:3]
        db_hosts = assigned_ips[3:5]
    else: 
        lb_hosts = web_hosts = db_hosts = assigned_ips

    ans_logger.info(f"\n🚀 [멀티 주문] 서비스명: {request.serviceName} | 템플릿: {user_template} ({needed_count}대)")
    
    settings = db.query(SystemSetting).first()
    if not settings:
        return {"status": "error", "message": "시스템 설정이 없습니다."}
    
    try:
        vcenter_pw = decrypt_password(settings.vcenter_password)
        vcenter_user = settings.vcenter_user
        vcenter_ip = settings.vcenter_ip
        
        selected_packages = request.config.get('packages', [])
    except Exception as e:
        ans_logger.error(f"🚨 [준비 실패] {e}")
        return {"status": "error", "message": "데이터 준비 중 오류 발생"}

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
    db.commit()
    db.refresh(new_project)

    user_tag = request.userName
    ans_logger.info(f"👤 주문자 확인: {user_tag}")

    for vm in vms:
        vm.status = "provisioning"
        vm.owner_tag = request.userName
        vm.project_id = new_project.id
    db.commit()
    ans_logger.info(f"📍 [자원 할당] {', '.join(target_vm_names)} ({ip_string}) -> 프로젝트 #{new_project.id}")

    target_playbook = "configure_workload.yml"
    ansible_vars = {
        "vcenter_hostname": vcenter_ip,
        "vcenter_username": vcenter_user,
        "vcenter_password": vcenter_pw,
        "target_ips": assigned_ips,
        "target_vm_names": target_vm_names,
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

    # [수정] run_ansible_task에 user_tag(ID) 전달
    background_tasks.add_task(run_ansible_task, target_playbook, ansible_vars, new_project.id, loop, user_tag)

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
    query = db.query(ProjectHistory)
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
    current_user: dict = Depends(get_current_user)
):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")

    s = db.query(SystemSetting).first()
    if not s:
        s = SystemSetting()
        db.add(s)

    if req.admin_password != s.admin_password:
        raise HTTPException(status_code=403, detail="관리자 비밀번호가 일치하지 않습니다.")

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

    user.status = "active"
    
    existing_quota = db.query(UserQuota).filter(UserQuota.username == username).first()
    if not existing_quota:
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

# [추가] 알림 전용 웹소켓 엔드포인트
@app.websocket("/ws/alarms/{user_id}")
async def websocket_alarm_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(user_id, websocket) # str 키 사용
    try:
        while True:
            await websocket.receive_text()
    except:
        manager.disconnect(user_id, websocket)
        
# ==========================================
# WebSocket SSH
# ==========================================
@app.websocket("/ws/ssh/{ip}")
async def websocket_ssh(websocket: WebSocket, ip: str):
    await websocket.accept()
    
    await websocket.send_text("\r\n")
    await websocket.send_text(f"\x1b[36mConnecting to {ip}...\x1b[0m\r\n")
    await websocket.send_text("\x1b[33mWelcome to H-CMP Console Service\x1b[0m\r\n")
    await websocket.send_text("========================================\r\n")

    async def read_input(echo=True):
        buffer = ""
        while True:
            data = await websocket.receive_text()
            for char in data:
                if char == "\r" or char == "\n":
                    await websocket.send_text("\r\n")
                    return buffer.strip()
                elif char == "\x7f" or char == "\x08":
                    if len(buffer) > 0:
                        buffer = buffer[:-1]
                        await websocket.send_text("\b \b")
                else:
                    buffer += char
                    if echo:
                        await websocket.send_text(char)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    attempts = 0
    max_attempts = 3

    while attempts < max_attempts:
        try:
            await websocket.send_text("login: ")
            username_input = await read_input(echo=True)
            if not username_input: continue

            await websocket.send_text("Password: ")
            password_input = await read_input(echo=False)

            await websocket.send_text("\r\nVerifying credentials...\r\n")

            await asyncio.to_thread(client.connect, ip, username=username_input, password=password_input, timeout=10)
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
            try:
                error_msg = str(e)
                if "10060" in error_msg:
                    error_msg = "Connection Timeout (Check IP or Firewall)"
                await websocket.send_text(f"\r\n\x1b[31mConnection Error: {error_msg}\x1b[0m\r\n\r\n")
            except: pass
            await websocket.close()
            return

    channel = client.invoke_shell()
    
    try:
        channel.resize_pty(width=80, height=24)
    except:
        pass

    await websocket.send_text(f"\x1b[32mLast login: {datetime.now().strftime('%a %b %d %H:%M:%S')} from WebConsole\x1b[0m\r\n")

    async def recv():
        try:
            while True:
                if channel.recv_ready():
                    raw_data = channel.recv(1024).decode(errors="ignore")
                    clean_data = re.sub(r'\x1b\[\?2004[hl]', '', raw_data)
                    await websocket.send_text(clean_data)
                
                if channel.exit_status_ready():
                    break
                await asyncio.sleep(0.01)
        except: pass

    async def send():
        try:
            while True:
                data = await websocket.receive_text()
                if "\r" in data: data = data.replace("\r", "\n")
                channel.send(data)
        except: pass

    await asyncio.gather(recv(), send())
    
    try: client.close()
    except: pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)