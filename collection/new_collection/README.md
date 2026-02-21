# New Collection (`new_collection`)

이 저장소는 Ansible을 사용하여 애플리케이션 인프라를 프로비저닝, 배포, 관리하기 위한 역할(Roles)과 태스크 모음입니다.
이 컬렉션은 온클릭 서비스 CMP 프로그램 UI 카테고리 기획에 맞추어 **총 15개의 독립된 역할**로 모듈화되어 있습니다.

## 역할 (Roles) 구조 개요

새롭게 개편된 `roles` 하위 구조는 의존성을 낮추고 플레이북에서 개별 선택적 설치가 가능하도록 구성되었습니다.
각 역할은 기본 설치를 담당하는 `main.yml`과 환경을 복구/제거하는 `cleanup.yml`을 포함합니다.

### 1. HA & NETWORK
- **`gateway`**: 네트워크 라우팅 및 IP 포워딩 설정(`net.ipv4.ip_forward = 1`)을 제어합니다.
- **`haproxy`**: Web 전용 고성능 로드밸런서 HAProxy 서비스 패키지를 구성합니다.
- **`patroni`**: PostgreSQL 고가용성 클러스터(DB A/S) 구조를 구성합니다.
  - **논리적 통합 아키텍처**: 과거 환경의 `db_haproxy`, `etcd_db`, `patroni` 3개 역할에 분산되어 있던 복잡한 아키텍처를 하나의 `patroni` 역할로 완벽히 통합했습니다.
  - **직렬 실행 보장**: `tasks/main.yml` 내에서 `include_tasks`를 활용하여 `db_haproxy.yml` -> `etcd_db.yml` -> `patroni_core.yml` 순서로 호출 제어 로직을 구축했습니다.
  - **호스트 의존성 분리**: 통합된 로직이 타겟(호스트) 충돌을 일으키지 않도록 `db_haproxy` 작업은 `dblb` 호스트 그룹에서만, `etcd` 및 `patroni` 코어 모듈은 `db_cluster` 그룹에서만 동작하도록 Ansible의 `group_names` 분기 제어 로직을 통해 스마트하게 필터링합니다.
  - **기존 무결성 유지**: 설정에 쓰이는 모든 `defaults`, `handlers`, `templates`, `files` 및 `cleanup` 스크립트를 변경 및 파편화 없이 이관하여 레거시 코드의 100% 무결성을 보장합니다.

### 2. WEB (ENGINE)
- **`nginx`**: Nginx 웹 서버 엔진 설치 및 서비스 연동을 처리합니다.
- **`tomcat`**: WAS(Web Application Server) Tomcat 컨테이너 설치를 진행합니다.

### 3. PROGRAM (RUNTIME)
- **`python`**: Python 3.x 코어 런타임 환경과 의존성 관리 도구인 `pip` 구성을 완료합니다.
- **`nodejs`**: Node.js 런타임 환경과 `npm` 초기화를 설정합니다.

### 4. DB (DATA)
- **`postgresql`**: 관계형 데이터베이스인 PostgreSQL 서버의 단독 설치, 초기화(`initdb`) 및 구동을 담당합니다.
- **`mysql`**: 관계형 데이터베이스 MariaDB/MySQL 서버를 설치 및 서비스 런칭합니다.
- **`redis`**: 키-값(Key-Value) 구조의 인메모리 캐시 데이터 스토어 Redis 서버 구축을 진행합니다.

### 5. DEVOPS (TOOLS)
- **`elasticsearch`**: 로깅, 모니터링 데이터 분석을 위한 ELK 스택의 핵심인 Elasticsearch 검색 엔진을 설치합니다.
- **`kibana`**: Elasticsearch 데이터를 시각화하는 Kibana 대시보드 환경을 구성합니다.
- **`jenkins`**: 지속적 통합/지속적 배포(CI/CD) 자동화를 위한 Jenkins(Java 기반) 서버 환경을 셋업합니다.
- **`docker`**: 컨테이너 런타임 엔진인 Docker CE 서비스 구축을 관장합니다.

### 6. SOURCE CODE UPLOAD (DEPLOYMENT)
- **`was_deploy`**: `was_filename`, `target_vm_name` 등의 변수를 받아, 타겟 Host의 IP(192.168.40.x 관리 대역 룰 기반)를 동적으로 추적하여 소스 파일을 목적지(`/opt/was_app` 등)로 안전하게 복사하고 배포합니다.

## 플레이북 연동 구조
`playbooks/site.yml`과 `playbooks/cleanup.yml`은 위 15가지 역할 구성을 `packages_to_install` 및 `was_filename` 매개변수 유무에 따라 **선택적(Include Role 지시자 기반)**으로 실행할 수 있게 완벽하게 통합되어 있습니다.
