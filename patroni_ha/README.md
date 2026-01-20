# Patroni + etcd + HAProxy + Keepalived (CentOS Stream 9) - Ansible 전체 코드

## 0) 디렉터리
- ansible.cfg / inventory.ini / site.yml
- roles/
  - etcd
  - patroni_postgres
  - haproxy_pg
  - keepalived_vip

## 1) 가장 중요한 체크 2개
1) **[dcs] 그룹에는 etcd 노드만** 넣으세요. (db IP 섞이면 Patroni가 DCS URL 파싱하다 죽습니다)
2) Patroni `/etc/patroni/patroni.yml`의 `etcd3.hosts`는 **host:port만** 넣고,
   프로토콜은 `protocol: http`로 분리합니다. (http:// 절대 넣지 말기)

## 2) 실행
```bash
ansible-playbook -i inventory.ini site.yml
```

특정 부분만:
```bash
ansible-playbook -i inventory.ini site.yml --tags etcd
ansible-playbook -i inventory.ini site.yml --tags patroni
ansible-playbook -i inventory.ini site.yml --tags haproxy,keepalived
```

## 3) 검증
DB 노드에서:
```bash
ss -lntp | egrep '8008|5432'
curl -sS http://127.0.0.1:8008/health
```

LB(VIP) 노드에서:
```bash
ip a | grep {{ db_vip }}
ss -lntp | grep 5432
```
