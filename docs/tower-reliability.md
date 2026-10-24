# 교훈 검증과 안전한 작업 복구

Tower 소스 비교에서 확인한 설계 중 파일 기반 플러그인에 맞는 다섯 항목을 보강한다.
이 문서는 v0.20.0부터 제공하는 선택형 도구를 설명한다. 구판 설치본을 사용한다면
현재 버전과 해당 스크립트의 파일 존재를 먼저 확인한다.

## 교훈을 비교할 때 무엇을 고정하는가

새 교훈의 후보 내용과 그 교훈을 실행하는 지시문은 바뀌는 대상이다. 시험 문제와
채점 기준, 모델/제공자, 토큰 예산, 도구 계약, 관련 설정과 반복 횟수는 비교 조건이다.
두 내용을 같은 시험으로 반복 확인하고, 실행 당시의 파일과 조건을 기록한다.
후보 버전을 시험 묶음 식별값에 섞지 않아 버전이 달라도 같은 시험으로 비교할 수 있다.

`vault_runs.py`는 호스트가 관찰한 시험 결과와 근거를 묶는 선택형 CLI다. 임의 명령을
실행하거나 모델을 자동 호출하지 않는다. 원문·조건이 바뀌면 기록의 현재성을 다시
확인해야 한다. 보고서의 일치와 보존은 관찰의 진실이나 실제 업무 효과의 인증이 아니다.
금지 행동의 실패를 평균 점수로 덮지 않으며, 비교 결과가 교훈 적용 권한을 만들지 않는다.

### 명시적 실행 순서

아래 경로는 이미 작성한 예제 자료를 가리킨다. 원래 후보/지시문과 개선 후보/지시문을
각각 별도 파일로 보존한다. 같은 파일을 덮어쓰면 옛 실행의 현재성 검사가 실패한다.
`--source`는 평가 대상 지시문을 선택하며 여러 번 지정할 수 있다. 모델·제공자·예산과
fixture/tools 파일은 두 실행에서 같은 조건으로 유지한다.

```text
python skills/agentic-vault/scripts/vault_runs.py --vault PATH prepare --candidate 50-projects/demo/baseline.md --source 50-projects/demo/baseline-prompt.md --fixture 50-projects/demo/input/fixture.json --tools 50-projects/demo/input/tools.json --model MODEL --provider PROVIDER --token-budget 2048 --candidate-version v1
python skills/agentic-vault/scripts/vault_runs.py --vault PATH prepare --candidate 50-projects/demo/candidate.md --source 50-projects/demo/candidate-prompt.md --fixture 50-projects/demo/input/fixture.json --tools 50-projects/demo/input/tools.json --model MODEL --provider PROVIDER --token-budget 2048 --candidate-version v2
```

`prepare`의 64자리 `id`와 `manifest_sha256`을 각 호스트 관찰 보고서에 넣는다.
호스트가 실제 시험을 수행하고 근거 파일을 저장한 뒤에 기록한다. CLI가 지시문을
실행하거나 관찰 값을 만들어 주는 절차는 아니다.

```text
python skills/agentic-vault/scripts/vault_runs.py --vault PATH record BASELINE_ID --report 50-projects/demo/input/baseline-report.json
python skills/agentic-vault/scripts/vault_runs.py --vault PATH record CANDIDATE_ID --report 50-projects/demo/input/candidate-report.json
python skills/agentic-vault/scripts/vault_runs.py --vault PATH inspect CANDIDATE_ID
python skills/agentic-vault/scripts/vault_runs.py --vault PATH compare BASELINE_ID CANDIDATE_ID
```

fixture 최상위는 `version: 1`, `repetitions: 2..10`, `cases`다. 최소 5개 사례가
`representative`, `authority`, `ambiguity`, `tool_failure`, `injection` 범주를 모두
포함해야 한다. 사례에는 `id`, `category`, `prompt`, `assertions`를 넣고, 각 assertion은
`id`, `description`, boolean `hard`다. 선택 키 `role`은 아래 '사례별 뒤집힘 표와 정정·유지
역할'에서 설명한다. 사례마다 hard assertion이 최소 하나 있어야 한다.
tools 최상위는 `version: 1`, `tools`이며 각 도구에는 `name`, 객체 `input_schema`,
객체 `output_schema`를 넣는다. 이름뿐 아니라 계약 전체가 비교 조건으로 묶인다.

