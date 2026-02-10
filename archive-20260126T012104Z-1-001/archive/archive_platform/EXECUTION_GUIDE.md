# Archive Platform 실행 가이드

## 사전 준비

### 1. Ansible 설치 확인
```bash
ansible --version
# Ansible 2.9 이상 필요
```

### 2. SSH 키 설정
```bash
# Control Node에서 모든 타겟 서버로 SSH 키 복사
ssh-copy-id ansible@192.168.40.10  # Gateway
ssh-copy-id ansible@192.168.40.20  # ALB
ssh-copy-id ansible@192.168.40.30  # Web1
ssh-copy-id ansible@192.168.40.40  # Web2
ssh-copy-id ansible@192.168.40.50  # DBLB1
ssh-copy-id ansible@192.168.40.51  # DBLB2
ssh-copy-id ansible@192.168.40.52  # DCS
ssh-copy-id ansible@192.168.40.60  # DB1
ssh-copy-id ansible@192.168.40.70  # DB2
```

### 3. 연결 테스트
```bash
cd archive_platform
ansible all -i inventory/hosts.ini -m ping
```

## 실행 방법

### 방법 1: 대화형 스크립트 사용 (권장)

```bash
cd archive_platform
chmod +x deploy.sh
./deploy.sh
```

메뉴에서 원하는 옵션을 선택하여 배포합니다.

### 방법 2: 직접 명령어 실행

#### 전체 배포
```bash
cd archive_platform
ansible-playbook -i inventory/hosts.ini playbooks/site.yml
```

#### 단계별 배포 (권장 순서)

```bash
# 1단계: DB 클러스터 배포
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags db

# 2단계: DBLB 배포
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags dblb

# 3단계: Web 서버 배포
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags web

# 4단계: ALB 배포
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags alb

# 5단계: Gateway 배포
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags gateway
```

#### 계층별 배포

```bash
# 백엔드만 배포 (DB + DBLB)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags backend

# 프론트엔드만 배포 (Gateway + ALB + Web)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags frontend

# 웹 계층만 배포 (ALB + Web)
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags web_tier
```

#### 특정 컴포넌트만 배포

```bash
# Gateway만
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags gateway

# ALB만
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags alb

# Web 서버만
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags web
```

#### Dry-run (실제 변경 없이 확인)

```bash
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --check --diff
```

#### Verbose 모드 (디버깅)

```bash
ansible-playbook -i inventory/hosts.ini playbooks/site.yml -v    # 기본
ansible-playbook -i inventory/hosts.ini playbooks/site.yml -vv   # 상세
ansible-playbook -i inventory/hosts.ini playbooks/site.yml -vvv  # 매우 상세
```

## 검증 방법

### 1. Gateway 검증
```bash
# Gateway에서 ALB로 프록시 확인
curl http://172.16.6.77
```

### 2. ALB 검증
```bash
# ALB 상태 페이지 확인
curl http://192.168.40.20:8080/stats

# Web 서버 로드밸런싱 확인
for i in {1..10}; do curl -s http://192.168.10.20 | grep Hostname; done
```

### 3. Web 서버 검증
```bash
# 각 Web 서버 직접 접속
curl http://192.168.10.30
curl http://192.168.10.40
```

### 4. DBLB 검증
```bash
# 1. DBLB에서 Patroni Master 확인 (백엔드 헬스체크)
ssh ansible@192.168.40.50
curl -I http://192.168.30.30:8008/master  # db1
curl -I http://192.168.30.40:8008/master  # db2
# 200 OK가 나오는 쪽이 현재 Master

# 2. Web 서버에서 DBLB 접속 확인 (서비스 연결성)
# DBLB VIP (192.168.20.100)의 5432 포트 접속 테스트
ansible web -i inventory/hosts.ini -m shell -a "curl -v telnet://192.168.20.100:5432"
# 'Connected to ...' 메시지가 나오면 성공 (Ctrl+C로 종료)
```

### 5. DB 검증
```bash
# DBLB VIP를 통한 DB 접속 테스트
psql -h 192.168.20.100 -U admin -d cmp_db -c "SELECT version();"
```

## 트러블슈팅

### 연결 실패 시
```bash
# 특정 호스트 연결 테스트
ansible gateway -i inventory/hosts.ini -m ping

# SSH 연결 디버깅
ssh -vvv ansible@192.168.40.10
```

### Role 경로 오류 시
```bash
# ansible.cfg의 roles_path 확인
cat ansible.cfg | grep roles_path

# 컬렉션 내부 roles 디렉터리 확인
ls -la roles/
# 다음 디렉터리들이 존재해야 함:
# gateway, alb, web, db_haproxy, etcd_db, patroni

# 특정 Role 구조 확인
ls -la roles/patroni/
ls -la roles/etcd_db/
ls -la roles/db_haproxy/
```

### 방화벽 문제 시
```bash
# 각 서버에서 방화벽 상태 확인
ansible all -i inventory/hosts.ini -m shell -a "firewall-cmd --list-all"
```

## 롤백 방법

```bash
# 특정 컴포넌트 서비스 중지
ansible web -i inventory/hosts.ini -m service -a "name=nginx state=stopped"

# 설정 파일 복원 (백업이 있는 경우)
ansible web -i inventory/hosts.ini -m copy -a "src=/etc/nginx/nginx.conf.backup dest=/etc/nginx/nginx.conf backup=yes"
```

## 추가 옵션

### 특정 호스트만 대상으로 실행
```bash
# web1만 배포
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags web --limit web1

# DBLB1만 배포
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags dblb --limit dblb1
```

### 병렬 실행 제어
```bash
# 동시에 2개씩만 실행
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --forks 2
```
