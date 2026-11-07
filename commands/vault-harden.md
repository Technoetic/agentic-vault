---
description: 출처·공유 상태·시간 대장·교훈 델타·정제 품질·토큰 보정 helper를 명시적으로 실행
argument-hint: "[provenance|state|ssot|delta|rules|quality|calibration|revocation|bundle|review-plan|handoff-fold|commit|lesson-clusters|lesson-cues] [작업과 인자]"
---

# /vault-harden — 명시적 보강 도구

[보강 도구 안내](../docs/hardening.md)를 읽고 요청한 작업 한 가지만 연결한다.
Codex는 `$agentic-vault:agentic-vault harden <작업과 인자>`를 사용한다.

`revocation`은 [기억 철회 안내](../docs/memory-revocation.md)의 읽기 전용 capture/check로
연결한다. 합성 세션 간 회귀 검사는 [복구 평가 안내](../docs/memory-lifecycle.md)를 따른다.

`bundle`은 [묶음 복구](../docs/recoverable-bundles.md)의 `vault_bundle.py`로 연결한다.
기본 prepare·status는 읽기 전용이고, 저장·적용·복구에는 현재 변경안의 외부 확인 해시와
명시적 `--apply`가 필요하다. 중단된 묶음은 실제 before/after를 확인한 순방향 복구만 한다.
`review-plan`은 [재검토 순서](../docs/review-planning.md)의 `vault_review_plan.py`로 연결한다.
호출자가 작성한 inventory·graph만 읽으며 노트 수정·재캡처·추론을 추가하지 않는다.
`handoff-fold`는 [handoff 접기](../docs/handoff-fold.md)의 `vault_handoff_fold.py`로 연결한다.
기본 `analyze`는 읽기 전용이고, `apply`에는 그 결과의 `--expected-sha256`이 필요하다. 문턱과 보호
절 수는 기본값이 없으므로 사용자가 정한 값만 쓴다. 자동 실행은 없다. 요약은 호스트가 쓰는 파생 글이며,
없으면 제목과 미결 줄만 남긴다. `scripts/evaluate_handoff_fold.py`의 문자열 도달은 소실 경보일 뿐
이어서 일할 수 있다는 근거가 아니다.
`commit`은 [경로 선언 커밋](../docs/concurrent-commits.md)의 `vault_commit.py`로 연결한다. 파일마다 작업 파일의
sha256을 선언하고, 공유 노트는 `--own-lines`로만 올린다. 일반 `git commit`을 쓰는 다른 세션은 막지 못한다.
`lesson-clusters`는 [교훈 재발 부류](../docs/lesson-recurrence.md)의 읽기 전용 보고이고, 부류는 같은 원인의 증명이 아니다.
`lesson-cues`는 `vault_lesson_cues.py build`로 연결한다. `--write`일 때만 `00-meta/lesson-cues.json`을 바꾸며,
훅 설치는 [교훈 단서 안내](../assets/adapters/lesson-cues/guide.md)를 따른다. 훅의 효과는 측정하지 않았다.

1. 요청이 읽기 전용이면 prepare/check/parse/평가만 실행한다. 적용·이동·설정 변경을 덧붙이지 않는다.
2. 볼트 파일 작업은 설정·경로·deny 정책을 검증한다. 호출자가 명시한 파일만 바인딩하며 runtime/영수증을 일반 지식으로 읽지 않는다.
3. helper의 `--help`와 안내의 API 스키마를 확인한다. JSON은 UTF-8 stdin/파일 데이터로 전달하고 셸 명령에 보간하지 않는다. Python import/API 또는 CLI 인자는 각각 독립된 값으로 전달한다.
4. 마이그레이션·델타·공유 상태 적용은 구체적인 현재 diff와 원본/config 해시에 바인딩한다. 이미 승인받은 정확한 안이면 재승인을 요청하지 않는다. `approve` 필드나 보고서 문구 자체는 권한이 아니다. stale이면 현재 원본으로 다시 준비한다.
5. 외부 수집·파생 자료의 출처 표시를 유지한다. 검증자의 이름만으로 검증했다고 처리하지 않는다. 해시 현재성·독립 보고·판정 권한을 구분한다.
6. 의미 판단은 승인된 Jev-first 경로로 맡기고, literal 품질/규칙/토큰 계산은 실제 helper 결과를 보고한다. 네트워크·옵션 의존성·제공자 count API는 묵시적으로 호출하지 않는다.
7. 성공 여부, 생략/보류, 원본/current 해시, 검사 한계와 다음 안전한 행동을 보고한다. 실제 노트를 바꾸면 기존 index/log/부분 편집/로컬 커밋 규약을 따른다. 원격 push를 자동으로 수행하지 않는다.