호스트 보고서는 `version: 1`, `run_id`, `manifest_sha256`, `outcomes`의 정확한 필드를
사용한다. 각 outcome은 `case_id`, 1부터 시작하는 `repetition`, `status: complete|error`,
`assertions: [{id, passed: boolean}]`, `evidence: [볼트 상대경로]`다. 모든 사례/반복과
모든 assertion을 한 번씩 기록하고 각 outcome에 실제 근거 파일을 적어야 한다.
누락·중복·달라진 근거·오류 결과를 통과로 채워 넣지 않는다.

생성/조회/비교가 정상 수행되면 종료 코드 0이지만 `eligible`, `reasons`, 점수 차이와
`authority: advisory_host_observations_only`를 함께 읽는다. 성공 종료는 교훈의 효과나
적용 승인을 뜻하지 않는다. 잘못된/오래된/비교 불가능한 입력은 종료 코드 2다.
`eligible`은 비교된 관찰 쌍에 hard 실패나 error가 없다는 의미다. 개선 후보를
추천하거나 성능 퇴행이 없다는 뜻이 아니다. 음수 `score_delta`는 퇴행으로 읽고
적용 결정을 보류한다. 원래 실행에 hard 실패가 있었다면 개선 후보가 통과해도 이
쌍의 eligible은 false이며, 두 점수와 실패 이유는 함께 남는다.
JSON/영수증은 64KiB, 일반 source는 256KiB, 근거 하나는 1MiB, source/근거 묶음은
2MiB까지다. 최대 16사례·사례당 8assertion·64파일이며 `.env`와 비밀 형태는 거절한다.

v0.30.0의 선택형 `--evaluator-source`·`--evaluator-config`는 채점기 스냅샷도 비교 조건에
묶는다. 별도 `vault_run_costs.py`는 호출자가 보고한 개선 전체 과정의 비용과 미확인 항목을
구별한다. 기존 실행에는 자동으로 추가되지 않는다. JSON·CLI·신뢰 한계는
[평가기와 비용 근거](improvement-accounting.md)를 따른다.

### 사례별 뒤집힘 표와 정정·유지 역할 (선택)

`compare` 점수는 모든 assertion을 같은 무게로 평균한다. 그래서 다른 사례가 좋아진 몫이
soft assertion 하나의 퇴행을 가릴 수 있다(양의 `score_delta`, `eligible: true`). 선택형
`flips`는 두 완료 실행을 (사례, assertion)마다 맞대어, 개선 전에 통과하던 검사가 개선
뒤에 실패한 경우를 따로 센다.

```text
python skills/agentic-vault/scripts/vault_runs.py --vault PATH flips BASELINE_ID CANDIDATE_ID
```

- 읽기 전용이다. 영수증·잠금 파일을 만들지 않는다. `compare`와 같은 현재성 검사로 두
  실행을 다시 읽고, 비교 조건이 다르면 `incompatible_execution_contracts`, 미완료면
  `run_not_complete`로 거절한다(종료 코드 2).
- `compare` 결과·`comparison_id`·비교 영수증, `vault_improvements`의 결정,
  `vault_run_costs`가 넣는 `performance`는 그대로다. 이 표는 그 어디에도 더해지지 않는다.

**세는 법**

- (case_id, assertion_id)마다 실행별 반복 R회 가운데 `status: complete`인 반복 수
  (`baseline_complete`·`candidate_complete`)와 그중 통과 수(`baseline_passes`·
  `candidate_passes`)를 센다. error 반복은 실행별 `error_outcomes`로 따로 세고, 그 반복에
  적힌 assertion 값은 읽지 않는다. 실패로 세지 않는다.
- 전부/전무 규칙: R회 모두 완료·통과면 그 실행에서 통과, 모두 완료·실패면 실패다. 두
  실행에서 모두 정해진 항목만 `flip_counts`의 `pass_to_pass`·`pass_to_fail`·
  `fail_to_pass`·`fail_to_fail`에 넣는다. 일부만 통과하면 `mixed`, error 반복이 있으면
  `incomplete`다. 여섯 값의 합은 맞댄 전체 항목 수 `item_count`다. 항목별 값은 `items`에 있다.
- `pass_count_drops`: 통과 수가 줄어든 항목(`candidate_passes < baseline_passes`)이다.
  error 반복이 있으면 완료 반복 대비 통과 비율로 비교한다. 어느 한쪽 실행에서 완료 반복이
  0이면 비교할 관찰이 없어 목록에 넣지 않는다(그 항목은 `incomplete`다). 전부/전무 규칙은
  과소 집계하고 이 목록은 한 번의 흔들림까지 잡아 과대 집계하므로 둘을 함께 읽는다.
- `negative_flip_rate` = `pass_to_fail` / `item_count`다. MUSCLE의 NFR처럼 분모는 전체 항목
  수다. 기준안이 통과한 항목 대비 비율은 `conditional_negative_flip_rate`라는 다른 이름으로만
  낸다(기준안 통과 항목이 없으면 `null`).
