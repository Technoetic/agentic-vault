# 지침량·인덱스 정리·요구별 검증 누락 진단

v0.21.0은 기존 Doctor·기억·출처 흐름에 세 선택형 진단을 추가한다. 기본 세션 훅,
기억 검색, evidence API는 그대로 사용한다. 모든 새 경로는 로컬 읽기 전용이며
네트워크·모델 호출·인덱스 자동 수정·기록 삭제를 추가하지 않는다.

## 참고한 공식 소스

MoAI-ADK의 main `2aab5f797b75983e132af451da68f69e3426557b`를 기준으로 설계를 참고했다.
아래는 immutable 소스다. 조사 당시 최신 공개 태그 v3.1.2와 main 기능을 구분한다.

| 설계 | 공식 소스 | agentic-vault 적용 |
|---|---|---|
| 지침 분량 관측 | [instructions_loaded.go](https://github.com/modu-ai/moai-adk/blob/2aab5f797b75983e132af451da68f69e3426557b/internal/hook/instructions_loaded.go), [rule-loading-budget.md](https://github.com/modu-ai/moai-adk/blob/2aab5f797b75983e132af451da68f69e3426557b/.claude/rules/moai/workflow/rule-loading-budget.md) | 런타임 훅 대신 선택형 정적 파일 진단; 로드 사실은 미관측 |
| 원문 보존 정리 | [memory_fold.go](https://github.com/modu-ai/moai-adk/blob/2aab5f797b75983e132af451da68f69e3426557b/internal/cli/memory_fold.go) | 명시적 완료 항목과 해시를 검증하는 미리보기; 적용 없음 |
| 요구·검증 연결 | [lint_coverage_sibling.go](https://github.com/modu-ai/moai-adk/blob/2aab5f797b75983e132af451da68f69e3426557b/internal/spec/lint_coverage_sibling.go) | 기존 evidence/run 완전성 패턴을 일반 작업의 선언된 요구·검사에 적용 |

MoAI는 Apache-2.0 프로젝트다. 이번 변경은 설계 참고와 Python 표준 라이브러리 구현이며
Go 소스 코드를 복사하거나 원 프로젝트의 전체 기능·성능을 재현하지 않는다.

## 해석과 적용 범위

지침량은 정적으로 읽은 파일의 분량이다. 호스트별 조건·세션 상황에 따라 실제 로드는
달라질 수 있다. 인덱스 미리보기는 완료 상태를 스스로 판단하지 않으며 원문을 줄여
요약하지 않는다. 요구별 검증은 필요한 검사와 제출된 검사를 비교하는 기능이다.
이 세 진단은 권한·업무 성공·비용 절감을 인증하지 않는다.

CLI와 입력 스키마는 아래에 문서화된 최종 구현 계약을 따른다.

## 전체 지침 분량 확인

```text
python skills/agentic-vault/scripts/vault_doctor.py --vault PATH --instructions --format json
```

`instructions`에 원문 대신 경로·해시·분량·문제 코드가 나온다. 루트 `AGENTS.md`를
우선하고 없으면 `CLAUDE.md`를 확인한다. Markdown의 로컬 `@` 참조를 따라가며
`.claude/rules` 등의 규칙을 목록화한다. 코드 블록의 예시 참조는 실행하지 않는다.
조건이 있는 규칙과 로드가 확인되지 않은 후보를 나누며 같은 파일을 중복 합산하지 않는다.
Unicode 문자 수는 기존 훅의 UTF-16 상한·제공자 토큰 수와 다른 척도다.

`status=partial`이면 누락·차단·순환·변경·한도 문제를 먼저 확인한다. 제한은 JSON의
`limits`에 표시한다. 비밀·금지·링크 경로를 읽기 위해 다른 도구로 우회하지 않는다.
이 옵션을 생략한 Doctor는 기존 보고서와 동작을 유지한다.

## 완료 인덱스 정리 미리보기

인덱스를 읽고 호스트가 선택한 완료 행만 별도 manifest로 지정한다. 예시의 해시는
현재 파일에서 직접 구해야 하며 예시 문자열을 실제 해시로 사용하면 안 된다.
원문 quote는 지정한 행 범위 전체와 정확히 일치해야 한다.

```text
python skills/agentic-vault/scripts/vault_index_fold.py --vault PATH --input 00-meta/fold-plan.json
```

입력 계약은 다음과 같다. `source`와 `destination`은 이미 존재하는 허용된 파일이다.
`destination_link`는 원본 인덱스에 이미 있는 `[[목적지 파일명]]` 연결의 정확한 행이다.
`references`에는 이동을 검토하는 완료 행의 기존 위키링크 대상을 명시하고 현재 해시를 묶는다.
실제 해시·quote·행 번호로 교체한 뒤 사용한다.

```json
{
  "version": 1,
  "source": {"path": "00-meta/index.md", "expected_sha256": "CURRENT_SOURCE_SHA256"},
  "destination": {"path": "20-knowledge/completed.md", "expected_sha256": "CURRENT_DESTINATION_SHA256"},
  "destination_link": {"start_line": 20, "end_line": 20, "quote": "- [[completed]] — 완료 기록\n"},
  "items": [{
    "id": "item-1", "state": "completed",
    "source": {"start_line": 21, "end_line": 21, "quote": "- [[example]] — 완료 항목\n"},
    "references": [{"path": "20-knowledge/example.md", "expected_sha256": "CURRENT_REFERENCE_SHA256"}]
  }]
}
```

행은 LF 기준 1부터 시작하며 CRLF·마지막 개행·공백도 원문대로 보존한다.
`completed`만 정리 대상으로 선택되고 `active`·`pending`은 남는다. 원문·목적지·참조가
바뀌었거나 행이 겹치면 `incomplete`로 표시하고 projected 문서를 반환하지 않는다.
완전한 계획은 `status=preview`, `apply_supported=false`이며 `projected_index`와
`archive_target`에서 삭제 예정 행과 목적지에 덧붙일 원문을 검토한다.
이 `archive_target`은 읽기 금지 아카이브를 탐색한다는 뜻이 아니다.

원문 행과 기존 목적지 연결을 보존하는 계획이다. 완료 상태는 작성자 선언이며 자동
추론하지 않는다. 원문 요약·파일 이동·폴더 검색·실제 적용 명령은 제공하지 않는다.
미리보기 뒤 다른 파일이 변경되면 같은 입력을 다시 검증해야 한다.

## 요구별 검증 누락 확인

별도 manifest는 아래 형식이다. `task`는 선택한 기존 evidence 영수증의 작업과 같아야 한다.
검사 ID는 제출된 보고서와 별개로 먼저 정한다. 요구·인수 조건의 본문은 이 도구가
판정하지 않으며, 의미 판단과 실제 검사는 호스트/검증자가 수행한다.

```json
{
  "version": 1,
  "task": "example-task",
  "requirements": ["REQ-1"],
  "acceptance": [{"id": "AC-1", "requirements": ["REQ-1"], "checks": ["check-1"]}],
  "required_checks": ["check-1"]
}
```

```text
python skills/agentic-vault/scripts/vault_coverage.py --vault PATH check --manifest 00-meta/coverage.json --evidence RECEIPT_ID
```

evidence의 snapshot/record 절차는 [기존 근거 안내](evidence.md)를 따른다.
검사 누락은 미실행 또는 미제출로 표시하며 둘을 구분했다고 주장하지 않는다.
`gap`·`failed`·`regression`과 변경된 산출물/보고서/근거를 별도로 진단한다.
연결만 있거나 현재 해시가 같다는 이유로 요구 충족·실행·검증자 신원을 인증하지 않는다.
전체 상태가 완전하더라도 명시한 선언과 관찰의 데이터 완전성을 뜻한다.

설치된 플러그인은 위 상대 경로 대신 해당 플러그인의 실제 설치 경로를 사용한다.
세 도구의 stdout과 종료 코드를 함께 확인한다. 제한·오류가 있는 보고서를 통과로
간주하지 않으며 사용자 권한과 실제 파일 소유권은 별도로 확인한다.
