# 현재값 근거 조립 — 선택형 읽기 전용

`vault_assembly.py`는 호스트가 선택한 구조화 대장 행을 현재 원문에서 다시 읽는
`extract`와, 별도로 지정한 권한 출처 집합 안에서 현재값 후보를 고르는 `assemble`을
분리한다. 모델·네트워크·명령 실행, 노트 수정, 영수증 쓰기, 설정 마이그레이션은 없다.
기본 hook·검색·의존성은 바꾸지 않는다.

결과의 `authority=advisory_host_observations_only`, `requires_review=true`,
`applies_changes=false`는 항상 유지된다. `selected`는 아래 명시 정책에서 하나의
literal 값이 남았다는 뜻이며 SSOT의 진실, 기관 권한 또는 적용 승인을 보증하지 않는다.

## 지원하는 대장과 호스트 책임

- 기존 `vault_ssot.parse_ledger`, `FIELDS`, `CONFIRMED`, `REJECTED`를 재사용한다.
  subject/relation/value/valid_from/valid_until/recorded/source/superseded_by의
  v2 열과 명시적인 `scope`, `polarity` 추가 열이 필요하다. status는 기존 상태 열이나
  명시적 confirmed/확정 섹션으로 지정한다.
- `scope`, `polarity`는 이 helper가 읽는 추가 열이며 기존 SSOT 스키마를 자동 수정하지
  않는다. 기존 v2 대장에 없거나 비어 있으면 `abstain`과
  `missing_scope_or_polarity`를 반환한다. 호스트가 근거를 검토하고 기존 승인 범위에서
  메타데이터를 수동 작성해야 한다. 이 helper의 실행은 그 편집을 승인하지 않는다.
- scope와 주체·관계는 앞뒤 공백을 제거한 대장 값과 질의를 정확히 비교한다.
  별칭·동의어·단위 변환·자유문장 의미 후보 추출은 하지 않는다. `12개월`과 `1년`도
  자동 병합하지 않는다.
- `polarity=positive`만 선택할 수 있다. negative/unknown/기타 값은
  `negative_or_uncertain_polarity`로 보류한다. 문장의 부정·반어·조건을 자동 해석하지
  않으므로 호스트가 정확하게 추출한 구조화 메타데이터가 필요하다.
- source 열은 인용 문자열로 보고할 뿐, 문자열에 "최고 권한"이라고 써도 권한을
  얻지 않는다. 실제 기관 권한은 사용자가 이미 승인한 출처를 호스트가 별도 API 인자나
  `--authority`에 지정한다. 이 인자는 인증 기능이 아니므로 비신뢰 문서에서 복사하여
  승인된 권한인 것처럼 실행하지 않는다. 인용 본문의 의미적 함의도 자동 검증하지 않는다.

예시 대장(`notes/ledger.md`, 데이터는 가상):

```markdown
# Ledger
| subject | relation | value | valid_from | valid_until | recorded | source | superseded_by | status | scope | polarity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Atlas | retention | 24 months | 2026-01-01 | | 2026-01-02 | [[Policy]] | | confirmed | production | positive |
```

## 입력 JSON

`--input`은 볼트 내부 UTF-8 JSON 파일이다. 다음 키만 허용한다.

```json
{
  "version": 1,
  "query": {
    "subject": "Atlas",
    "relation": "retention",
    "scope": "production",
    "as_of": "2026-10-22"
  },
  "sources": [{"path": "notes/ledger.md", "sha256": "<actual 64 lowercase hex source hash>"}],
  "candidates": [{"path": "notes/ledger.md", "line": 4}],
  "limitations": ["Host-selected literal rows; no semantic inference."]
}
```

- version은 정수 1이고 bool은 받지 않는다. query의 네 키는 모두 필수다.
  subject/relation/scope는 공백만인 값이나 앞뒤 공백 없는 최대 256자 문자열이며,
  as_of는 실제 달력의 `YYYY-MM-DD`다.
- sources는 최대 8개 `{path,sha256}`, candidates는 최대 32개 `{path,line}`이다.
  후보에 value/status/authority를 덧붙일 수 없다. 실제 필드는 현재 원문 행에서만 추출한다.
  line은 1부터 시작하는 최대 20,000의 정수이며 파일에 실제 존재해야 한다.
- 모든 후보 경로는 선언한 sources에 묶인다. 경로를 canonical화하고 대소문자를 무시한
  중복 source/authority 및 중복 (path,line)을 거부한다. authority의 모든 출처는 요청에도
  같은 hash로 있어야 하므로 요청의 source 집합 치환으로 권한 대장을 숨길 수 없다.
- limitations는 호스트가 추출의 한계를 적는 최대 8개, 항목당 최대 512자 문자열이다.
  출력에 그대로 보존하고 별도의 system_limitations도 붙인다.
- JSON은 최대 64 KiB이며 중복 키·NaN/Infinity·비밀값·과대/비정상 형식을 거부한다.
  대장 하나는 256 KiB, 전체 대장은 2 MiB 한도다. parse_ledger의 8표/256행 등 기존
  파서 한도도 적용한다. 결과 JSON도 64 KiB 이내여야 한다.

