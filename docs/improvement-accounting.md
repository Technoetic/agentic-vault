# 평가기 식별과 개선 비용 근거

미릴리스 선택 기능이다. 기존 `vault_runs.py`에 평가기 파일을 선택해 연결하고,
별도 `vault_run_costs.py`에 호스트가 준비한 사용량 보고서를 기록한다. 기본 세션 훅,
기존 실행 ID, 모델·네트워크 호출, 교훈 적용·승격 절차는 바꾸지 않는다.

## 평가기도 비교 조건으로 고정

기존 `runs prepare` 인자에 다음을 더한다.

```text
--evaluator-source evaluation/grader.py --evaluator-source evaluation/support.py --evaluator-config evaluation/config.json
```

`--evaluator-source`는 한 번 이상 지정하며 `--evaluator-config`는 생략할 수 있다.
설정은 JSON 객체다. 파일은 기존 경로 정책에서 허용한 볼트 상대경로의 UTF-8
스냅샷이다. 볼트 밖 실제 채점기를 사용한다면 호스트가 그 버전의 사본을 별도로 보존한다.
이 도구는 선택한 코드나 설정을 실행하지 않는다.

Python API는 `prepare(..., evaluator_sources=[...], evaluator_config=...)`다.
두 인자를 생략하면 기존 manifest와 ID가 그대로다. 지정하면 `manifest.evaluator`에
정렬된 `sources` 지문과 선택적 `config` 지문을 담는다. 지문은 `path`, `sha256`, `size`다.

- 코드·설정이 바뀌면 기존 실행의 기록·조회·비교를 거부한다.
- 다른 평가기, 다른 설정 또는 평가기 지정 여부가 다른 실행은 서로 비교할 수 없다.
- 평가기는 후보·지시문·시험·도구 계약·관찰 보고서·관찰 근거와 경로를 공유하지 않는다.
- 평가기 선택은 설정 포함 최대 64파일, 파일당 256KiB, JSON 설정은 64KiB다.
  평가기를 지정한 실행의 선택 입력 전체는 2MiB 이내다. 기존 비밀·금지·링크 경로 규칙도 적용한다.

이 검사는 선택한 바이트의 현재성을 확인한다. **그 코드가 실제로 실행됐는지,
채점이 옳은지, 로그가 사실인지, 평가자가 독립적인지는 인증하지 않는다.**

## 비용 보고서

호스트가 공급자 사용량·실행기 로그·업무 기록에서 필요한 값만 골라 별도 보고서를 만든다.
도구는 공급자 계정·청구 시스템에 접속하거나 임의 명령을 실행하지 않는다.

아래는 생성 단계만 아는 **불완전한 보고서**다. `RUN_ID`는 실제 준비 실행의 64자리 ID로 바꾼다.

```json
{
  "version": 1,
  "run_id": "RUN_ID",
  "scope_complete": false,
  "entries": [{
    "id": "generation-attempt-1",
    "phase": "generation",
    "status": "discarded",
    "observation": "host_report",
    "recorder": "host-usage-export",
    "evidence": ["evaluation/usage/attempt-1.json"],
    "metrics": {
      "input_tokens": 100,
      "output_tokens": 20,
      "calls": 1,
      "elapsed_ms": 1500,
      "cost_microusd": 80
    },
    "cost_basis": "estimated"
  }]
}
```

| 필드 | 의미 |
|---|---|
| `phase` | `generation`, `evaluation`, `selection`, `recording` 중 하나. 후보 생성·시험·선택·기록을 구별한다 |
| `status` | `complete`, `error`, `discarded`. 오류·버린 시도도 합산한다 |
| `observation` | `instrument_log`, `host_report`, `unknown`. **호출자가 선언한** 근거 종류이며 계측 인증이 아니다 |
| `recorder` | 기록 주체/실행기의 안전한 라벨. 신원 인증이 아니다 |
| `metrics` | 위 다섯 키를 모두 사용한다. 값은 0 이상 10^15 이하 정수 또는 `null`. boolean·실수는 거부한다 |
| `cost_microusd` | 백만분의 1 미국 달러. 원화 등 다른 통화를 그대로 넣지 않는다 |
| `cost_basis` | `billed`(청구액), `estimated`(가격표 등으로 추산), `unknown`. 비용이 `null`이면 `unknown`이다 |
| `scope_complete` | 선택한 실행의 전체 개선 과정을 빠짐없이 보고했다는 호스트 선언. 숨은 시도까지 자동 탐지하지 않는다 |

