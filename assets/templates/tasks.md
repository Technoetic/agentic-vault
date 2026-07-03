---
title: "{{PROJECT_NAME}} tasks"
type: project
status: active
ai_priority: high
tags: [tasks, kanban]
related:
  - "[[{{PROJECT_NAME}} context]]"
project: {{PROJECT_NAME}}
created: {{DATE}}
updated: {{DATE}}
---

# 작업 칸반 (Now / Next / Backlog / Blocked / Done)

규칙: 체크박스 항목(`- [ ]`)으로 관리하고, 상태가 바뀌면 해당 섹션으로 **이동**한다(복사 금지 — 같은 항목이 두 섹션에 있으면 안 된다).
완료 항목은 체크(`- [x]`) 후 Done으로 옮기고 완료 날짜를 병기한다. 다중 에이전트 환경에서는 전체 덮어쓰기(Write) 금지 — Edit로 자기 항목만.

## Now (이번 세션 최우선)

- [ ] (지금 가장 먼저 할 한 가지 — 3개 이내 유지)

## Next (다음 착수 대기)

- [ ] (Now가 비면 승격할 항목)

## Backlog

- [ ] (언젠가 할 일 — 트리거 조건이 있으면 병기)

## Blocked

- [ ] (블로커 사유와 해제 조건을 반드시 명시: "~ 대기 중, ~되면 재개")

## Done

- [x] (완료 항목, YYYY-MM-DD)