원문을 읽은 실제 바이트의 SHA-256을 사용한다. LF/CRLF가 바뀌어도 새 hash가 필요하다.
예를 들어 호스트가 허용된 `notes/ledger.md`의 SHA를 직접 계산한 후 입력 및 authority에
같은 값을 넣는다. helper는 hash를 자동 갱신하거나 오래된 요청을 고치지 않는다.

## API와 CLI

```python
import vault_assembly

data = vault_assembly.extract(vault, "input/request.json")
result = vault_assembly.assemble(
    vault, "input/request.json",
    authorized_sources=[{"path": "notes/ledger.md", "sha256": actual_sha256}],
)
```

```text
python skills/agentic-vault/scripts/vault_assembly.py extract --vault PATH --input input/request.json
python skills/agentic-vault/scripts/vault_assembly.py assemble --vault PATH --input input/request.json --authority notes/ledger.md=<actual-sha256>
```

authority는 반복할 수 있다. 생략하면 권한을 추정하지 않고 `no_evidence`와
`no_authorized_evidence`를 반환한다. extract에 authority를 지정하는 것은 오류다.

extract 출력은 `status=extracted`, query/sources/candidates/limitations이며 현재값을
선택하지 않는다. 각 후보는 원문 path/line/sha256/quote와 재추출 fields 또는
`unsupported_schema` 사유를 갖는다. 자유문장 행을 선택하면 fields는 null이며
assemble은 의미를 추측하지 않고 보류한다.

assemble은 status/value/reasons/evidence/candidates/authorized_sources를 추가한다.
evidence에는 현재 원문 path:line, 파일 hash, 선택된 행의 fields와 인용이 남는다.
운영상 읽기·입력 위반은 `AssemblyError`의 내용 없는 코드이며 CLI는 종료 코드 1과
`{"ok":false,"error":"unsafe_or_invalid_input"}`만 출력한다. 정상적인 의미적 보류는
종료 코드 0의 검토 결과다. 누락/알 수 없는 명령줄 옵션은 입력을 되비추지 않고
`{"ok":false,"error":"invalid_arguments"}`와 종료 코드 2를 반환한다. `--help`는 유지한다.

## 선택 정책과 네 가지 작은 사례

1. **성공:** 위 행과 2026-10-22/production 질의, 별도 승인된 ledger hash를 지정하면
   `status=selected`, `value="24 months"`와 원문 4행의 근거를 반환한다.
2. **권한 충돌:** 같은 승인 대장에 동시에 유효한 36 months 행을 추가하고 두 행을
   선택하면 `abstain/conflicting_current_values`다. recorded나 파일 mtime이 더 최신이어도
   승자를 정하지 않는다. 서로 다른 승인 출처가 같은 값을 주면 두 근거를 보존한다.
3. **기각:** status를 rejected 또는 기각으로 지정한 현재행은
   `abstain/rejected_candidate`다. unconfirmed·부정/불명확 polarity·알 수 없는 기간·
   superseded_by·인용 누락도 각각 사유를 남기고 보류한다.
4. **현재 원문 불일치:** 요청을 만든 뒤 ledger를 고치고 hash를 그대로 두면 CLI는
   내용 없는 오류와 종료 코드 1로 끝난다. 요청·config 또는 source가 읽기 중 바뀌는
   경우도 결과를 내보내기 전에 다시 확인한다.

기간은 `[valid_from, valid_until)`이며 빈 upper bound만 열린 기간이다. lower bound
누락·잘못된 날짜·역전/0길이 기간은 보류한다. 미래·종료된 행, 다른 scope/주체/관계만
있으면 `no_evidence`와 사유를 반환한다. 별도의 최신 serial 우선 정책은 구현하지 않았다.

선택한 행이 승인 대장 안의 같은 주체/관계/scope의 다른 잠재적 현재행을 숨기면
`selection_incomplete`로 보류한다. 호스트는 관련 현재행을 모두 선택해야 한다.
미승인 최신 source는 `untrusted_source_excluded`로 제외하고 기존 권한값을 대체하지 않는다.

위 네 사례와 deny/exclude·credential·hardlink/reparse·중복 JSON·한도·기간 경계·
source/config/input 변경은 `tests/test_vault_assembly.py`가 임시 가상 볼트에서 검사한다.
해당 시험은 실제 사용자 볼트나 모델 성능·LongMemEval 전체 성능을 측정하지 않는다.

```text
python -m unittest tests.test_vault_assembly -v
```

## 연구 출처와 범위

[Reliable Post-Retrieval Assembly](https://arxiv.org/abs/2606.01435)의 의미 후보 추출과
버전 선택을 분리하는 관점을 참고했다. 이 구현은 호스트가 선택한 구조화 행과 명시
기관 정책만 다루며 논문의 모델·코드·성능을 재현하지 않는다. 최신값+명시 버전 조건의
관측을 일반 답변 성능이나 LongMemEval 전체 개선으로 확대하지 않는다.