알 수 없는 관찰은 `observation: "unknown"`, 모든 metric을 `null`, `evidence: []`,
`cost_basis: "unknown"`으로 남긴다. 알려진 관찰은 명시적 근거 파일이 필요하다.
일부 수치만 모르면 해당 metric만 `null`로 남길 수 있다. **미측정은 0이 아니다.**

보고서는 최대 64KiB·64항목이며 ID는 고유해야 한다. 근거는 UTF-8 텍스트이며 기존
native 실행과 같은 금지 경로·비밀 규칙으로 읽는다. 고유 근거 파일은 전체 최대 64개,
근거 한 개는 1MiB, 보고서와 선택 근거를 합한 크기는 2MiB 이내다.
보고서·후보·지시문·시험·도구·평가기 파일을 비용 근거로 재사용하지 않는다.
동일한 근거 경로를 여러 항목에서 쓰는 것도 거부하므로 한 사용량 파일에 여러 단계가
있다면 호스트가 중복되지 않는 이벤트 사본으로 분리해야 한다.

## 기록·조회·합산·비교

```text
python skills/agentic-vault/scripts/vault_run_costs.py --vault PATH record RUN_ID --report evaluation/cost-report.json
python skills/agentic-vault/scripts/vault_run_costs.py --vault PATH inspect COST_ID
python skills/agentic-vault/scripts/vault_run_costs.py --vault PATH summarize COST_ID_1 COST_ID_2
python skills/agentic-vault/scripts/vault_run_costs.py --vault PATH compare BASELINE_COST_ID CANDIDATE_COST_ID
```

`record`는 준비 실행 영수증·보고서·근거를 바인딩한 불변 영수증을 만든다. 아직 시험이
완료되지 않았어도 비용은 남길 수 있다. 보고서를 고치면 별도 파일로 보존하고 새 비용
영수증을 만든다. 원문을 덮어쓰면 이전 영수증의 현재성 검사가 실패한다.

`inspect`와 `summarize`는 읽기 전용이다. 보고서와 근거를 다시 확인하고 정규화 데이터를
재계산한다. 합산은 명시한 최대 64개 비용 ID만 읽으며 전체 저장소를 스캔하지 않는다.
같은 실행의 여러 영수증이나 같은 근거 경로를 중복 합산하지 않는다. 파일을 다른 이름으로
복사한 중복·보고하지 않은 시도까지 발견한다는 보장은 없다.

- `totals`: 하나라도 모르는 값이 있으면 해당 metric 합계는 `null`.
- `known_subtotals`, `unknown_counts`: 알려진 부분합과 모르는 항목 수.
- `missing_phases`, `unknown_observations`, `full_cost_available`: 누락 단계와 보고 완결성.
- `metering`, `scope_authority`: 계측 종류와 완결성은 호출자 설명/선언이라는 한계.

`compare`는 기존 native 성능 비교를 호출하므로 **성능 비교 영수증을 기록한다**.
기존 완료·동일 조건·평가기·hard 실패 판정을 유지한다. 자원 차이는 두 보고서가
전체 범위·네 단계·모든 수치를 갖고, 비용 근거도 모두 동일한 `billed` 또는
`estimated`일 때만 `reported_deltas`에 낸다. 아니면 차이는 `null`이고 사유를 남긴다.
**성능의 `eligible: false`와 자원 차이의 계산 가능 여부는 별개다.** 비용이 더 낮아도
hard 실패를 상쇄하거나 적용을 승인하지 않는다.

이 보고서는 비용 효과를 자동 판정하지 않는다. 청구액과 추산액을 섞어 절감률을
만들지 않으며, 선언한 `token_budget`가 실제 사용량이나 실행 상한 강제를 뜻하지 않는다.
`instrument_log`도 호스트가 제출한 근거 종류다. 해시를 붙였다고 직접 계측으로 바뀌지 않는다.

## 오프라인 예제

```text
python scripts/evaluate_improvement_accounting.py
```

임시 합성 볼트에서 네 후보에 같은 평가기를 연결하고, 버린 시도의 비용 합산,
미확인 비용의 `null` 전파, 전체/불완전 비용 비교, 평가기 변경 거부를 확인한다.
관찰과 사용량은 **합성 호스트 보고서**다. 실제 모델의 정확도·업무 효과·절감액이나
재귀적 자기 개선을 측정한 결과가 아니다.

설계 참고: [DGM v3](https://arxiv.org/pdf/2505.22954v3)의 평가 우회 사례와 탐색 비용,
[AIDE²](https://arxiv.org/pdf/2609.26457v1)의 개선 실험 평가 조건. 코드·모델을 가져온 것은 아니다.
