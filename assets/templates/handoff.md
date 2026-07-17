---
title: "{{PROJECT_NAME}} handoff"
type: project
status: active
ai_priority: high
tags: [handoff, session]
related:
  - "[[{{PROJECT_NAME}} context]]"
  - "[[{{PROJECT_NAME}} tasks]]"
project: {{PROJECT_NAME}}
created: {{DATE}}
updated: {{DATE}}
---

# Session Handoff (핫 캐시 — 매 세션 종료 시 갱신, 500단어 이내)

**기준 커밋(anchor):** `(없음)` — 이 handoff 내용이 반영하는 볼트 시점(git 해시). /vault-session-end가 갱신 시마다 이 줄을 교체하며, 그 이후의 변경은 `git log --oneline <anchor>..HEAD`로 확인한다. git 미사용 볼트는 `(없음)`으로 둔다.

이 파일은 다음 세션이 가장 먼저 읽는 인수인계서다. 세션 종료 시 4개 섹션을 최신 상태로 갱신하고 프런트매터 `updated`를 오늘 날짜로 바꿔라.
다중 에이전트 환경에서는 전체 덮어쓰기(Write) 금지 — 부분 수정(Edit)으로 자기 항목만 갱신하라.

## ▶ NEXT — 다음 세션이 바로 이어서 할 일

- (착수 지점을 구체적으로: 무엇을, 어떤 파일에서, 어떤 선행 조건이 충족되면. 다음 세션이 이 항목만 읽고 즉시 작업 가능해야 한다)

## §1 최근 완료된 작업

- (이번 세션의 성과를 검증 증거와 함께 — "완료" 단정 전에 검증 명령을 실행했는가)

## §2 인간의 확인이 필요한 사항

- (사용자 결정·승인 대기 항목 — 무엇을 왜 물어야 하는지, 선택지가 있으면 함께)

## §3 보류된 작업

- (착수했으나 중단된 것 + 중단 사유 + 재개 조건)
