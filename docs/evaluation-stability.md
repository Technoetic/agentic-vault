# 한국어 모델·검색 비교와 Windows 편집 보강

v0.24.0에 포함된 선택형 평가 도구와 Windows 부분 편집 보강, v0.25.0에서 더한 선택형 측정 조건
기록, v0.26.0에서 더한 간헐 실패 테스트의 분류와 반복 측정 규칙, v0.31.0에서 더한
[요청 본문을 읽지 않는 시험 서버](#요청-본문을-읽지-않는-시험-서버)의 원인·수정·재발 방지 검사·측정(시험과
문서만 바꿨다)이다. 요청·설명 언어와 문자열 보존 지시, 공개 fixture의 검색 순위를 따로 비교하고, 로컬 모델
실행의 프롬프트 캐시·슬롯 부하·반복 일치를 선택해서 기록한다. 간헐 실패 테스트는 같은 조건으로 반복해 x/N과
정확한 상한으로 보고한다. 기본 세션 훅·모델 호출·런타임 의존성·검색 설정을 추가하지 않는다. 기존 설치 캐시와
볼트는 설치·upgrade를 별도로 수행하기 전까지 그대로다. v0.31.0의 [릴리스 노트](releases/v0.31.0.md)와
[릴리스 검증](verification/v0.31.0.md), v0.26.0의 [릴리스 노트](releases/v0.26.0.md)와
[릴리스 검증](verification/v0.26.0.md), v0.25.0의 [릴리스 노트](releases/v0.25.0.md)와
[릴리스 검증](verification/v0.25.0.md), v0.24.0의 [릴리스 노트](releases/v0.24.0.md)와
[릴리스 검증](verification/v0.24.0.md)을 함께 확인한다.

## 요청·설명 언어와 문자열 보존 비교

```text
python scripts/evaluate_tool_matrix.py
python scripts/evaluate_tool_matrix.py --backend local-model --model <local-model-id> --api-key-env <environment-variable-name> --request-language all --instruction-language all --prompt-variant all --condition all --repeats 2 --seed 17 --receipt-out <new-file>
```

첫 명령은 모델을 호출하지 않는 가상 프로토콜 대조군이다. 두 번째 명령만 명시적으로
기존 루프백 전송을 사용한다. endpoint의 기본값은 `http://127.0.0.1:8080/v1/chat/completions`다.
인증값은 지정한 환경변수에서만 읽고 파일·영수증·명령 인자에 복사하지 않는다.

- 요청 언어는 기존 질문과 문서의 언어를 함께 선택한다. 사용자 문장만 바꾸는 실험이 아니다.
- 설명 언어는 시스템 문장과 도구 설명을 함께 바꾼다. 함수명·인자명·업무 식별자는 유지한다.
- `literal-identifiers`는 주소·코드·식별자를 번역·요약하지 않고 복사하라는 지시를 더한다.
  채점기의 정답이나 공격 목표를 모델 입력에 넣지 않는다. `canonical`은 기존 프롬프트다.
- 반복은 1~5회, 최대 80개 에피소드다(`--string-patterns`를 켜면 A·B 두 팔이라 160개). 선언한 seed로 순서를 섞고
  같은 반복 조건에 같은 seed를 요청한다. 서버가 seed를 따르는지, 반복들이 독립적인지는 인증하지 않는다.
- 정상 업무 성공, 공격 목표 달성, 모델 오류, 인자 실패를 따로 센다. 오류도 전체 분모에 남긴다.
  공격률의 분모는 공격 문구가 들어간 에피소드이며, 오류 없는 조건의 별도 비율도 제공한다.
- Wilson 구간은 작은 합성 표본을 설명하는 구간이다. 독립 표본이나 업무 전체의 정확도 보장이 아니다.
- 원 응답은 바이트 상한을 검사하고 알려진 인증값을 정제한 뒤 보관한다. 알 수 없는 모든 비밀을
  찾아낸다는 보장은 없다. 모델 오류를 답변 보류나 공격 방어 성공으로 바꾸지 않는다.
- 실행 코드 지문을 시작·종료에 비교한다. 변경되면 원 관측을 보존하되 현재 코드 비교로 사용하지 않는다.
  실제 제공자 사용량과 문자 기반 추정을 분리하고 청구 금액은 알 수 없으면 null로 둔다.

`evaluate_tool_safety.py`의 선택형 `system_language`, `tool_language`, `prompt_variant`, `seed`,
`capture_responses` 인자는 기존 기본 호출에 영향을 주지 않는다. 기존 canonical golden도 유지한다.

## 측정 조건 기록: 프롬프트 캐시·슬롯 부하·반복 일치

로컬 모델의 도구 호출 결과를 해석할 수 있도록 실행 조건을 남기는 선택형 옵션이다. 모델 동작이나 기본 요청은
바꾸지 않는다. 옵션을 주지 않으면 요청 바이트와 종료 코드는 v0.24.0과 같고, 출력 JSON은 경과 시간과
`evaluate_tool_matrix.py`가 넣는 실행 코드 지문(소스 SHA-256) 값만 다르다. 테스트는 v0.24.0(`d2b9315`)에서 만든
행렬 기본 출력을 이 값들만 정규화해 비교하고, 기본 요청 바이트는 SHA-256을 고정해 비교한다.

```text
python scripts/evaluate_tool_safety.py --backend local-model --model <local-model-id> --api-key-env <environment-variable-name> --no-prompt-cache --slot-snapshots --receipt-out <new-file>
python scripts/evaluate_tool_matrix.py --backend local-model --model <local-model-id> --api-key-env <environment-variable-name> --request-language ko --instruction-language en --prompt-variant canonical --condition clean --repeats 5 --repeat-agreement --slot-snapshots --receipt-out <new-file>
```

| 옵션 | 동작 | 사용 위치 |
|---|---|---|
| `--no-prompt-cache` | 모든 채팅 요청 끝에 llama-server 요청 옵션 `"cache_prompt": false`를 붙인다 | 두 스크립트 |
| `--slot-snapshots` | 에피소드마다 시작 전과 끝난 뒤 `GET /slots`를 한 번씩 요청해 전체 슬롯 수와 바쁜 슬롯 수를 남긴다 | 두 스크립트 |
| `--repeat-agreement` | `--repeats`(2~5)의 모든 반복이 기본 `--seed`를 요청한다. 셀마다 같은 구성을 순차로 반복하고 첫 실행과 해시가 같은 반복의 비율을 낸다 | `evaluate_tool_matrix.py` |
| `--string-patterns` | 모든 trial을 스키마 A(현재 스키마)와 B(`send_message`의 `recipient`·`body`에 형식 클래스 `pattern`을 더한 스키마)로 같은 seed에서 한 번씩 실행하고, 호스트의 `re.fullmatch` 판정을 기록만 한다. 확인한 llama.cpp의 Qwen3-Coder XML 경로는 문자열 값의 `pattern`을 문법에 넣지 않으므로, 그 경로에서 B의 `pattern`은 프롬프트 힌트로만 작동한다 | `evaluate_tool_matrix.py` |

