# 출처와 공유 상태 보강

기존 Markdown/Git 노트를 유지하면서 필요한 helper를 명시적으로 선택한다.
모든 결과는 자료이며 실행 권한·사실 확정·검증자 신원을 자동으로 만들지 않는다.
Claude Code는 `/vault-harden`, Codex는 `$agentic-vault:agentic-vault harden`으로 연결한다.
Python 파일은 플러그인의 `skills/agentic-vault/scripts/`에 있다. CLI 입력은 UTF-8 JSON이다.

| 작업 | helper / 진입점 | 결과와 경계 |
|---|---|---|
| 출처 | `vault_provenance.py`: `derive_provenance`, `validate_provenance`, `analyze_notes` | 선택 원본·정제본 해시와 가장 낮은 신뢰를 유지. 별도 근거의 현재성을 조회하며 이름만으로 독립 검증을 인정하지 않음 |
| 공유 상태 | `vault_state.py`: `advisory_lock`, `patch_note`, `project_handoff`, `detect_removed_blockers` | 원본 UTF-8 바이트 범위의 부분 편집, 기대 파일/config 해시, 소유자 토큰과 TTL. 협조하는 쓰기만 잠금 보호 |
| 시간 대장 | `vault_ssot.py`: `parse_ledger`, `check_ledger`, `prepare_migration`, `apply_migration` | 이력·시간 충돌·확정 행 출처 검사. dry-run 후 명시적 적용; 날짜/기업 사실을 추정하지 않음 |
| 교훈 | `vault_lesson_delta.py`: `prepare_delta`, `apply_delta` | ADD/INC/EDIT/RETIRE, 안정적인 ID·횟수·현재 제안 해시. RETIRE는 기록을 보존 |
| 선언 규칙 | `vault_declarative_rules.py`: `evaluate_rules` | literal existence/substitution/occurrence 기본값. regex는 명시적 선택, 별도 프로세스 제한시간·입력 상한 |
| 정제 품질 | `vault_compile_quality.py`: `check_compile` | 숫자·ISO 날짜·구조화 이름/위키 대상·호스트가 지정한 원문 주장 누락. 의미적 정확성과 이름 전체 추출을 보장하지 않음 |
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
