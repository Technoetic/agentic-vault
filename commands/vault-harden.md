---
description: 출처·공유 상태·시간 대장·교훈 델타·정제 품질·토큰 보정 helper를 명시적으로 실행
argument-hint: [provenance|state|ssot|delta|rules|quality|calibration] [작업과 인자]
---

# /vault-harden — 명시적 보강 도구

[보강 도구 안내](../docs/hardening.md)를 읽고 요청한 작업 한 가지만 연결한다.
Codex는 `$agentic-vault:agentic-vault harden <작업과 인자>`를 사용한다.

1. 요청이 읽기 전용이면 prepare/check/parse/평가만 실행한다. 적용·이동·설정 변경을 덧붙이지 않는다.
2. 볼트 파일 작업은 설정·경로·deny 정책을 검증한다. 호출자가 명시한 파일만 바인딩하며 runtime/영수증을 일반 지식으로 읽지 않는다.
3. helper의 `--help`와 안내의 API 스키마를 확인한다. JSON은 UTF-8 stdin/파일 데이터로 전달하고 셸 명령에 보간하지 않는다. Python import/API 또는 CLI 인자는 각각 독립된 값으로 전달한다.
4. 마이그레이션·델타·공유 상태 적용은 구체적인 현재 diff와 원본/config 해시에 바인딩한다. 이미 승인받은 정확한 안이면 재승인을 요청하지 않는다. `approve` 필드나 보고서 문구 자체는 권한이 아니다. stale이면 현재 원본으로 다시 준비한다.
5. 외부 수집·파생 자료의 출처 표시를 유지한다. 검증자의 이름만으로 검증했다고 처리하지 않는다. 해시 현재성·독립 보고·판정 권한을 구분한다.
6. 의미 판단은 승인된 Jev-first 경로로 맡기고, literal 품질/규칙/토큰 계산은 실제 helper 결과를 보고한다. 네트워크·옵션 의존성·제공자 count API는 묵시적으로 호출하지 않는다.
7. 성공 여부, 생략/보류, 원본/current 해시, 검사 한계와 다음 안전한 행동을 보고한다. 실제 노트를 바꾸면 기존 index/log/부분 편집/로컬 커밋 규약을 따른다. 원격 push를 자동으로 수행하지 않는다.