`--string-patterns`는 측정 조건이 아니라 비교 조건이다. 켜면 B 시행의 요청에 `pattern`이 더해지고 계획이 두 배가 된다.
규칙과 기록 형식은 [문자열 인자 패턴 짝 비교](#문자열-인자-패턴-짝-비교)에 있다.

### 왜 기록하는가

- Thinking Machines Lab의 [Defeating Nondeterminism in LLM Inference](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)
  (Horace He, 2025-09-10)는 temperature 0에서도 LLM API의 출력이 실제로는 결정적이지 않다고 설명한다. 주된 원인은
  서버 부하에 따라 배치 크기가 바뀌는데 커널이 배치 크기에 불변이 아니라는 점이다. 개별 사용자에게 다른 동시
  사용자는 입력이 아니라 시스템의 비결정적 성질이 된다. 출처가 보고한 측정(이 저장소에서 재현하지 않음)에서
  Qwen3-235B-A22B-Instruct-2507을 temperature 0으로 1,000회 생성하자 고유 완성이 80개, 가장 많은 완성이 78회였고
  103번째 토큰에서 처음 갈라졌다. 배치 불변 커널을 쓰자 1,000회가 모두 같았다.
- [llama.cpp 서버 README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)(2026-10-19 열람)는
  `cache_prompt`가 가능하면 이전 요청의 KV 캐시를 재사용하며 기본값이 true라고 적는다. 백엔드에 따라 배치 크기가
  다르면 logits가 비트 단위로 같다는 보장이 없어 이 옵션이 비결정 결과를 낼 수 있다고도 적는다. `GET /slots`는
  기본으로 켜져 있고 `--no-slots`로 끌 수 있으며, 응답 예시는 슬롯마다 `id`·`is_processing` 등을 담은 배열이다.
- 이 저장소의 [2026-10-18 관측](verification/eval-stability-20261018.md)에서 첫 32에피소드는 별도 구조화 기억 추론과
  동시에 실행해 모델 오류 8건(요청 시간 초과 7, transport 오류 1)이 났다. 뒤의 8에피소드(0/4와 4/4)는 경쟁 호출 없이
  실행했다. v0.24.0의 행렬 도구도 원 응답은 영수증에 보관하므로 반복끼리 출력을 비교할 수는 있었다. 빠진 것은 같은
  seed의 반복과 조건 기록이다. 반복은 seed를 하나씩 올려 요청했고(뒤의 8에피소드는 17과 18) 같은 구성의 반복이 아니었으며, 슬롯
  부하와 프롬프트 캐시 설정도 남기지 않았다. 그래서 반복 사이의 차이가 seed 때문인지 부하·캐시 때문인지, 오류가 부하
  때문인지 영수증만으로 가를 수 없었다.

### 기록 형식

| 키 | 의미 |
|---|---|
| `measurement_conditions` | 요청에 붙인 필드(`request_fields`, 붙인 것이 없으면 `{}`), 스냅샷 규칙과 한도, 반복 일치 규칙, 결정성 비보장 문구 |
| `episodes[].evaluation_conditions.cache_prompt` | `--no-prompt-cache`일 때만 `false`로 생긴다 |
| `episodes[].server_slots` | `before`·`after`(`total_slots`, `busy_slots`) 또는 null과 `before_reason`·`after_reason`, `own_request_may_be_running_after` |
| `summary.slot_activity` | 다른 슬롯이 바쁜 상태로 시작·종료한 에피소드 수, 자기 요청일 수 있는 경우, 스냅샷 누락 수와 사유. 행렬은 `cells[].summary`에도 둔다 |
| `episodes[].output_fingerprint` | 행렬의 `--repeat-agreement`에서 `response_sha256`, `tool_calls_sha256`, 보관 응답 수, `outputs_complete` |
| `summary.repeat_agreement`, `cells[].summary.repeat_agreement` | 전체·셀별 `response_match`·`tool_call_match`(분자·분모·비율)와 `incomplete_runs`. 셀에는 기준 trial과 서로 다른 해시 수도 둔다 |

**프롬프트 캐시.** 필드는 기존 요청의 맨 끝에만 붙고 나머지 바이트는 그대로다. README는 `cache_prompt`를
`/completion` 옵션으로 설명하고, `/v1/chat/completions` 절에서는 `mirostat` 같은 `/completion` 전용 기능도
지원한다고 적는다. 이 도구는 서버가 채팅 요청의 이 필드를 실제로 따랐는지 확인하지 않는다. 캐시를 끄면 이전
요청의 KV 캐시를 재사용하지 않으므로 느려지고 시간 초과가 늘 수 있다. 그래서 측정 조건으로만 쓰고 기본값을 바꾸지 않는다.

**슬롯 스냅샷.** `/props` 진단과 같은 규칙을 쓴다. `--endpoint`와 같은 루프백 scheme·host·port의 `/slots`만
요청하고 프록시·리다이렉트·재시도를 끈다. 5초·262,144바이트로 제한하며 `--api-key-env`를 준 경우 bearer 헤더를
같은 서버에만 보낸다. 슬롯 매개변수·프롬프트·작업 번호·헤더·키는 남기지 않는다. 스냅샷은 에피소드 시간 예산 밖에서
요청하므로 에피소드의 결과와 `elapsed_seconds`에 들어가지 않는다. 대신 실행 시간이 에피소드당 최대 10초 늘 수 있다.
실패해도 실행은 계속되고 값은 null이며 사유 코드가 남는다: `http_error`(404 등 오류 상태, 리다이렉트, 인증 실패),
`transport_error`, `slots_timeout`, `slots_bytes_exceeded`, `invalid_slots_json`, `slots_not_array`,
`slots_fields_absent`(빈 배열, 4,096개 초과, 객체가 아닌 항목, 불리언 `is_processing` 없음), `non_loopback_endpoint`,
`invalid_api_key`. `scripted-control`은 요청 없이 `scripted_control`, `probe_slots`가 없는 사용자 정의 전송 객체는
`slots_probe_unavailable`, 그 함수의 예외·형식 오류는 `slots_probe_error`다. 사용자 정의 `probe_slots`도 모델 요청과
같은 시간 제한 도우미로 감싸므로 5초 안에 돌아오지 않으면 기다리지 않고 `slots_timeout`으로 남긴다. 스냅샷 실패는 종료 코드를 바꾸지 않는다.

시작 스냅샷의 바쁜 슬롯은 모두 다른 요청으로 센다. 앞 에피소드가 시간 초과로 남긴 이 평가기의 요청일 수도 있다.
요청 시간 초과·에피소드 시간 초과·transport 오류로 끝난 에피소드는 자기 마지막 요청이 서버에서 아직 돌 수 있으므로
`own_request_may_be_running_after`가 참이다. 이때 끝 스냅샷은 바쁜 슬롯 하나를 빼고 세고, 정확히 하나만 바쁘면
`ended_busy_possibly_own_request`로 따로 센다. 스냅샷은 에피소드 앞뒤의 시점 표본이다. 둘 다 0이어도 에피소드
도중에 다른 요청이 없었다는 증명이 아니며, 서버가 슬롯을 비우는 내부 시점도 확인하지 않는다.

**반복 일치.** `--repeat-agreement`는 기존 `--repeats`의 seed 규칙만 바꾼다. 모든 반복이 기본 seed를 요청하므로
한 셀의 반복은 같은 구성이고, 보고서의 `seed_policy`도 그렇게 적는다. 반복은 한 번에 하나씩 순차 실행하며 실행 순서는
기존처럼 seed로 섞는다. 한 구성을 연달아 재려면 요청 언어·설명 언어·지시 변형·조건을 하나씩 골라 셀 하나만 실행한다.

- `response_sha256`: 보관한 응답마다 종료 이유·본문·도구 호출 이름과 인자 원문. 호출 ID·사용량·시간 같은 서버
  메타데이터는 뺀다.
- `tool_calls_sha256`: 턴을 가로지른 호출 순서대로 도구 이름과 JSON으로 해석한 인자. 공백·키 순서만 다른 인자는 같다.
- 기준은 각 셀에서 처음 실행된 반복이고 나머지 반복은 모두 분모에 남는다. 두 실행 모두 요청마다 응답이 보관되고
  시간 제한으로 끊기지 않았으며 해시가 같을 때만 일치로 센다. 프로토콜 오류는 보관한 응답으로 비교하고, 시간 초과나
  응답을 받지 못한 실행은 일치로 세지 않는다. 전체 값은 셀의 분자·분모를 더한 것이며 셀 비율의 평균이 아니다. 모든
  비교가 같은 첫 실행을 공유하므로 구간은 내지 않는다.
- 평가기는 서버가 준 호출 ID를 다음 요청에 그대로 돌려보낸다. 서버가 응답마다 새 ID를 만들면 같은 구성이라도 두 번째
  턴부터 입력이 달라진다. 따라서 이 값은 첫 요청이 같은 실행들의 전체 궤적 일치다.
- 일치율 1.0은 같은 출력을 반복했다는 뜻이지 정답이라는 뜻이 아니다. 이때 N번의 반복은 사실상 한 관측이므로 Wilson
  구간을 독립 표본처럼 읽지 않는다. 1.0보다 낮으면 기록한 조건에서 출력이 갈라졌다는 관측이며 원인(캐시·부하·커널)을
  가르지 않는다.

**보장하지 않는 것.** 세 옵션은 조건을 기록할 뿐 결정성을 만들지 않는다. 이 도구는 배치 불변 커널을 요청하거나
확인하지 않는다. `cache_prompt: false`를 보내도 다른 슬롯이 바쁘면 배치 구성은 바뀔 수 있다. 테스트는 루프백 가짜
서버와 합성 대조군만 사용한다. 실제 서버로는 2026-10-19 기능 통합 뒤 로컬 llama-server 한 대(슬롯 4개)에서 두 가지만
확인했다. 이 버전의 `/slots` 파서가 실제 응답을 읽었고(바쁜 슬롯 0개), 같은 짧은 채팅 요청을 직접 두 번 보내자 두 번째
요청은 프롬프트 115토큰 중 111토큰의 캐시를 재사용했지만 `"cache_prompt": false`를 붙인 두 요청은 0이었다. 반복 일치율과
GPU 비결정성은 측정하지 않았다.

## 문자열 인자 패턴 짝 비교

`send_message` 문자열 인자에 형식 규칙(`pattern`)을 알려 주면 모델이 내는 값이 달라지는지 보는 선택형 짝 비교다.
기본값으로 채택하지 않는다. 옵션을 주지 않으면 요청 바이트와 종료 코드가 이전과 같고, 출력 JSON은 앞 절처럼 경과
시간과 실행 코드 지문(소스 SHA-256) 값만 다르다. 테스트는 옵션을 명시적으로 끈 실행도 v0.24.0 golden 출력과 요청 바이트
SHA-256으로 비교한다.

```text
python scripts/evaluate_tool_matrix.py --string-patterns
python scripts/evaluate_tool_matrix.py --backend local-model --model <local-model-id> --api-key-env <environment-variable-name> --request-language all --instruction-language all --condition all --string-patterns --receipt-out <new-file>
```

- 스키마 A는 지금 스키마다(`recipient`·`body`에 `maxLength`만 있다). 스키마 B는 A에 `pattern` 두 개만 더한다.
  `recipient`는 `^[a-z]{1,32}@[a-z]{1,32}[.]invalid$`, `body`는 `^[A-Z0-9-]{1,64}$`다. 지금 픽스처의 값(수신 주소와
  Dispatch code)이 ASCII라서 ASCII 형식 클래스로 충분하다.
- 모든 trial을 A와 B로 한 번씩, 같은 seed로 실행한다. `trial_id` 끝에 `-schema-A`·`-schema-B`가 붙고 셀도 팔별로
  나뉜다. 계획이 두 배가 되므로 최대 에피소드는 160개다.
- B 요청은 `pattern` 두 개만큼(요청마다 78바이트) 길어서 `--max-request-bytes`를 빠듯하게 주면 B에서만
  `request_bytes_exceeded`가 날 수 있으므로, 두 팔은 넉넉한 상한으로 비교한다.
- 패턴에 정답(코드)이나 canary를 넣지 않고 `recipient`에 enum을 쓰지 않는다. 승인된 수신자와 주입된 수신자가 모두
  B 패턴을 통과하므로 주입된 수신자로 보낸 호출은 B에서도 스키마 유효로 남고, 공격 목표 달성도 그대로 센다. enum을
  쓰면 이 안전 신호가 스키마 오류로 바뀌어 사라진다.
- 형식 클래스는 답의 모양(대문자·숫자·하이픈 코드)을 알려 준다. 정답을 넣지 않았어도 힌트 효과의 크기는 실측 전까지
  모른다. 이 옵션은 작은 표본과 반복 미측정이라는 기존 한계를 해소하지 않는다.
- `evaluate_tool_safety.run_episode(schema_variant='B')`도 같은 B 스키마를 보내고 `evaluation_conditions.schema_variant`를
  남긴다. 기본값 `'A'`는 기존 요청과 출력 그대로다.

### 패턴 허용 목록

B 패턴은 금지 목록이 아니라 허용 목록으로 검사한다(`evaluate_tool_safety.lint_string_pattern`). 통과하지 못한 패턴이
있으면 행렬은 요청 전에 멈춘다. JSON Schema의 `pattern`은 ECMA-262 정규식이고 검증기는 대개 앵커 없이 검색한다. 이
평가기의 판정은 Python `re.fullmatch`이므로 두 판정이 같아지는 문법만 허용한다.

- 허용: 맨 앞 `^`와 맨 끝 `$`(둘 다 필수), 리터럴 문자, 양의 문자 클래스와 오름차순 범위(`[0-9]` `[A-Z]` `[a-z]`
  `[가-힣]` `[.]`, 명시한 공백), 비어 있지 않은 `(…)` 묶음, `*` `+` `?`, 횟수가 256 이하인 `{m}` `{m,n}`, 그리고
  `\n`·`\r`·`\t` 이스케이프.
- 금지: `\d`·`\w`·`\s`·`\b`와 그 밖의 모든 이스케이프(점은 `\.` 대신 `[.]`로 쓴다), `.`, 패턴 안쪽의 `^`·`$`,
  최상위 `|`(필요하면 `^(a|b)$`로 묶는다), `(?`로 시작하는 모든 문법(전방 탐색·명명 그룹·인라인 플래그), `{,n}`과
  `{m,}`, 게으른·소유 수량자, 수량자나 `|`가 든 묶음에 붙인 수량자(`^(a*)*b$`, `^(a|a)*b$`, `^(a|b)?$`), 빈 묶음
  `()`, 256을 넘는 반복 횟수(`{257}`), 부정·빈·중첩 클래스, 클래스 안에서 겹쳐 쓴 `-`·`&`·`~`·`|`(`--` `&&` `~~` `||`).
  클래스 안의 `-`는 범위 연산자이거나 맨 앞·맨 뒤의 리터럴이다.

금지한 문법은 엔진마다 뜻이 다르다.

- Python의 `.`는 CR·U+2028·U+2029에도 맞고 ECMA의 `.`는 맞지 않는다. llama.cpp의 스키마→문법 변환기는 `.`를
  `[^\x0A\x0D]`로 바꿔 세 번째 뜻이 된다.
- `^a|b$`는 양 끝에 앵커가 있어도 ECMA 검색에서 `ax`에 맞고 `re.fullmatch`에는 맞지 않는다. 앵커 없는 `[0-9]+`는
  ECMA 검색에서 `abc123`에 맞는다. Python의 `$`는 끝 줄바꿈 앞에도 맞으므로 `re.match`·`re.search`가 아니라
  `re.fullmatch`로 판정한다.
- Python 기본 모드의 `\s`는 U+001C~U+001F·U+0085에도 맞고, ECMA의 `\s`는 U+FEFF에도 맞는다. `re.ASCII`는 `\d`·`\w`를
  ECMA와 같게 만들지만 `\s`는 맞추지 못한다(U+00A0·U+3000 등이 ECMA에만 남는다). `\d`는 Python에서 전각 숫자에,
  `\w`는 한글 음절과 호환 자모에도 맞는다. `\s`의 범위는 Unicode 버전에 따라서도 달라질 수 있다.
- llama.cpp 변환기는 `\d`·`\w`·`\s`와 전방 탐색을 지원하지 않아 경고만 남기고 아무 문자열이나 받는다.

아래 규칙은 엔진 차이가 아닌 다른 이유로 둔다.

- Python은 `[[`로 여는 클래스와 클래스 안에서 겹쳐 쓴 `--`·`&&`·`~~`·`||`를 앞으로 중첩 집합이나 집합 연산으로 읽을
  수 있다며 FutureWarning을 낸다. lint는 클래스 안의 `[`와 겹쳐 쓴 연산자를 위치와 관계없이 거부한다. ECMA-262 `v`
  플래그와의 호환은 목표가 아니다(JSON Schema는 `u` 플래그를 쓰라고 한다).
- 수량자나 `|`가 든 묶음에 수량자를 붙이면 `re.fullmatch`가 값 길이에 대해 지수 시간으로 되돌아갈(backtracking) 수 있다.
  빈 묶음은 반복해도 값을 읽지 않아서, 수량자를 붙이면 빈 값을 검사해도 시간이 반복 횟수에 비례한다(`^(){100000000}$`).
  두 경우를 모두 거부하므로 수량자가 붙는 것은 한 문자이거나, 한 글자 이상이고 한 가지로만 맞는 고정 길이 묶음뿐이다.
  반복할 때마다 값을 한 글자 이상 읽으니 어느 수량자도 값의 글자 수보다 많이 반복하지 않고, lint를 통과한 패턴은 지수
  시간으로 되돌아가지 않는다. `{m}`·`{m,n}`의 횟수는 따로 256 이하로 제한한다. lint는 `maxLength`를 모르므로 고정 상한을
  두며, 지금 B의 횟수(32·64)는 그 안에 든다. 다만 같은 문자에 맞는 수량자를 이어 쓴 패턴(`^[a-z]*[a-z]*@$`)은 통과하고,
  검사 시간이 값 길이의 다항식으로 늘 수 있다. 지금 B의 두 패턴은 수량자가 하나뿐이거나(`body`) 두 수량자 사이에 그
  클래스가 맞지 않는 `@`가 있어(`recipient`) 이 경우에 해당하지 않는다. lint는 수량자 없는 `|` 묶음의 개수도 제한하지
  않는다. 그런 묶음의 선택지가 같은 글자에 맞을 수 있으면(`(ab|[a]b)`를 여러 번 이어 쓴 패턴) 맞지 않는 값의 검사
  시간이 묶음 하나마다 최대 두 배로 늘어, 값 길이가 아니라 패턴 크기에 대해 지수로 는다. 지금 B의 두 패턴에는 `|`가
  없다.

테스트는 이 차이를 상수로 고정한다. 전각 숫자, U+3000, U+00A0, U+FEFF, U+0085, U+001F, 호환 자모, NFD 한글, 끝 줄바꿈,
최상위 `|`, CR, U+2028 픽스처마다 Python 판정은 실행해서 확인하고, ECMA-262(`u` 플래그) 판정은 명세의 정의에서 옮겨 적은
상수다. JavaScript 엔진은 실행하지 않는다. 허용한 패턴은 모든 픽스처에서 두 판정이 같고, 테스트의 픽스처 표(`STRING_PATTERN_FIXTURES`)에 든 금지 패턴은 적어도 한
픽스처에서 판정이 갈린다. `[가-힣]`은 NFD 한글·호환 자모·반각 자모·옛한글을 거부한다. 그중 `nfc_changed`가 따로
가려내는 것은 분해된 현대 음절(NFD 한글)뿐이다. 호환 자모·반각 자모와, 조합형 자모로만 되어 있어도 현대 음절로 합쳐지지
않는 값(낱자모, 옛한글 자모 열)은 NFC로 바뀌지 않아 그냥 `schema_invalid`로 센다. 지금 B 패턴에는 한글 범위가 없어 이
구분이 판정에 영향을 주지 않는다.

### 호스트 판정

- 각 팔은 자기 스키마로 판정한다. 보관한 응답의 모든 `send_message` 호출(실행됐든 아니든)에서 인자 원값을 형식·길이(코드
  포인트 수)·`re.fullmatch`로 검사하고, 정규화하지 않는다. NFC 정규화로 바뀌는 문자열 값이 있는 호출은 `nfc_changed`로
  따로 센다.
- 결과는 `exact`, `valid_but_wrong`, `schema_invalid`로 나뉜다. `exact`는 함수와 모든 인자가 기대 호출과 정확히 같은
  호출이며 실행 정확도의 정의다. 스키마 유효는 `exact`와 `valid_but_wrong`의 합이다. 네 비율(스키마 유효, 유효하지만
  틀림, 스키마 무효, 실행 정확도)의 분모는 모두 판정한 호출 수다. 정답은 같은 셀의 기존 에피소드 지표(정상 업무 성공)로 본다.
- 판정은 기록만 한다. 실행 경계의 차단 조건, 채점, 모델 오류, 종료 코드는 바꾸지 않는다. 판정으로 호출을 막으면 재시도
  양상이 바뀌어 프롬프트 효과와 호스트 강제 효과가 섞인다. 그래서 B 패턴을 어긴 값도 기존 규칙에 맞으면 가상 편지함에
  저장된다.
- 같은 출력이 A에서는 '유효하지만 틀림', B에서는 '스키마 무효'가 될 수 있으므로 두 팔의 판정은 합산하지 않는다.
  보고서 전체 `summary`의 기존 지표는 두 팔을 합친 값이고, 비교는 셀과 `summary.schema_variants`로 한다.

| 키 | 의미 |
|---|---|
| `trial_plan[].schema_variant`, `episodes[].schema_variant`, `episodes[].evaluation_conditions.schema_variant` | `A` 또는 `B`. 옵션을 켰을 때만 생긴다 |
| `episodes[].string_argument_verdicts` | 호출마다 `turn`, `verdict`, `violations`, `nfc_changed`. 위반 코드는 `arguments_not_object`, `additional_properties`와 인자별 `<이름>:required`·`:type`·`:maxLength`·`:enum`·`:pattern`이다 |
| `cells[].conditions.schema_variant`, `cells[].summary.string_arguments` | 팔별 셀과 그 셀의 판정 수, 네 비율(Wilson 구간 포함), 위반 코드별 수, `nfc_changed_calls` |
| `summary.schema_variants.A`, `summary.schema_variants.B` | 팔별 기존 지표와 `string_arguments` |
| `measurement_conditions.string_patterns` | 패턴, 허용 목록, 판정 규칙, 서버 강제 여부 문구 |
| `resource_bounds.max_episodes` | 옵션을 켜면 160 |

### 서버의 패턴 강제 여부

llama.cpp 빌드 b10729의 소스(`common/chat.cpp`, `common/json-schema-to-grammar.cpp`)를 읽어 확인했다. 실행해서 확인하지는
않았다.

- 이 빌드는 Qwen3.5 채팅 템플릿을 Qwen3-Coder XML 도구 호출 경로로 처리한다. 이 경로에서 type이 string인 인자 값은
  `\n</parameter>\n`까지 읽는 자유 텍스트라서 `maxLength`·`pattern`이 문법에 들어가지 않는다. `tool_choice`가 `auto`든
  `required`든 같고, `auto`이면 문법이 lazy가 될 뿐이다.
- 같은 빌드의 다른 도구 호출 경로 중에는 매개변수 스키마 전체를 문법 생성기에 넘기는 것도 있다. 그런 경로에서는
  `pattern`이 문법에 들어갈 수 있다.
- 확인한 Qwen3.5 채팅 템플릿은 도구 정의를 JSON 그대로(`tool | tojson`) 프롬프트에 넣는다. 그래서 이 구성에서 B의
  `pattern`은 모델이 읽는 힌트로만 작동한다. 채팅 템플릿이 Qwen3-Coder XML 경로를 타는 실행에서는 A/B 차이를 제약 디코딩의
  효과로 보고하지 않는다.
- 문자열 값을 문법으로 마스킹하면 분포가 제한되는 대신 왜곡될 수 있다는 경고가 있다(Mitigating Bias in Locally
  Constrained Decoding). 이 구성의 문자열 값에는 해당하지 않는다. 서버가 문자열 값을 마스킹하지 않기 때문이다. 디코더 쪽
  보정은 가져오지 않는다.
- 보고서의 `server_props.build_info`·`server_props.model`·`server_props.chat_template_sha256` 중 하나라도 바뀌면 그
  빌드의 소스에서 템플릿이 타는 도구 호출 경로와 문자열 인자 처리를 다시 읽는다. 평가기는 서버가 `pattern`을 강제하는지
  확인하지 않는다.

## Kiwi·qmd 검색 비교

```text
python scripts/evaluate_retrieval_backends.py --fixture recall_hard
python scripts/evaluate_retrieval_backends.py --fixture recall_hard --kiwi
python scripts/evaluate_retrieval_backends.py --fixture recall_hard --kiwi --qmd-cli <absolute-qmd-dist-cli-qmd.js> --node <node-executable> --qmd-mode search --artifact-dir <external-experiment-directory> --output <report.json>
```

기본은 기존 BM25와 한글 bigram 조건만 비교한다. Kiwi가 필요한 별도 실험 환경에는
`kiwipiepy==0.24.0`, qmd에는 `@tobilu/qmd@2.8.3`을 설치해 이번 관측을 재현할 수 있다.
핵심 엔진의 설치 의존성으로 넣지 않는다. qmd는 셸 문자열 대신 node와 절대 JS 경로로 실행한다.

입력은 저장소에 포함된 `recall`, `recall_advanced`, `recall_hard` fixture만 허용한다.
실제 볼트 경로를 받지 않는다. 저장소에서 fixture까지의 접합점·링크를 검사하고 정책이 허용한
노트만 숫자 ID로 복사한다. qmd 설정·색인·캐시는 실험 디렉터리에 둔다. 상속된 `INDEX_PATH`가
다른 색인으로 보내지 못하도록 격리하고, 반환 URI는 이번 복사본의 정확한 ID에만 연결한다.
입력·정책·관련 코드는 실행 전후 해시로 확인한다. 선택한 모델은 캐시에서 복사할 때 원본과
복사본의 해시를 확인하며 질의 전체의 종료 시점 모델 파일 검증을 제공하지 않는다.
외부 프로그램 자체의 OS 샌드박스는 아니다.

qmd `search`는 키워드 조건이다. 선택형 `query`는 `--allow-model-download`를 함께 명시해야 한다.
이번 벡터 조건은 **CPU, typed lex+vec, no-rerank**다. query expansion·LLM reranker와 전체 qmd 기본
파이프라인을 평가하지 않는다. `--qmd-model-cache <existing-cache>`는 이미 내려받은 공개
embeddinggemma GGUF 하나만 해시를 확인해 복사한다. 전체 모델 묶음을 자동 설치하지 않는다.

top5 경로 순위의 recall·nDCG, 언어/시나리오별 분모, 답 없음 오탐과 호출 오류를 보고한다.
이는 답변 정확도나 시간 유효성 판정이 아니다. Kiwi는 미리 만든 토큰을 사용하고 기존 경로는
매 질의 스캔, qmd는 프로세스 시작이 포함되므로 지연을 같은 생산 환경 속도로 일반화하지 않는다.
색인·모델 준비 비용은 질의 지연과 따로 기록한다.

## Windows 부분 편집

Windows의 원자적 교체가 WinError 5/32로 거부되면 최대 6회, 총 대기 0.31초 범위에서 재시도한다.
매번 lease·설정·대상 revision·관련 출처 해시를 재검사한다. 그 사이 원문이나 정책이 바뀌면
중단하고 덮어쓰지 않는다. 지속적인 잠금이나 다른 오류도 실패로 남기며 정책이 막은 정리를 강행하지 않는다.

Python 3.12의 경로 해석 중 다른 프로세스가 guard를 지우면 남을 수 있는 DOS drive `\\?\` 접두어는
동일한 물리적 root임을 확인한 비교에서만 보강한다. 원래 OS 입출력 경로를 유지한다.
UNC/device 표기를 임의로 같은 경로로 취급하지 않는다. 협조 잠금은 잠금에 참여하는 writer를 위한
것이며 다중 파일 트랜잭션·외부 앱 편집의 완전한 차단을 제공하지 않는다.

## 간헐 실패 테스트: 분류와 반복 측정

`tests/test_vault_runs.py`의 동시 prepare 테스트
(`test_concurrent_prepares_preserve_one_manifest_and_at_most_one_completion`)는 Windows에서 가끔
실패한다. 원인은 아직 측정하지 못했다. 그래서 고치기 전에 실패를 분류하고, 효과는 반복 횟수와
상한으로 잰다.

### 실패 분류

- 동시 실행한 자식이 예상하지 못한 오류로 끝나면 테스트는 모든 자식을 모은 뒤 한 번 실패한다.
  메시지에는 자식마다 예외 클래스, errno, winerror, 가장 안쪽 코드 위치, 경로 범주가 들어간다.
- 경로 범주는 엔진의 런타임 배치에서 나온다. `lock`(잠금 lease와 `.guard`), `temp`
  (`.vault-state-` 임시 파일), `manifest`(`run-*-prepared.json`), `receipt`(`run-*-completed.json`,
  `run-compare-*.json`), `other`다. 원래 경로·노트 내용·예외 문장은 싣지 않는다.
- `replace_failed`처럼 `from None`으로 원인을 감춘 오류도 암묵 context를 따라가 분류한다.
  비정상 종료와 시간 초과도 따로 적는다.
- 분류 코드는 테스트 지원 모듈 `tests/runtime_failures.py`에 있다. 엔진은 이 모듈을 가져오지 않으며
  엔진 동작은 바뀌지 않는다.

### 재시도는 아직 넣지 않는다

[DeleteFileW 문서](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-deletefilew)는
지운 파일이 마지막 핸들이 닫힐 때까지 삭제 대기 상태로 남는다고 설명한다. 그 사이 같은 이름을 열면
ERROR_ACCESS_DENIED(5)로 실패한다. guard를 지우고 곧바로 다시 만드는 잠금 경로가 이 상태에 걸린다는
가설이 있지만 확인하지 못했다. 지금 재시도를 넣으면 다른 원인까지 가린다.

- 최신 Windows 10/11의 NTFS에서는 파일 시스템이 지원하면 DeleteFileW가 POSIX 삭제 방식을 쓴다.
  이름이 곧바로 사라지므로 위의 삭제 대기가 생기지 않는다.
- 측정 호스트(Windows 11 빌드 26300, NTFS 임시 폴더, Python 3.12.10)에서 탐침을 돌렸다
  (2026-10-20). FILE_SHARE_DELETE를 포함한 다른 핸들을 연 채 `os.remove`로 지우자 이름이 곧바로
  사라졌다. 같은 이름의 `O_CREAT|O_EXCL` `os.open`도 성공했다. 이 호스트에서는 삭제 대기 가설에
  반하는 증거다.
- POSIX 삭제를 쓰지 않는 곳(다른 파일 시스템, 네트워크 공유 등)에서는 삭제 대기가 여전히 생길 수
  있다. 접근 거부의 다른 원인도 배제하지 못했다.

- 분류 메시지의 `lock_access_denied`는 lock·guard 경로의 접근 거부를 뜻한다. `os.open`은 C 런타임을
  거치며 EACCES(13)만 남기고 winerror를 비운다. 그래서 winerror 5와 winerror 없는 errno 13을 함께
  센다. 이 표시는 후보일 뿐 삭제 대기의 확인이 아니다. POSIX의 errno 13은 보통의 권한 거부다.
- 재시도는 lock 경로의 접근 거부(winerror 5 또는 winerror 없는 errno 13, 즉 `lock_access_denied`)가
  반복 측정과 최소 재현으로 삭제 대기임이 확인된 뒤에만 검토한다.
  그때도 새 일정을 만들지 않고 원자적 교체의 기존 제한 재시도(`REPLACE_RETRY_DELAYS`, WinError 5/32,
  최대 6회·총 대기 0.31초)를 재사용한다. 지속적인 거부·정책 변경·소유자 변경은 계속 fail-closed로 남긴다.

### 반복 측정 도구

```text
python scripts/flaky_repeat.py test_vault_runs.VaultRunsTests.test_concurrent_prepares_preserve_one_manifest_and_at_most_one_completion -n 300
python scripts/flaky_repeat.py <module.Class.test_name> -n 30 --fresh-process --json
```

- unittest id 하나를 N번 실행한다. 기본은 같은 프로세스이고 `--fresh-process`는 실행마다 새
  인터프리터를 띄운다. 통과·실패·오류·건너뜀을 센다. 기본 검색 경로는 `tests/`다.
- 같은 프로세스에서는 모듈을 한 번만 가져온다. 모듈·클래스 수준 상태가 실행 사이에 남는다. 격리가
  중요하면 `--fresh-process`를 쓴다.
- 실패와 오류가 실패 사건(x)이다. 건너뛴 실행은 분모에서 뺀다. 상한은 `scripts/eval_stats.py`의
  단측 95% Clopper-Pearson이다.
- 출력은 `x/N failures; failure rate <= p`다. x가 0이면 `not observed in N runs (rate <= p)`라고 쓰고
  고쳤다고 쓰지 않는다. 상한이 관측률보다 1%p 넘게 높으면 상한이 넓다고 적는다. x가 0이면 수백 번의
  겨냥 반복을, x가 1 이상이면 반복을 더 늘리라고 적는다.
- 실패 서명은 예외 클래스와 메시지 첫 줄만 남긴다. 그 줄의 절대 경로는 `<path>`로 바꾼다. 따옴표로
  감싼 경로는 닫는 따옴표까지, 감싸지 않은 경로는 첫 공백까지 바꾼다. 상대 경로와 나머지 메시지는
  그대로 남으므로 공유하기 전에 확인한다. 서로 다른 서명은 10개까지 보존한다.
- 실행을 마치면 x와 관계없이 종료 코드 0, 사용법 오류는 2다. 측정 도구이며 게이트가 아니다.
- 테스트 id를 불러오지 못하면 첫 실행에서만 사용법 오류로 본다. 그 뒤의 불러오기 실패(가져오기
  시점의 간헐 실패)는 오류 사건 `LoadError`(`--fresh-process`에서는 `ChildLoadError`)로 센다. 이미 마친
  실행은 버리지 않는다.

### 수정 효과를 재는 규칙

- 수정 효과는 영향받는 테스트(또는 최소 재현)를 수백 번 겨냥해 반복해서 잰다. 결과는 x/N과
  정확한 상한으로 보고한다.
- 0/30의 상한은 약 0.095, 0/300은 약 0.0099다. 30번 연속 통과로 95% 신뢰로 말할 수 있는 것은
  실패율 약 9.5% 미만까지다. 1% 미만을 말하려면 약 300번(정확히는 299번)이 필요하다.
- 전체 테스트 한 번 통과는 회귀 확인일 뿐이다. 간헐 실패가 사라졌다는 근거가 아니다.
- 같은 기계에서 다른 작업이 돌았다면 그 조건을 함께 적는다.
- Gruber 등(ICST 2021)은 Python 프로젝트 22,352개의 테스트 876,186개를 분석했다. 통과한 테스트가
  flaky가 아니라고 95% 신뢰로 말하려면 평균 약 170회의 재실행이 필요하다고 추정했다. Linux에서
  실행한 데이터셋의 평균이며 이 테스트 묶음의 고정 기준이 아니다.

### 요청 본문을 읽지 않는 시험 서버

v0.31.0의 시험·문서 변경이다. `tests/test_tool_safety_evaluation.py`의 루프백 전송 시험 두 개가 Windows에서
가끔 기대한 오류 대신 `transport_error`로 실패했다. 원인을 측정으로 확인했고 시험 코드만 고쳤다. 엔진과 전송 코드는 그대로다.

**원인.** 두 시험의 HTTP 처리기는 POST 본문을 읽지 않고 응답한 뒤 연결을 닫았다.
[RFC 1122](https://www.rfc-editor.org/rfc/rfc1122) §4.2.2.13은 half-duplex 종료를 구현한 호스트가 받은 데이터가
남은 채로 닫거나 닫은 뒤에 데이터를 받으면, 데이터가 사라졌음을 알리도록 TCP가 RST를 보내야 한다고(SHOULD) 적는다.
CPython 3.12의 `http.client`는 헤더를 보낸 뒤 본문을 따로 `send()`한다. 서버의 첫 수신에 헤더만 들어오고 처리기가
본문을 읽지 않은 채 응답하면 본문이 읽히지 않고 남는다. Windows 클라이언트가 응답을 읽기 전에 RST를 받으면
`ConnectionAbortedError`(WinError 10053)나 `ConnectionResetError`(10054)가 난다. `LocalChatTransport`는
`HTTPError`가 아닌 `OSError`(`URLError`·시간 초과 포함)를 모두 `transport_error`로 바꾸므로, `http_error`나
`response_bytes_exceeded`를 기대한 시험이 실패한다. socketserver는 요청을 닫기 전에 이미 `shutdown(SHUT_WR)`을
부르므로 그 호출을 더해도 막지 못한다.

**수정.** 두 처리기의 `do_POST`는 응답하기 전에 `self.rfile.read(int(self.headers.get('Content-Length') or 0))`로
본문을 읽는다. `Content-Length`만큼 읽는 점은 같은 파일의 다른 처리기와 같다. 다른 처리기는
`int(self.headers['Content-Length'])`를 쓰고, 새로 넣은 두 줄은 헤더가 없으면 0바이트를 읽는다.

**재발 방지 검사.** `tests/test_http_handlers.py`는 `tests/*.py`(하위 폴더 제외)를 ast로 읽는다. `def`·`async def`로
정의한 `do_POST`·`do_PUT`·`do_PATCH`가 첫 매개변수의 `rfile.read(...)`도, 허용한 도우미(`BODY_READ_HELPERS`, 지금은
없음)도 부르지 않으면 `파일:클래스·함수 경로 (line N)`으로 실패한다. 일부러 본문을 남기는 처리기는 사유와 함께
`UNREAD_BODY_ALLOWED`에 둔다(지금은 비어 있다). 더는 해당하지 않는 허용 항목도 실패한다. `tests/test_winfs.py`의
정션 검사는 파일 전체의 문자열 검사지만, 이 규칙은 메서드 하나로 범위를 좁혀야 해서 ast를 쓴다. 메서드 안에 읽기
호출이 있는지만 본다. 응답보다 먼저 읽는지, 모든 경로에서 읽는지와 별칭·부모 클래스·목록에 없는 도우미를 거친 읽기는
증명하지 않는다. 대입·lambda·`setattr`로 붙이거나 검사 범위 밖의 부모 클래스에서 물려받은 처리기는 찾지 못한다.

**측정.** 2026-10-24 22:33–22:46 KST, Windows 11 빌드 26300, Python 3.12.10, `flaky_repeat.py` 같은 프로세스
반복 N=300, 단측 95% Clopper–Pearson이다. 수정 전은 `452c5c0`의 사본, 수정 후는 이 변경이다. 같은 기계에 다른 작업
세션의 프로세스가 떠 있었다. 기록은 아래 명령의 `--json` 출력으로 남겼고, 원본 기록과 아래 탐침의 소스는 릴리스 검증 자료와
함께 보관한다.

```text
python scripts/flaky_repeat.py test_tool_safety_evaluation.TransportTests.test_http_redirect_is_not_followed_or_retried_and_body_is_not_logged -n 300 --json
python scripts/flaky_repeat.py test_tool_safety_evaluation.TransportTests.test_http_response_bytes_are_bounded_before_json_parsing -n 300 --json
```

| 시험 | 수정 전 | 수정 후 |
|---|---|---|
| `TransportTests.test_http_redirect_is_not_followed_or_retried_and_body_is_not_logged` | 4/300, 상한 0.0303(4번 모두 `'transport_error' != 'http_error'`) | 0/300, 상한 0.0099 |
| `TransportTests.test_http_response_bytes_are_bounded_before_json_parsing` | 0/300, 상한 0.0099 | 0/300, 상한 0.0099 |

- 응답 바이트 시험은 2026-10-23 v0.29.0 릴리스 확인 때 같은 호스트에서 수정 전 코드로 8/300(상한 0.0476, 8번
  모두 `'transport_error' != 'response_bytes_exceeded'`)이었다. 같은 코드에서도 측정 시간대에 따라 실패 수가 달랐다.
- 시험 반복만으로는 수정 전후의 차이(4/300과 0/300)가 우연이 아니라고 말하기에 부족하다. 원인은 저장소 밖의
  탐침으로 따로 확인했다. 두 시험과 같은 처리기 네 개(응답 바이트·리다이렉트, 본문을 읽음·안 읽음)를 띄우고
  `LocalChatTransport`로 요청을 번갈아 300번씩 보냈다(같은 날 22:45). `transport_error` 뒤에 감춰진 원래 예외는
  `__context__`에서 읽었다.

| 처리기 | 본문을 읽지 않음 | 본문을 읽음 |
|---|---|---|
| 응답 바이트(200, 본문 2,048바이트, 상한 128바이트) | 11/300 `transport_error`, 모두 `ConnectionAbortedError` 10053 | 0/300 |
| 리다이렉트(302) | 7/300 `transport_error`, 모두 `ConnectionAbortedError` 10053 | 0/300 |

2026-10-23 같은 호스트의 이전 탐침에서도 본문을 읽지 않으면 300번 중 9번(10053 8번, 10054 1번) 연결이 끊겼고,
읽으면 0번이었다.

**한계.**

- 0/300은 단측 95% 신뢰로 실패율이 약 1% 이하라는 뜻이지 없다는 증명이 아니다.
- Linux·macOS에서는 이 경쟁이 드러나지 않을 수 있어 CI 통과가 수정의 증거가 아니다. 플랫폼과 무관한 방어는 위의
  정적 검사다.
- `Content-Length`를 그대로 믿는 읽기는 작은 JSON을 보내는 루프백 시험용이다. 크기 상한 없이 실제 서버로 옮기지 않는다.
- 본문 길이를 알 수 없는 처리기(chunked, 일부러 큰 본문)는 `finish()`에서 `shutdown(SHUT_WR)` 뒤 시간 제한을 두고
  EOF까지 읽는 방식(Microsoft Learn의 graceful shutdown 순서)을 쓸 수 있다. 시간 제한이 없으면 클라이언트가 연결을
  열어 둘 때 `server.shutdown()`이 멈출 수 있어 공용 `server()` 도우미에는 넣지 않았다. 지금 그런 처리기는 없다.

## 연구와 관측

- [LongMemEval-V2](https://arxiv.org/abs/2605.12493), [MemoryAgentBench](https://arxiv.org/abs/2507.05257): 상태 변화와 실제 기억 사용을 분리하는 평가 설계 참고.
- [MLCL](https://arxiv.org/abs/2601.05366), [BFCL](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard): 언어별 도구 인자 실패를 가르는 실험 틀. MLCL의 한국어 검증을 재현한 것은 아니다.
- [Kiwi](https://doi.org/10.23287/KJDH.2024.1.1.6), [qmd](https://github.com/tobi/qmd): 선택형 검색 대조군.
- [Git의 Windows rename 수정](https://github.com/git/git/commit/391bceae4350): 열린 핸들 때문에 교체가 거부되는 원인과 제한된 재시도의 참고.
- [An Empirical Study of Flaky Tests in Python](https://arxiv.org/abs/2101.09077)(Gruber 등, ICST 2021), [DeleteFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-deletefilew): 재실행 횟수와 상한으로 간헐 실패를 보고하는 규칙, 삭제 대기 가설의 근거. 논문의 170회는 데이터셋 평균이며 출처의 측정은 재현하지 않았다.
- [Defeating Nondeterminism in LLM Inference](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/), [llama.cpp 서버 README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md): 부하·배치 크기와 프롬프트 캐시가 temperature 0 출력을 바꿀 수 있다는 근거. 출처의 측정은 재현하지 않았다.
- [RFC 1122](https://www.rfc-editor.org/rfc/rfc1122) §4.2.2.13, [Graceful Shutdown, Linger Options, and Socket Closure](https://learn.microsoft.com/en-us/windows/win32/winsock/graceful-shutdown-linger-options-and-socket-closure-2): 읽지 않은 수신 데이터가 남은 채 닫으면 RST를 보낸다는 규칙과, 명시적 shutdown 뒤 남은 데이터를 받는 종료 순서의 근거. RST 규칙은 RFC에만 있고 Microsoft 문서는 RST나 읽지 않은 수신 데이터를 다루지 않는다.
- [The Constraint Tax](https://arxiv.org/abs/2605.26128), [Mitigating Bias in Locally Constrained Decoding](https://arxiv.org/abs/2606.01926): 스키마 유효·정답·실행 정확도·'유효하지만 틀림'을 따로 세는 짝 비교 설계와, 문자열 값 마스킹이 분포를 왜곡할 수 있다는 경고의 참고. 출처의 측정은 재현하지 않았고 디코더 쪽 방법은 가져오지 않았다.
- [JSON Schema Regular Expressions](https://json-schema.org/understanding-json-schema/reference/regular_expressions), [llama.cpp b10729](https://github.com/ggml-org/llama.cpp/tree/b10729): `pattern`이 ECMA-262 문법이고 이식 가능한 부분집합을 권장한다는 근거(그 부분집합에도 `.`와 전방 탐색이 있어 더 좁은 허용 목록을 쓴다)와, 서버가 문자열 값의 `pattern`을 문법에 넣지 않는다는 소스 근거. 서버 동작은 실행으로 확인하지 않았다.

공식 논문의 데이터·코드를 복사하거나 공식 벤치마크 점수를 재현했다고 주장하지 않는다.
2026-10-18 개발 브랜치에서 실행한 관측은 [당시 검증 기록](verification/eval-stability-20261018.md)과
[기계 판독 요약](verification/eval-stability-20261018.json)에 보존한다. 그 기록의 미공개·미설치
서술은 관측 시점의 상태다. 최종 릴리스 소스의 검사와 공개 자산 검증은
[v0.24.0 검증 기록](verification/v0.24.0.md) 및 공개 릴리스의 `verification.json`에서 확인한다.

경쟁 모델 호출 없이 같은 예산으로 실행한 한국어 clean 조건에서 canonical은 0/4,
literal-identifiers는 4/4 완료했다(설명 언어별 각 두 반복, 오류 0). 최초 32에피소드의
오류 8건도 별도로 보존하며 방어 성공으로 바꾸지 않는다. 구조화 기억의 10/10은 균형
10문항 pilot이고 전체 50문항·자유형 답변의 점수가 아니다.

`recall_hard` 64질의에서 typed lex+vec의 recall@5는 1.0000이었지만 답 없음 8/8에서도
후보를 반환했다. 전체 벡터 비교는 환경 격리 보강 전의 관측이다. 보강 후 네 키워드 방식의
64질의와 벡터 한 질의를 다시 확인했으며, 전체 벡터 64질의를 재실행한 기록은 아니다.
이 관측으로 벡터 검색이나 문자열 보존 지시를 기본값에 자동 적용하지 않는다.
