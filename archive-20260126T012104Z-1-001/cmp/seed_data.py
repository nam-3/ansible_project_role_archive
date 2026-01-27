import os
import sys
from sqlalchemy.orm import Session
from main import SessionLocal, WorkloadPool, engine, Base

# 테이블이 없으면 생성
Base.metadata.create_all(bind=engine)

def seed_data():
    db: Session = SessionLocal()
    try:
        # 기존 데이터 확인
        count = db.query(WorkloadPool).count()
        if count > 0:
            print(f"⚠️ [Skip] 이미 {count}개의 자원이 존재합니다.")
            return

        print("🚀 초기 자원 데이터(VM Pool) 생성을 시작합니다...")

        # 더미 데이터 생성 (192.168.10.31 ~ 192.168.10.40)
        initial_vms = []
        for i in range(1, 11):
            vm_ip = f"192.168.10.{30 + i}"
            vm_name = f"wkld-{i:02d}"
            
            vm = WorkloadPool(
                ip_address=vm_ip,
                vm_name=vm_name,
                status="available",  # 초기 상태
                owner_tag=None,
                project_id=None,
                occupy_user=None
            )
            initial_vms.append(vm)

        db.add_all(initial_vms)
        db.commit()
        print(f"✅ 성공적으로 {len(initial_vms)}개의 VM 자원을 워크로드 풀에 등록했습니다.")
        print("   - IP 범위: 192.168.10.31 ~ 192.168.10.40")

    except Exception as e:
        print(f"🚨 에러 발생: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    # 환경 변수 강제 설정 (로컬 실행 시 필요할 수 있음)
    # os.environ["DB_HOST"] = "192.168.30.20" 
    seed_data()
