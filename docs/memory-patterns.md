# 논문·공식 오픈소스의 기억 패턴 적용

이 개발 브랜치는 아래 아홉 자료에서 볼트에 맞는 동작을 구현한다. 원 논문의
전체 알고리즘·데이터베이스·학습 모델을 재현하거나 논문의 점수를 가져오지 않는다.
핵심은 Python 표준 라이브러리이며, 파일은 원문으로 남는다. 새 기능은 명시적으로
호출한다. 기본 `vault_recall.recall()`의 출력·순위와 기존 세션 주입은 유지한다.

| 참고 자료 | 흡수한 동작 | 실행 가능한 구현 | 검증·효과 및 선택형 의존 |
|---|---|---|---|
| [A-MEM 논문](https://arxiv.org/abs/2502.12110), [공식 시스템](https://github.com/WujiangXu/A-mem-sys) | 새 기억과 기존 노트의 연결을 검토하고 출처를 보존 | `vault_links.py`의 해시·diff 제안, `vault_memory.py`의 구조화 단위  | 연결·인용·해시 동작 테스트 통과. 의미 관계/기억 진화의 업무 효과 미측정; 외부 시스템 불필요 |
| [SimpleMem 논문](https://arxiv.org/abs/2601.02553), [공식 코드](https://github.com/aiming-lab/SimpleMem) | 독립적으로 이해할 수 있는 사실 단위, 정확한 중복 병합, 질문에 따른 선택 | 출처 인용을 검증하는 `compile_units()`; 의미 해석·대명사 해소는 호스트가 명시적으로 작성  | 단위·중복·조건·잠재 충돌 36개 테스트 통과. 호스트 의미 해석의 정확도/압축 절감 효과 미측정; 외부 모델 불필요 |
| [HippoRAG 2 논문](https://arxiv.org/abs/2502.14802), [공식 코드](https://github.com/OSU-NLP-Group/HippoRAG) | 질의에서 연결된 근거로 범위를 확장 | 실제 파일명이 유일한 위키링크를 최대 2단계 순회하고 경로·해시를 반환; PPR·엔티티 모델은 별도  | 2단계 연결 fixture 통과. 합성 전체 검색 재현율 78.6%→100%는 시간/결합 등 공동 효과; PPR/엔티티 모델 미도입 |
| [Zep 논문](https://arxiv.org/abs/2501.13956), [Graphiti](https://github.com/getzep/graphiti) | 사실의 유효 기간과 출처, 과거 시점 조회 | `valid_from`·`valid_until`·`checked_at`·`superseded_by`와 `--as-of`; Graphiti DB나 완전한 이중 시간 DB는 사용하지 않음  | 현재/과거·만료 fixture 통과, 합성 stale 노출 4/4→0/4. Graphiti DB와 실업무 현재성 정확도 미측정 |
| [LongMemEval V2](https://arxiv.org/abs/2605.12493), [공식 코드](https://github.com/xiaowu0162/LongMemEval-V2) | 변경 사실·절차·전제 오류·정답 없음의 구분 | 경험 fixture와 고급 fixture의 질의별·시나리오별 검색/노출 평가  | 기존16/경험16/고급16질문 평가 실행. 출처 검색·노출만 측정; 공식 대규모 QA/클라우드 모델 미실행 |
| [MINJA 논문](https://arxiv.org/abs/2503.03704), [공식 코드](https://github.com/dsh3n77/MINJA) | 저장된 오염이 후속 작업으로 전파되는 경로 점검 | 오염 근거 노출 측정, 원문 재조회·정책 검사, 합성 실행기의 금지 행동 차단과 철회 후 잔류 검사  | 합성 실행기의 금지 행동 차단·실제 출처 철회 후 잔류0 확인. 실제 공격 성공률 미측정; 공격 모델 미도입 |
| [MemoryArena 논문](https://arxiv.org/abs/2602.16313), [공식 코드](https://github.com/ZexueHe/MemoryArena) | 여러 세션의 기억이 실제 다음 행동에 영향을 주는지 평가 | 상태 전제·사후 조건으로 채점하는 `evaluate_memory_workflows.py`; 원 벤치마크 재현은 아님  | 같은 로컬 Qwen 모델/예산의 후속8시도: 기존6/8→개선8/8. 기능 실험이며 일반 성능/공식 점수 아님; 로컬 모델 선택형 |
| [qmd 공식 코드](https://github.com/tobi/qmd) | BM25·검색 결과 결합과 선택형 외부 의미 검색 | `lexical`/`bm25`/`hybrid`, RRF, `import_qmd_candidates.py`와 원문을 다시 읽는 sidecar 입력  | BM25/RRF/원문 재조회·어댑터20테스트 검증. qmd 실제 임베딩/재순위 모델 미설치·효과 미측정; 외부 제공자 선택형 |
| [Letta Code](https://github.com/letta-ai/letta-code) | 파일 기반 지속 기억과 교훈의 후속 효과 추적 | 기존 승인된 교훈 제안에 적용 가능성·성공·재발·역효과 관측 추가; 자동 승격 없음  | 관측34테스트·현재 근거/분모/동시 기록 검증. 교훈의 실제 재발 감소 효과 미측정; Letta 서버 불필요 |

## 시간·연결·결합 검색

저장소 루트에서 실행한다. 설치된 플러그인은 해당 설치의 실제 경로를 사용한다.

```text
python skills/agentic-vault/scripts/vault_recall.py --vault PATH --query "복구 절차" --as-of 2026-10-07 --expand-links 2 --backend hybrid --max-tokens 1500 --format json
```

시간은 ISO 날짜 또는 초·시간대가 있는 ISO datetime이다. 날짜는 UTC 자정으로
비교한다. 유효 구간은 `[valid_from, valid_until)`이다. 잘못된 날짜·중복 키·해석할 수
없는 YAML은 확정하지 않고 진단한다. 기간이 없는 자료도 현재 사실로 인증하지
않는다. `checked_at`은 작성자가 기록한 점검 시각이며 진실 인증이 아니다.
`superseded_by`는 유일하게 확인되는 실제 파일명 연결에서만 해석한다.

위키링크는 `[[실제 파일명]]`으로만 확장한다. aliases와 중복 파일명을 추정하지
않는다. 반환된 그래프 경로·시간 정보·해시는 출처 근거이며 권한이 아니다.
BM25는 어휘 검색이며, `hybrid`는 어휘 점수/BM25/명시적 외부 후보의 순위를 RRF로
결합한다. 임베딩은 내장하지 않는다. 모든 읽기는 deny/exclude·예약 `.git`·링크 파일
차단·파일/전체 바이트 상한을 따른다. 최종 출처와 정책이 바뀌면 결과를 보류한다.
`matches`는 후보 목록이고 `context`는 예산 안의 실제 반환 텍스트다. 후보가 있다고
그 본문이 모델에게 전달됐다고 집계하지 않는다. 누락·불완전 검색은 diagnostics로
확인한다. 시간·파일 해시는 의미적 진실이나 안전한 지시를 인증하지 않는다.

## 선택형 qmd 연동

qmd 자체의 설치·모델 다운로드·임베딩 생성은 별도 선택이다. 이 브랜치는 qmd 엔진을
실행하지 않는다. 공식 `--json` 결과 배열을 다음 어댑터에 명시적으로 전달할 수 있다.

```text
qmd query "복구 절차" --json -n 10
python scripts/import_qmd_candidates.py --collection MY_COLLECTION --input qmd-results.json
```

어댑터는 `qmd://MY_COLLECTION/20-knowledge/note.md` 같은 경로와 결과 순서만
사용한다. snippet·score 설명·승인 문구는 근거로 사용하지 않는다. 출력은 다음처럼
호스트가 선택한 vault-relative JSON 파일에 저장한다. 셸별 인코딩을 확인하고 UTF-8을
사용한다. 변환 출력이 후보의 현재 파일 정책 적합성을 증명하는 것은 아니다.

```json
{"provider":"qmd","candidates":[{"path":"20-knowledge/note.md","rank":1}]}
```

```text
python skills/agentic-vault/scripts/vault_recall.py --vault PATH --query "복구 절차" --backend hybrid --external-candidates 00-meta/qmd-candidates.json --format json
```

검색기는 허용된 원문을 직접 다시 읽고 해시를 확인한다. 외부 후보는 최대 50개,
sidecar는 64 KiB이며 qmd의 원시 결과를 그대로 넣지 않는다. 삭제·철회된 출처의 옛
snippet을 재사용하지 않는다. 기본 검색은 외부 프로세스·네트워크를 호출하지 않는다.

## 출처가 붙은 기억 단위

호스트가 이번 작업에서 확인한 내용을 `subject`·`predicate`·`value`·`time`·
`conditions`·`uncertainty`로 작성한다. 사람·조직·대명사·날짜는 원문으로 확인하여
명시하고, 확인할 수 없으면 불확실성에 남긴다. 컴파일러는 의미를 새로 발명하지 않는다.
예를 들어 원문의 LF 기준 6번째 줄이 정확히 `Atlas retention: 24 months`일 때:

```json
[
  {
    "subject":"Atlas",
    "predicate":"retention_months",
    "value":"24",
    "time":{"valid_from":"2026-10-07"},
    "conditions":["Production artifacts"],
    "uncertainty":"Only the scope stated by this source has been confirmed.",
    "sources":[{"path":"20-knowledge/atlas.md","line":6,"quote":"Atlas retention: 24 months"}]
  }
]
```

```text
python skills/agentic-vault/scripts/vault_memory.py --vault PATH --input 00-meta/memory-units.json --query "Atlas retention" --max-tokens 1500 --format json
```

출처에 선택적으로 `expected_sha256`을 넣어 이전 원문의 64자리 소문자 해시를
요구할 수 있다. 출력의 sources.binding은 현재 원문을 결합한다.
시간·조건·불확실성까지 정확히 같은 단위만 병합하며 출처는 합친다. 같은 주체/술어·같은 조건에서
기간이 겹치는 다른 값은 잠재 충돌로 표시한다. 조건이나 겹치지 않는 기간이 다르면
이 검사에서는 충돌로 표시하지 않는다. 자동으로 하나를 진실로 선정하지 않는다. 예산에서 빠진 단위와 관련된 충돌도 진단에 남긴다.
일부 입력이 거부돼도 전체 status가 ok일 수 있다. `diagnostics.complete`, rejected,
omissions와 potential_conflicts를 모두 확인한다. 단위·출처 인용은 통째로 선택하며
인용을 잘라 의미를 바꾸지 않는다. 128단위·단위당 8출처·64파일 등의 상한이 있다.

결과는 `authority=data_only`, `requires_approval=true`인 검토 자료다. CLI는 노트·hot·
handoff를 수정하지 않는다. 호스트가 기존 작업 범위와 파일 소유권에 따라 필요한
내용만 반영한다. 출력의 승인 필드는 현재 사용자 승인을 대신하지 않는다.

## 교훈의 성공·재발·역효과 기록

기존 `vault_proposals.py`에서 실제로 적용된 제안 ID를 사용한다. 관측은 호스트가
확인한 이번 시도에 한정한다. 적용 가능성이 없었던 시도와 미확인 결과를 분리한다.

```json
{"attempt_id":"A-001","session_id":"S-001","applicability":"applicable","outcome":"success","verification_status":"unverified","evidence_ids":[]}
```

```text
python skills/agentic-vault/scripts/vault_lesson_metrics.py --vault PATH record --proposal-id ID --input 00-meta/observation.json
python skills/agentic-vault/scripts/vault_lesson_metrics.py --vault PATH summarize --proposal-id ID
```

관측은 `00-meta/proposals/observations/ID/`에 추가한다. v1 제안 영수증과 대상 노트는
수정하지 않는다. **관측 당시 적용된 영수증과 같은 대상 해시**가 확인된 applicable
시도만 효과율에 반영한다. 나중에 적용해도 과거 pending 시도에 소급 점수를 주지
않는다. unknown 결과는 알려진 결과의 비율 분모에서 제외하고, 분모 0은 null이다.

호스트가 verified를 요청해도 근거 ID가 없거나 파일·검증 기록이 바뀌면 verified로
집계하지 않는다. 근거가 현재 파일에 연결된다는 뜻이며 결과의 진실 인증은 아니다.
조회에서도 근거 현재성을 다시 확인한다. 체크섬·연속 번호는 보통의 손상과 중간
삭제를 탐지하며, 서명이나 마지막 기록 전체 삭제 탐지는 제공하지 않는다.
최대 128관측/제안이다. 중복 시도·동시 기록·변경된 정책·대상은 실패로 처리한다.
승격·확정·롤백은 기존 사용자 승인과 교훈 검토 절차를 따른다.

## 검색과 연속 작업을 따로 평가

```text
python scripts/evaluate_recall.py
python scripts/evaluate_recall.py --fixture tests/fixtures/recall_experience
python scripts/evaluate_recall.py --fixture tests/fixtures/recall_advanced --compare --max-no-answer-false-positive-rate 0 --max-forbidden-exposure-rate 0 --max-stale-top-1-rate 0
python scripts/evaluate_memory_workflows.py --backend deterministic
```

검색 비교는 같은 질문·top 3·예산을 사용한다. 고급 fixture의 명시적 시점·연결 깊이·
외부 후보 옵션만 개선 조건에 전달하며 정답 경로는 검색기에 전달하지 않는다.
`--advanced`는 고급 조건만, `--compare`는 두 조건을 출력하고 개선 조건에 문턱값을
적용한다. recall/MRR은 출처 검색, forbidden/stale은 반환 후보의 노출이다.
정답 없음에서 빈 결과도 해당 fixture 검사 결과이며 세상의 사실 부재 증명이 아니다.

워크플로 실행기는 임시 합성 볼트에서 발견→재시도→다음 재시도 또는 출처 철회를
수행한다. 서비스 시작 전 stale lock, 변경된 보존 기간, 게시 전 checksum 같은
전제·사후 조건을 실제 Python 상태에 적용한다. 잘못된 출처를 실제 격리한 다음
검색의 잔류 경로를 확인한다. 셸·파일 시스템·서비스를 모델에게 실행시키지 않는다.
무기억/기존 검색/시간 검색+구조화 기억의 같은 계획 정책을 비교하고, 발견 세션은
완료율 분모에서 제외한다. 이것은 통제된 기능 실험이며 일반 업무 성능 수치가 아니다.

실제 로컬 모델 계획을 사용하려면 명시적으로 선택한다. 인증 키는 지정한 환경변수로
읽으며 저장·출력하지 않는다. loopback의 지정 API만 허용하고 redirect를 차단한다.
실행되는 행동은 같은 제한된 시뮬레이터뿐이다. 모델 오류·잘못된 JSON은 실패로 남긴다.

```text
python scripts/evaluate_memory_workflows.py --backend local-model --model MODEL_ID --api-key-env LOCAL_LLM_API_KEY --output-tokens 256
```

고정 정책 결과와 모델 실험 결과는 별도로 표시한다. 실제 qmd 임베딩·Graphiti/PPR·
클라우드 모델·공식 대규모 벤치마크 성능은 이 소규모 fixture 테스트로 확정하지 않는다.
