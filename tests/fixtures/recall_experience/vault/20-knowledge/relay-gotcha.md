---
title: Relay stale lock gotcha / 릴레이 잠금 주의사항
status: current
---
# Relay stale lock gotcha / 릴레이 잠금 주의사항
Relay stale lock recovery: check the owning process before removing a lock. A timeout alone does not prove the owner exited.
릴레이 잠금 복구: 잠금을 제거하기 전에 소유 프로세스를 확인한다. 시간 초과만으로 프로세스 종료를 단정하지 않는다.
After a confirmed exit, clear only that session lock and rerun preflight.
