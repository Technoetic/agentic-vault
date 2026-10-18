# 출처와 공유 상태 보강

후속 개발 브랜치의 [Windows 파일 교체와 경로 비교 보강](evaluation-stability.md#windows-부분-편집)은
실제 공유 핸들 거부를 제한적으로 재시도하고 매번 기존 정책·revision 검증을 유지한다.
공개 v0.23.0 설치본의 동작이 이미 변경됐다는 뜻은 아니다.

기존 Markdown/Git 노트를 유지하면서 필요한 helper를 명시적으로 선택한다.
모든 결과는 자료이며 실행 권한·사실 확정·검증자 신원을 자동으로 만들지 않는다.
Claude Code는 `/vault-harden`, Codex는 `$agentic-vault:agentic-vault harden`으로 연결한다.
Python 파일은 플러그인의 `skills/agentic-vault/scripts/`에 있다. CLI 입력은 UTF-8 JSON이다.

| 작업 | helper / 진입점 | 결과와 경계 |
|---|---|---|
| 출처 | `vault_provenance.py`: `derive_provenance`, `validate_provenance`, `analyze_notes` | 선택 원본·정제본 해시와 가장 낮은 신뢰를 유지. 별도 근거의 현재성을 조회하며 이름만으로 독립 검증을 인정하지 않음 |
| 공유 상태 | `vault_state.py`: `advisory_lock`, `patch_note`, `project_handoff`, `detect_removed_blockers` | 원본 UTF-8 바이트 범위의 부분 편집, 기대 파일/config 해시, 소유자 토큰과 TTL. 협조하는 쓰기만 잠금 보호 |
| 시간 대장 | `vault_ssot.py`: `parse_ledger`, `check_ledger`, `prepare_migration`, `apply_migration`, `check_support` | 이력·시간 충돌·확정 행 출처 검사. dry-run 후 명시적 적용; 날짜/기업 사실을 추정하지 않음. 선택형 `check-support`는 값이 인용 노트 본문에 있는지 경고만 함 |
| 교훈 | `vault_lesson_delta.py`: `prepare_delta`, `apply_delta` | ADD/INC/EDIT/RETIRE, 안정적인 ID·횟수·현재 제안 해시. RETIRE는 기록을 보존 |
| 선언 규칙 | `vault_declarative_rules.py`: `evaluate_rules` | literal existence/substitution/occurrence 기본값. regex는 명시적 선택, 별도 프로세스 제한시간·입력 상한 |
| 정제 품질 | `vault_compile_quality.py`: `check_compile` | 숫자·ISO 날짜·구조화 이름/위키 대상·호스트가 지정한 원문 주장 누락. 선택형 역방향은 정제본에만 있는 literal(날조 후보)을 관측. 의미적 정확성과 이름 전체 추출을 보장하지 않음 |
| 토큰 | `vault_token_calibration.py`: `calibrate`, `estimate`, `check_drift`, `measure_local`, `measure_counter` | 모델/세대별 측정치와 보정 해시. 명시적으로 선택할 때만 사용; 기존 hook/검색 계수 유지 |
| 외부 전송 경고 | `vault_egress.py`: `scan_text`, `analyze_notes` | D1 정규화로 가짜 토큰·한국 식별번호 패턴 검사. 일치한 비밀값을 출력하지 않음; 자동 외부 전송 없음 |

## 원본을 옮기기 전

허용된 원본과 정제본 각각의 전체 바이트 SHA-256을 구한다. 다음 형식으로 정제 품질 CLI에 전달한다.

```json
{"sources":[{"path":"10-inbox/web/source.md","sha256":"<64 lower-case hex>"}],"targets":[{"path":"20-knowledge/references/result.md","sha256":"<64 lower-case hex>"}],"required_claims":["원문에 실제 존재하는 필수 문장"]}
```

`python <플러그인 루트>/skills/agentic-vault/scripts/vault_compile_quality.py --vault <볼트>`의 stdin으로 넣는다.
exit0은 선택 literal이 보존됐다는 뜻이다. exit1은 누락 또는 검사할 literal 부재, exit2는 경로·입력·해시 오류다.
그 결과로 의미적 완전성이나 격리 권한을 추정하지 않는다. 날짜·수치·인명 전체와 조건/불확실성을 호스트가 대조한다.
한국어 이름 경계는 보수적으로 확인한다. 이름 뒤 조사가 붙은 서술은 별도 이름 label 또는 명시적인 원문 주장으로 확인하며 자동 NER로 판정했다고 표시하지 않는다.
지원되는 의미 평가는 원본의 허용된 발췌를 기존 `vault_judge.py`에 바인딩하여 승인된 Jev 경로로 확인한다.
deny/excluded 자료를 인라인 문자열로 바꿔 외부 전송 제한을 우회하지 않는다.

`derive_provenance`로 이동 전 source/target 해시와 origin을 기록한다. 원문이 나중에 deny zone으로 옮겨졌다면 이후 검사에서
그 원문을 다시 읽지 않는다. 현재성을 다시 확인할 수 없으면 unresolved로 남긴다. 출처 기록은 원문 접근 권한을 확장하지 않는다.
검사 사이 또는 이동 직전 파일이 바뀌면 새 해시와 결과로 다시 검토한다. multi-file 해시 검사는 원자적인 파일시스템 스냅샷이 아니다.

## 날조 후보·출처 뒷받침 경고 (선택)

두 검사는 정밀도 방향의 관측이다. 기존 회수율 방향 검사(원문 literal이 정제본에 남았는가)와 `check` 경고는 그대로 두고,
호출자가 명시적으로 선택할 때만 실행한다. 결과는 모두 비차단 관측(경고)이며 사실 판정·격리 권한·출처 진위 증명이 아니다.
선택하지 않으면 기본 인자·CLI 출력·exit code가 v0.22.1과 같다. 테스트가 v0.22.1(`63b4cc9`)에서 캡처한 출력과 비교한다.
방법은 [HaluMem](https://arxiv.org/abs/2511.03506)(기억 환각을 추출·갱신·질의응답 단계별로 측정)과
[MemTxn](https://arxiv.org/abs/2607.27834)(갱신이 원천에 의해 뒷받침되는지 보는 경계)을 참고했다.
방법 참고일 뿐 두 논문의 코드·데이터·수치는 포함하지 않는다. HaluMem 데이터(CC-BY-NC-ND-4.0)는 이 저장소에 없다.

### 정제본에만 있는 literal: `check_compile` 역방향

정제본(target)에는 있지만 선택한 원본(source) 어디에도 없는 숫자·ISO 날짜·이름 literal을 날조 후보로 나열한다.
추출 범위·일치 규칙·파일/바이트/fact 상한은 정방향과 같다. 숫자·날짜는 추출 token끼리 비교하고, 이름은 정방향과 같은
보수적 단어 경계로 원본 본문에서 찾는다. frontmatter는 양쪽 모두 제외한다. 호스트 지정 주장(`required_claims`)은 원본 쪽 항목이라 대상이 아니다.

API는 `check_compile(vault, sources, targets, required_claims, report_unsupported=True)`다.
CLI는 위 JSON 요청을 그대로 stdin으로 넣고 `--report-unsupported`를 더한다.

```
python <플러그인 루트>/skills/agentic-vault/scripts/vault_compile_quality.py --vault <볼트> --report-unsupported
```

`status`·`review_reason`·exit code(0/1/2)의 의미는 바뀌지 않는다. 역방향 결과만으로 needs_review가 되거나 원본 격리가 막히지 않는다.
기존 필드는 그대로 두고 다음 필드만 더한다.

| 필드 | 뜻 |
|---|---|
| `target_literals_checked` | 선택한 모든 정제본에서 뽑은 고유 `(kind, literal)` 수 |
| `unsupported_target_literals` | 원본에 없는 literal 목록. 항목마다 `kind`(number/date/name), 정규화한 `literal`, `target_paths` |
| `unsupported_by_kind` | 위 목록의 kind별 개수 |
| `fabrication_candidate_rate` | 위 목록 수 ÷ `target_literals_checked`. 분모가 0이면 `null`. 이번 호출에서 선택한 파일만의 비율이며 표본 추정치가 아니다 |
| `fabrication_scope` | 범위와 한계를 적은 고정 문자열 |

### 대장 값의 출처 노트 대조: `vault_ssot.py check-support`

시간 대장 행의 `value`가 같은 행 `source` 칸이 가리키는 노트 본문에 실제로 있는지 본다.

```
python <플러그인 루트>/skills/agentic-vault/scripts/vault_ssot.py --vault <볼트> check-support --path <대장.md>
```

경고만 있으면 exit 0이다. 대장 경로가 deny/exclude/runtime 구역이거나 Markdown이 아니거나, 검사 중 설정·대장·출처 노트가 바뀌면
기존 `check`와 같이 stdout 없이 stderr에 `{"error":"ssot_operation_unavailable"}`를 쓰고 exit 1이다. API는 `check_support(vault, path)`다.

참조는 다음만 해석한다.

- `[[이름]]`, `[[이름#제목|별칭]]`, `![[이름]]`: 허용된 노트 가운데 파일명(`.md` 제외, NFC 정규화, 대소문자 구분)이 정확히 같은 노트가 하나일 때만 해석한다. 둘 이상이면 고르지 않는다.
- `[[폴더/이름]]`, `` `폴더/이름.md` ``, 칸 전체가 공백 없는 `.md` 경로 하나: 볼트 루트 기준 정확한 경로다. `./`·`../`로 시작하면 대장 위치 기준이다.
- `[[#제목]]`과 대장 자신을 가리키는 링크: 대장에서 사실 표를 뺀 나머지 본문과 비교한다. 행이 자기 자신을 뒷받침하지 않게 한다.
- `[[보고서.pdf]]`처럼 첨부로 보이는 이름은 같은 이름의 노트가 없으면 건너뛴다. URL·서술 문장·공백이 든 일반 경로는 참조로 보지 않는다.
- deny·exclude·runtime 구역과 `.git` 안으로는 들어가지 않는다(목록·읽기·후보 표시 없음). 구역 경로를 직접 쓴 참조는 `policy`로만 표시한다.
- symlink·reparse point·hardlink 노트와 256 KiB(대장과 같은 상한) 초과 노트는 읽지 않는다. 읽은 노트는 끝에 대장·설정과 함께 다시 확인한다.

값과 노트 본문(frontmatter 제외)을 NFKC·casefold·공백 정규화해 비교한다. 쓴 그대로의 형태와, 위키링크를 대상 이름으로 바꾸고
인라인 코드·굵게 표시를 지운 형태를 함께 본다. 노트 본문은 위키링크를 별칭으로 바꾼 형태도 본다.
값의 처음·끝이 ASCII 영숫자인데 바로 옆에 ASCII 영숫자가 붙으면 다른 literal로 본다. 숫자 앞뒤에 `.`·`,`와 숫자가 이어져도 다른 수로 본다
(`12`는 `125`·`1,212`·`12.5`에서 찾지 않는다). 출처 노트 중 하나라도 값을 포함하면 그 행은 뒷받침된 것으로 센다.

| 경고 코드 | 뜻 | `details` |
|---|---|---|
| `ssot-source-unsupported` | 해석된 출처 노트 어디에도 값이 없다 | `cited`: 비교한 노트 경로 |
| `ssot-source-unresolved` | 참조를 허용된 노트 하나로 해석하거나 읽지 못했다 | `reference`(대장에 쓴 그대로), `reason`, 모호하면 `candidate_count`·`candidates`(최대 16개) |

`reason`은 `not_found`, `ambiguous`, `policy`(deny/exclude/runtime 구역·symlink 경로), `size_limit`, `unsafe_file`(hardlink 등 단일 정규 파일 아님),
`unreadable`(UTF-8 아님·읽는 중 변경), `invalid_reference`, `inventory_incomplete`, `source_limit` 중 하나다.
나머지 출력 필드는 `path`, `source_sha256`(대장), `rows_checked`·`rows_supported`·`rows_unsupported`·`rows_unresolved`·`rows_skipped`,
`cited_notes`(비교한 노트의 `path`·`sha256`·`bytes`), `incomplete`(상한 때문에 덜 본 이유), `method`, `authority`다.
값이나 출처가 없는 행, 노트 참조가 없는 행, 열 이름이 중복된 표의 행은 `rows_skipped`로 세고 경고하지 않는다.
그런 행은 기존 `check`가 그대로 다룬다(예: `ssot-confirmed-source-missing`). `check` 출력과 lint 경고는 바뀌지 않는다.
상한(코드 상수, 측정값 아님): 출처 노트 128개, 출처 읽기 합계 8 MiB, 행당 참조 8개, 디렉터리 항목 20,000개, 경고 1,024건.
앞의 넷을 넘으면 `incomplete`에 이유를 남기고, 경고 수 상한은 기존 `check`처럼 오류로 끝낸다.

### 경고 우선 원칙

두 검사는 경고로만 시작한다. 차단(fail-closed) 승격은 실제 무해 자료의 benign replay에서 오탐 0건을 확인하고 사람이 승인한 뒤에만 검토한다.
이번 버전의 두 경고 코드는 lint `WARNING_CODES`·`PROMOTABLE_CODES`에 없어 `warning_policy.levels`나 인증서로 승격할 수 없다.
`fabrication_candidate_rate`도 차단 기준이 아니다. 경고 수나 비율만 보고 정제본·대장을 고치거나 원본을 옮기지 않는다.

### 한계

- literal은 진실이 아니다. 원본에 있는 값이라도 맞는 사실이라는 보장이 없고, 원본에 없는 값이 곧 틀린 값도 아니다.
- 바꿔 쓰기, 단위·형식 차이(`125,000`과 `125000`, `2026-10-07`과 `2026.10.07`, `12개월`과 `1년`)는 놓침과 오탐을 모두 만든다.
- 이름은 구조화 label·위키 대상만 다루는 literal 비교이며 언어학적 NER이 아니다. 조사가 붙은 이름(`홍길동은`)은 다른 literal로 보아 오탐이 되고,
  정제본의 개념 위키링크도 원문에 없는 이름으로 잡히기 쉽다.
- `check-support`는 값이 출처 본문 어딘가에 있는지만 본다. 같은 주체·관계·기간에 대한 서술인지, 부정문인지는 판단하지 않는다.
  행의 상태(확정·기각 등)와 관계없이 모든 행을 같은 방식으로 본다.
- 사실 표를 빼는 것은 대장이 자기 자신을 인용할 때뿐이다. 인용한 노트가 다른 대장이면 그 대장의 사실 표도 본문으로
  비교하므로, 두 대장이 서로를 출처로 인용하면 원문 근거 없이 서로의 행을 뒷받침하는 순환이 생길 수 있다.
- 경계 규칙은 값의 처음·끝 글자가 ASCII 영숫자일 때만 적용된다. `-5`처럼 부호로 시작하는 값은 앞쪽 경계를 보지 않아
  `10-5` 같은 범위 표기 안에서도 일치로 센다.
- 경로 없이 쓴 `[[이름]]`이 deny·exclude·runtime 구역에만 있으면 구역을 목록에 넣지 않으므로 `not_found`로 보고한다.
  구역 안 노트의 존재를 드러내지 않기 위한 동작이며 출처가 실제로 없다는 뜻이 아니다. 구역 경로를 직접 쓴 참조만 `policy`다.
- 끝의 재확인에서 대장은 SHA-256으로 다시 비교하지만 인용 노트는 stat(장치·inode·크기·수정 시각·링크 수, Windows가 아니면
  변경 시각 포함)만 비교한다. 크기와 수정 시각이 그대로인 채 바뀐 인용 노트는 놓칠 수 있다.
- frontmatter·첨부(PDF 등)·deny 구역 원문과는 비교하지 않는다. 값과 노트가 서로 다른 별칭·표기를 쓰면 놓친다.
- 해석은 파일명 정확 일치다. Obsidian의 대소문자 무시·별칭 해석과 결과가 다를 수 있다.
- 실제 볼트에서의 오탐률·놓침률은 측정하지 않았다(2026-10-17 기준). 근거는 단위 테스트의 합성 자료뿐이다.

## 수집과 경고

Jarvis 수집은 `captured_via`, `content_origin`, `captured_at`, `body_sha256`을 호스트가 만든다.
직접 작성한 메모는 own, 전달 자료는 forwarded, 호스트가 선택한 URL 수집은 url이다. 링크를 포함한 직접 메모 전체를 외부 자료로 낮추지 않는다.
파생 노트의 manifest 참조는 `provenance_manifest: "00-meta/provenance/<ID>.json"`으로 둘 수 있다.
기본 frontmatter 상한을 넘기지 않도록 긴 manifest는 별도 JSON으로 보존한다. `verified_by` 문자열은 증명이 아니다.
Git staged 검사는 index 원문만 사용한다. JSON 근거를 직접 확인할 수 없는 privileged 파생 자료는
`provenance-unresolved`로 표시한다. 이것을 근거가 없다는 사실 판정이나 치명 승격으로 사용하지 않는다.
별도 CLI 검증은 명시한 허용 원본과 현재 근거를 확인한다.

링크 anchor·중복 이름·날짜와 추가 출처/대장/전송 발견은 경고로 시작한다. `warning_policy.levels`로 승격하려면
`vault_warning_policy.py --certificate`의 현재 checker/corpus 해시와 실제 benign replay 0 FP를 함께 사용한다.
불완전 스캔·미검증 출처는 치명 승격할 수 없다. 코드·줄바꿈·corpus가 달라지면 기존 인증서는 재사용하지 않는다.

## 부분 편집과 checkpoint

`patch_note(vault,path,expected_sha256,[{"start":0,"end":0,"replacement":"..."}], expected_config_sha256=...)`의
start/end는 원본 UTF-8 **바이트**의 반열린 범위다. untouched 바이트와 LF/CRLF는 유지한다.
다른 파일 근거는 `source_hashes={"allowed/note.md":"<sha256>"}`로 함께 바인딩한다.
오래된 파일/config·잠금 소유자 변경은 적용하지 않는다. 잠금은 타 프로세스의 비협조 편집까지 막지 않는다.
유효 시간이 지난 PID에 `os.kill(pid,0)`을 호출하지 않는다. 중단된 guard는 안전한 수동 복구가 필요할 수 있다.

PreCompact/SessionEnd는 모델 호출 없이 `00-meta/.agentic-vault/runtime/`에 사건·원본 해시·명시한 pending ID만 기록한다.
hot/tasks/handoff를 자동 작성하거나 완료로 바꾸지 않는다. runtime은 Git ignore 및 일반 노트 inventory에서 제외한다.
내부 runtime 쓰기는 inventory exclude의 예외이며 deny 정책은 계속 적용한다.
compact SessionStart는 고정 제약 발췌/현재 source 해시를 먼저 유지한다. 제약 블록 전체가 예산에 맞지 않으면 내용 없이 진단한다.
선택한 최대 8개 blocker 발췌와 Now/anchor의 범위를 넘어 전체 계약을 주입했다는 의미는 아니다.
전체 출력 상한은 9,500 UTF-16 단위이며 기존 0 예산과 startup/clear/resume 동작을 유지한다.
Codex hook 지원은 [공식 설명](https://learn.chatgpt.com/docs/hooks)을 참고한다. 실제 정의 신뢰를 자동 승인하지 않는다.

## 측정과 보정

`calibrate(samples,model_id,generation)`은 서로 다른 sample hash의 측정치 최소2개를 받아 두 종류의 문자 계수를 적합한다.
sample은 `text_sha256`, `hangul_chars`, `other_chars`, `tokens`, `model_id`, `generation`, `measurement_origin`을 가진다.
origin은 reported_sample/local_tokenizer/host_counter이며 **호출자가 보고한 라벨 자체는 독립 증명이 아니다**.
랭크 부족·비정상 계수·bool/음수/nonfinite 수치·세대 불일치를 거절한다. calibration hash는 수정/오용 감지이며 서명이 아니다.

CLI `--action calibrate|drift|estimate|measure-local`에 해당 API 인자 JSON을 전달한다.
`estimate`에 calibration/model_id/generation을 모두 명시할 때만 보정하며 기본 예산 추정은 바꾸지 않는다.
`measure_local`은 선택 의존성인 [실제 tiktoken](https://github.com/openai/tiktoken)을 호출한다.
인코딩 최초 사용에는 공개 BPE 캐시 다운로드가 필요할 수 있다. tokenizer 버전/encoding을 generation 라벨로 명시하고
서비스가 보고한 API 토큰 수와 구분한다. 의존성이 없으면 unavailable로 보고한다.
`measure_counter`는 호스트가 승인된 실제 count API callable을 넘기고 `allow_network=True`를 명시해야 한다.
엔진이 제공자 endpoint/키를 추측하지 않으며 민감한 입력을 보내지 않는다. count API를 실행하지 않았다면 실행했다고 보고하지 않는다.

## 검색 평가

`vault_recall.py --expand-query [--query-mapping <볼트 상대 경로>]`는 명시적 검색 확장이다.
alias와 mapping은 검색어만 보강하고 위키링크의 실제 파일명을 바꾸지 않는다. 애매한 목적지는 선택하지 않는다.
mapping은 `trigger => replacement`의 평문이며 원본 경로/해시와 inventory 세대 검사를 보고한다.
inventory 전체의 stat 세대 검사는 metadata-preserving rewrite를 잡는 암호학적 스냅샷이 아니다.
`scripts/evaluate_recall.py --help`의 hard fixture/top-k/같은 예산별 ablation을 사용한다.
반환 context claim coverage, nDCG, paired 통계와 abstention을 함께 본다. synthetic label 성능을 실제 업무 정확도로 일반화하지 않는다.
