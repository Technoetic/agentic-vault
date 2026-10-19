# 한국어 모델·검색 비교와 Windows 편집 보강

v0.24.0에 포함된 선택형 평가 도구와 Windows 부분 편집 보강, v0.25.0에서 더한 선택형 측정 조건
기록이다. 요청·설명 언어와 문자열 보존 지시, 공개 fixture의 검색 순위를 따로 비교하고, 로컬 모델
실행의 프롬프트 캐시·슬롯 부하·반복 일치를 선택해서 기록한다. 기본 세션 훅·모델 호출·
런타임 의존성·검색 설정을 추가하지 않는다. 기존 설치 캐시와 볼트는 설치·upgrade를
별도로 수행하기 전까지 그대로다. v0.25.0의 [릴리스 노트](releases/v0.25.0.md)와
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
- 반복은 1~5회, 최대 80개 에피소드다. 선언한 seed로 순서를 섞고 같은 반복 조건에 같은 seed를 요청한다.
  서버가 seed를 따르는지, 반복들이 독립적인지는 인증하지 않는다.
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
바꾸지 않는다. 옵션을 주지 않으면 요청 바이트·출력 JSON·종료 코드가 v0.24.0과 같다.
테스트는 v0.24.0(`d2b9315`)에서 만든 행렬 기본 출력과 기본 요청 바이트의 SHA-256을 고정해 비교한다.

```text
python scripts/evaluate_tool_safety.py --backend local-model --model <local-model-id> --api-key-env <environment-variable-name> --no-prompt-cache --slot-snapshots --receipt-out <new-file>
python scripts/evaluate_tool_matrix.py --backend local-model --model <local-model-id> --api-key-env <environment-variable-name> --request-language ko --instruction-language en --prompt-variant canonical --condition clean --repeats 5 --repeat-agreement --slot-snapshots --receipt-out <new-file>
```

| 옵션 | 동작 | 사용 위치 |
|---|---|---|
| `--no-prompt-cache` | 모든 채팅 요청 끝에 llama-server 요청 옵션 `"cache_prompt": false`를 붙인다 | 두 스크립트 |
| `--slot-snapshots` | 에피소드마다 시작 전과 끝난 뒤 `GET /slots`를 한 번씩 요청해 전체 슬롯 수와 바쁜 슬롯 수를 남긴다 | 두 스크립트 |
| `--repeat-agreement` | `--repeats`(2~5)의 모든 반복이 기본 `--seed`를 요청한다. 셀마다 같은 구성을 순차로 반복하고 첫 실행과 해시가 같은 반복의 비율을 낸다 | `evaluate_tool_matrix.py` |

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

## 연구와 관측

- [LongMemEval-V2](https://arxiv.org/abs/2605.12493), [MemoryAgentBench](https://arxiv.org/abs/2507.05257): 상태 변화와 실제 기억 사용을 분리하는 평가 설계 참고.
- [MLCL](https://arxiv.org/abs/2601.05366), [BFCL](https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard): 언어별 도구 인자 실패를 가르는 실험 틀. MLCL의 한국어 검증을 재현한 것은 아니다.
- [Kiwi](https://doi.org/10.23287/KJDH.2024.1.1.6), [qmd](https://github.com/tobi/qmd): 선택형 검색 대조군.
- [Git의 Windows rename 수정](https://github.com/git/git/commit/391bceae4350): 열린 핸들 때문에 교체가 거부되는 원인과 제한된 재시도의 참고.
- [Defeating Nondeterminism in LLM Inference](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/), [llama.cpp 서버 README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md): 부하·배치 크기와 프롬프트 캐시가 temperature 0 출력을 바꿀 수 있다는 근거. 출처의 측정은 재현하지 않았다.

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