- 반복을 묶는 규칙은 논문에 없는 agentic-vault 확장이다(`repetition_rule`). 비결정적 실행의
  반복 번호는 짝이 아니므로 반복끼리 짝지어 세지 않는다.

**정정·유지 역할**

fixture의 assertion에 선택 키 `role`을 넣을 수 있다.

```json
{"id": "date-format", "description": "Uses the requested date format", "hard": false, "role": "correction"}
```

- `correction`: 개선이 고치려는 검사다(SWE-bench FAIL_TO_PASS에 해당). 기준안에서 실패하고
  후보에서 통과해야 한다. soft(`hard: false`)에만 붙인다. hard에 붙이면
  `hard_correction_assertion`으로 거절한다. hard 거부권과 기준안 부적합 판정을 약하게 만들지
  않기 위해서다.
- `retention`: 개선 전후 모두 통과해야 하는 검사다(PASS_TO_PASS에 해당). hard·soft 모두 된다.
- 다른 값·`null`·빈 문자열은 `invalid_assertion_role`이다. 키가 없는 fixture는 이전과 바이트가
  같아 manifest 해시와 run ID가 그대로다. `role`을 더하거나 바꾸면 다른 fixture가 되어 이전
  실행과 비교할 수 없다. `role`이 있는 fixture는 v0.30.0 이하 플러그인이 거절한다.
- `role`은 `compare`의 점수·`eligible`·개선 결정에 쓰이지 않는다.

`role`이 하나라도 있으면 `flips` 결과의 `roles`에 다음을 담는다(없으면 `null`).

| 필드 | 의미 |
|---|---|
| `baseline`·`candidate` | 실행별 `correction_pass_rate`·`retention_pass_rate`. 전부/전무 규칙으로 통과한 항목의 비율이다. 빈 목록은 1로 본다(SWE-bench와 같음) |
| `resolution` | 후보 실행 기준. correction·retention이 모두 통과면 `FULL`, correction 일부와 retention 전부면 `PARTIAL`, 나머지는 `NO` |
| `retention_break_count` | `pass_count_drops`에 오른 retention 항목 수 |
| `invalid_correction_tests` | 기준안의 완료 반복에서 한 번이라도 통과한 correction. 고친 것을 시험하지 못하므로 표시만 하고 등급에서 빼지는 않는다 |
| `correction_count`·`retention_count`·`unlabelled_count` | 역할별 항목 수. 역할이 없는 항목은 등급에 쓰지 않는다 |

`resolution`은 후보 실행만 채점한다. 후보 실행에서 역할이 있는 항목에 error 반복이 있으면 그
항목은 통과로 세지 않으므로 `FULL`이 될 수 없다. 이때 `NO`나 `PARTIAL`은 퇴행이 아니라 관찰
부족일 수 있으니 `error_outcomes`와 `incomplete` 항목을 함께 본다. 기준안 실행의 error는
`resolution`을 바꾸지 않는다. 그 항목은 `incomplete`가 되고 기준안의 역할별 통과율에서 통과로
세지 않는다. 기준안 반복이 모두 error인 correction은 `invalid_correction_tests`에 오르지
않으므로, 후보가 `FULL`이어도 그 correction이 기준안에서 실패했다는 관찰은 없다.

반복 2~10회·최대 16사례×8assertion이라 모든 수는 서술 값이다. p값은 내지 않으며, 한 반복의
뒤집힘은 잡음일 수 있다. 관찰은 호스트가 보고한 값이다. correction 검사가 기준안에서 올바른
이유로 실패했는지는 확인하지 않는다. 등급은 검토 자료이고 적용 승인이 아니다.

