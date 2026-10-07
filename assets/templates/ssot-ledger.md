---
title: "SSOT — 시간 범위가 있는 사실 대장"
type: reference
status: active
ai_priority: high
tags: [ssot, facts, temporal]
created: {{DATE}}
updated: {{DATE}}
---

# 시간 범위가 있는 사실 대장

값은 이 대장에 두고 다른 노트는 이 노트를 위키링크로 참조한다. 충돌 값의 진위나 조직 정체성은 자동 선택하지 않는다.

`valid_from`은 포함, `valid_until`은 제외하는 ISO 날짜 구간이다. 종료일 공란은 끝이 정해지지 않은 구간이며 시작일 공란은 유효 범위 미상이다. `recorded`는 기록 날짜다. `source`는 실제 출처이고 `status=confirmed`인 행은 출처가 필수다. `superseded_by`는 후속 항목을 가리키는 기록이며 그 자체로 종료일을 추정하거나 이전 행을 삭제하지 않는다. 값·출처·날짜가 불명확하면 공란을 유지하고 확인 과제를 남긴다.

## 사실

| subject | relation | value | valid_from | valid_until | recorded | source | superseded_by | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 구판 호환과 명시적 마이그레이션

허용하는 구판 열 이름: 주체/대상/주어/entity → subject, 항목/관계/속성/predicate/property → relation, 값/사실/내용/fact → value, 시작일/유효 시작/유효시작/유효시작일/from/effective_from → valid_from, 종료일/유효 종료/유효종료/유효종료일/valid_to/until/effective_until → valid_until, 기록일/기록시각/관측일/recorded_at/observed_at → recorded, 출처/근거/evidence → source, 대체 항목/대체항목/후속항목/대체됨/superseded → superseded_by, 상태/확정여부/state → status.

셀 안의 파이프는 `\|`로 이스케이프한다. 다른 열과 사용자 본문은 유지한다. 구판 마이그레이션은 열 이름을 정리하고 없는 열을 공란으로 추가한다. 날짜나 사실을 추정하지 않으며 확인 경고가 남을 수 있다.

식별에 필요한 subject/relation/value 열이 없는 표는 자동 이행하지 않는다. 사용자가 주체와 관계를 명시한 후 다시 준비한다. 종료일 없는 서로 다른 값은 시작일을 모르는 경우에도 확인 경고를 남기되 겹치는 날짜를 추정하지 않는다. 확정 상태는 status 셀 또는 명시적인 `확정`/`✅확정`/`confirmed` 제목에서 읽을 수 있으며 출처 진위는 별도 검증한다.

`vault_ssot.py --vault <vault> prepare --path <ledger.md>`는 원문·설정 해시에 묶인 제안만 출력한다. 제안을 검토하고 현재 사용자의 승인을 받은 실행자가 JSON 제안을 `apply --approve`의 표준 입력으로 전달한다. 승인 표시나 제안 해시는 사람의 신원·출처 진위 증명이 아니다. 원문 또는 설정이 달라지면 다시 준비한다. 일반 업그레이드는 사용자 대장을 자동 변경하지 않는다.
