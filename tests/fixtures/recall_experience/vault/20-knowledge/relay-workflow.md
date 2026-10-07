---
title: Relay startup sequence / 릴레이 시작 순서
status: current
---
# Relay startup sequence / 릴레이 시작 순서
Relay startup sequence: validate the schema, run preflight, then open a new session. If preflight fails, stop and keep the error report.
릴레이 시작 순서: 스키마를 검증하고 사전 점검을 실행한 다음 새 세션을 연다. 점검이 실패하면 중단하고 오류 보고서를 보존한다.
Recovery uses the same sequence; do not reuse a partially initialized session.