출처: [MUSCLE](https://arxiv.org/abs/2407.09435) §3.1의 음의 뒤집힘(NF)과 NFR 정의(분모는 전체
항목 수), [SWE-bench 데이터셋 카드](https://huggingface.co/datasets/princeton-nlp/SWE-bench)의
FAIL_TO_PASS·PASS_TO_PASS 정의, [SWE-bench](https://github.com/SWE-bench/SWE-bench) 채점 코드
`swebench/harness/grading.py`의 `get_resolution_status`(FULL·PARTIAL·NO 규칙)를 참고했다. 지표
정의와 필드 의미만 빌렸고 코드·데이터·문장은 가져오지 않았다.

## 적용한 교훈을 되돌릴 때

기존 `vault_proposals.py`의 원문과 적용 기록을 재사용한다. 적용 이후의 내용이 그대로
남은 단일 대상 파일을 먼저 미리보기로 확인한 뒤, 이미 승인한 정확한 복구만 명시적으로
실행한다. 다른 사람이 바꾼 내용, 달라진 설정/경로 정책 또는 오래된 미리보기는 거절한다.
원래 줄바꿈과 Unicode 바이트를 보존하며 적용·복구 이력을 지우지 않는다.

이는 자동 롤백이나 여러 파일의 일괄 트랜잭션이 아니다. 공유 handoff/tasks의
부분 수정 규칙은 계속 적용된다. 해당 파일 전체를 바꾸는 후보를 이 도구에 맡기지 않는다.
협조 잠금은 외부 편집기를 OS 수준에서 잠그지 않는다.

```text
python skills/agentic-vault/scripts/vault_proposals.py --vault PATH rollback-preview ID
python skills/agentic-vault/scripts/vault_proposals.py --vault PATH rollback ID --approve --preview-hash PREVIEW_SHA256
```

`PREVIEW_SHA256`은 미리보기의 `preview_sha256`이다. 미리보기는 읽기 전용이다.
대상·영수증·설정의 현재 바이트가 같아야 명시적 복구가 가능하며, 중간에 끊긴 복구는
같은 승인/미리보기로 원문 또는 후보 바이트가 정확히 확인될 때만 회계를 마무리한다.
구판 영수증도 현재 미리보기의 정책에 묶지만, 당시 기록하지 않은 적용 시점의 설정을
소급 인증하지 않는다. `rolled_back` 뒤 원래 apply를 다시 실행해 후보를 부활시키지 않는다.

## 외부 작업의 결과를 모를 때

`vault_operations.py`의 명시적 시작 기록을 먼저 남기고, 호출자가 작업을 수행한 뒤
성공 결과의 제한된 메타데이터를 저장한다. 완료된 기록은 다시 실행하는 대신 조회한다.
외부 작업이 끝났는지 알 수 없거나 결과 저장이 끊겼으면 불확실 상태로 보류한다.
실제로 확인한 후에만 별도의 조정 절차로 기록을 정리한다.

이 기록을 호출한 플러그인/호스트 도우미에만 적용되는 계약이다. 모든 Claude/Codex
도구를 가로채거나 원격 서비스의 정확히 한 번 실행을 보장하지 않는다. 실행 기록이나
소유 토큰도 외부 작업의 승인, API 권한 또는 성공의 독립 증거가 아니다. 새로 승인된
의도는 다른 작업 식별값으로 구분한다. 자격증명과 원문 응답을 기록에 넣지 않는다.

```text
python skills/agentic-vault/scripts/vault_operations.py --vault PATH begin --input 50-projects/demo/input/begin.json
python skills/agentic-vault/scripts/vault_operations.py --vault PATH finish --input 50-projects/demo/input/finish.json
python skills/agentic-vault/scripts/vault_operations.py --vault PATH mark-uncertain --input 50-projects/demo/input/uncertain.json
python skills/agentic-vault/scripts/vault_operations.py --vault PATH inspect --id OPERATION_ID
python skills/agentic-vault/scripts/vault_operations.py --vault PATH reconcile --input 50-projects/demo/input/reconcile.json
```

begin 입력은 `intent`, `action`, `parameters`와 선택 `ttl_seconds`(기본 300),
`wait_seconds`(기본 2)다. 같은 intent/action의 다른 parameters는 충돌이며 의도와
parameters 본문은 영수증에 저장하지 않는다. 결과의 `disposition: claimed`와
소유 토큰이 함께 있을 때만, TTL과 기존 외부 작업 승인 범위 안에서 호출자가 진행한다.
종료 코드 0만 보고 실행하면 안 된다. `blocked`, `reused`, `closed`는 새 실행을 허용하지 않는다.

finish는 `id`, `owner_token`, `result`, mark-uncertain은 `id`, `owner_token`을 받는다.
result는 제한된 메타데이터만 허용한다: `http_status`, `response_bytes`, `effect_count`,
`remote_id_sha256`, `evidence_sha256`, boolean `verified`. verified는 호출자의 관찰
표시이고 인증서가 아니다. 소유 토큰은 첫 claimed 응답에만 반환하고 조회에서는 숨긴다.
만료·설정 변경·다른 소유자는 finish를 완료시키지 못한다.

reconcile은 `id`, 현재 영수증의 `expected_sha256`, `outcome: succeeded|not-executed`와
필요한 result를 받는다. 실제로 확인한 후에만 명시적으로 호출한다. not-executed로
정리한 의도는 닫힌 상태로 유지하며 재실행을 허용하지 않는다. 새 승인된 작업은 새
의도로 시작한다. 입력은 32KiB, parameters는 16KiB, 영수증은 16KiB, result는 1KiB까지다.
원문 외부 응답·자격증명·자의적인 오류 문구는 받아 기록하지 않는다.

기존 협조 잠금에 의존하므로 Windows에서 요청이 몰리면 경로/잠금 검사에 의해
`operation_unavailable` 또는 `lock_busy`로 거절될 수 있다. 이때 외부 작업을 실행하지
않고 현재 영수증을 확인한다. 시험에서는 중복 실행이 차단됐지만 잠금 파일이 남아
뒤 요청을 막는 사례도 있었다. 잠금 파일을 임의 삭제해 재실행 권한을 만들지 않는다.
고부하 가용성이나 모든 호스트의 잠금 복구를 보장하는 도구는 아니다.

## 연결 진단의 단계

`vault_connectors.py`는 설치·설정·자격증명 가용성·인증 시험·실제 연산 시험을 나누어
보고한다. 기본 진단은 오프라인이고 읽기 전용이다. 환경변수의 값이나 `.env`를
읽어 출력하지 않는다. 키가 있다는 사실만으로 인증 성공이나 정상 운용으로 표시하지 않는다.

지원하는 명시적 probe만 기존 승인 범위에서 수행한다. 시험하지 않았거나 지원하지 않는
단계는 미확인으로 남긴다. Jarvis를 시작하거나 Telegram 메시지를 보내지 않으며,
구독 로그인만으로 bare-Claude의 API 인증 요건을 충족했다고 추정하지 않는다.

```text
python skills/agentic-vault/scripts/vault_connectors.py --vault PATH --format json
python skills/agentic-vault/scripts/vault_connectors.py --vault PATH --probe jev --timeout 5 --format json
```

두 번째 명령은 승인된 전송 범위에서만 사용한다. 공개 합성 질문 한 건을 고정된 Jev
연결로 보내며 원문 노트·개인 자료를 전송하지 않는다. 한 번의 요청만 수행하고 재시도·
redirect는 허용하지 않는다. 인증/연산 단계에는 `checked`도 표시한다. 연결 거절은
failed, 응답 유실·timeout·rate-limit·잘못된 응답은 인증을 unknown으로 남긴다.
Jarvis live probe는 지원하지 않으며 `--probe jarvis`는 미확인 상태와 종료 코드 1을
반환한다. 아무 probe도 하지 않은 기본 진단의 종료 코드 0은 ‘진단 완료’라는 뜻이다.
어댑터 파일이 빠졌거나 안전하게 읽고 불러올 수 없으면 해당 연결의 설치 단계는
missing/unknown으로 남기고 의존하는 probe를 취소한다. 다른 연결의 진단은 계속한다.
Jarvis가 설정되어 있어도 설정 해석 모듈을 불러올 수 없으면 configured는 unknown이다.

## 기존 도구와 연결

기존 출처 검증은 `vault_evidence.py`, 교훈 후보는 `vault_proposals.py`, 효과 관측은
`vault_lesson_metrics.py`, 공유 부분 편집은 `vault_state.py`를 계속 사용한다. 새 기록은
지식 회상 본문에 자동 편입하지 않는다. 세션 시작·compact·종료 훅이나 기본 예산에
새 모델/네트워크 호출을 추가하지 않는다.

## 설계 출처와 적용 범위

Tower `76268c2969f20ccbea66d6582206c26e81ba7983`의
[반복 행동 평가](https://github.com/moatai-io/towersource/blob/76268c2969f20ccbea66d6582206c26e81ba7983/packages/backend/services/harness/harness-eval-runner.ts),
[실행 대장](https://github.com/moatai-io/towersource/blob/76268c2969f20ccbea66d6582206c26e81ba7983/packages/backend/services/agent-turn/side-effect-tool-wrapper.ts),
[복구 미리보기](https://github.com/moatai-io/towersource/blob/76268c2969f20ccbea66d6582206c26e81ba7983/packages/backend/services/agent-turn/file-checkpoint.ts),
[연결 진단](https://github.com/moatai-io/towersource/blob/76268c2969f20ccbea66d6582206c26e81ba7983/packages/backend/scripts/provider-doctor.ts)
설계를 참고했다. Tower 코드와 의존성을 복사하지 않고 기존 stdlib 엔진에 맞춰 구현한다.
기존 vault 경로 정책·deny zone·링크/정션/하드링크 검사·동시 편집 계약을 유지한다.
Tower의 전사 웹 플랫폼, PostgreSQL, credential vault, 지속 실행 감독기는 추가하지 않는다.
