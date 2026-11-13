# vault-upgrade: 보강 helper bundle과 선택 이행, 0.9.0 안내 목록

> 원본은 저장소 `commands/vault-upgrade.md` 2단계의 '보강 helper bundle과 선택 이행' 절과 그 뒤의 `0.9.0` 안내 목록이고(2단계의 검증 두 줄은 스킬 본문에 남겼다), Antigravity 스킬 본문 한도 때문에 `scripts/build_antigravity_package.py`가 이 파일로 옮겼다. 이 파일을 직접 고치지 않는다. `<PLUGIN_ROOT>`는 이 플러그인 설치 폴더의 절대 경로로, 이 파일이 있는 폴더의 세 단계 위 폴더다.

### 보강 helper bundle과 선택 이행

새 standalone 검사기 설치/교체 때 같은 플러그인의 `skills/agentic-vault/scripts/`에서
`vault_*.py`·`jev_client.py`·`jev_ask.py`와 `resources/lint-benign-v1.json`을
같은 상대 구조로 `00-meta/scripts/`에 함께 설치하고 원본과 바이트를 대조한다.
기존 helper도 사용자 편집·출처 불명을 자동 덮어쓰지 않는다. 이전 설치본 해시와
일치하는 엔진 파일만 교체하며 불명확한 파일은 기존 유지/검토로 보고한다.
의존성이 빠진 단일 검사기는 기존 fatal 게이트를 유지하되 새 경고는 incomplete다.
전체 bundle 확인 전에 훅을 활성화하지 않는다. 플러그인 checkpoint hook은 플러그인에
설치되며 이 절차가 Codex trust·전역 설정·Jarvis 데몬을 변경하지 않는다.

기존 `.gitignore`에 `00-meta/.agentic-vault/runtime/`가 없으면 그 한 줄만 추가한다.
runtime을 일반 노트 inventory나 백링크 대상으로 넣지 않는다. [시간 대장 템플릿](../../../assets/templates/ssot-ledger.md)은
선택 자료다. 실제 SSOT 이행 요청이 있을 때만 `vault_ssot.py --vault <볼트> prepare --path <실제 대장>`으로
dry-run을 준비하고 값·추가 열·본문의 보존과 diff를 검토한다. 이미 승인된 정확한 안에만
`apply --approve`와 proposal stdin을 사용한다. 업그레이드가 지식/교훈 대장을 자동 이행하지 않는다.
[보강 도구](../../../docs/hardening.md)의 출처·교훈 델타·literal 품질·토큰 보정은 명시적으로 선택한다.
기존 budget 계수·warning severity·aliases·Jarvis 일정/인증/활성 범위는 유지한다.

- `0.9.0`은 Codex에서도 같은 엔진을 사용한다. `$agentic-vault:agentic-vault session-start`·`recall <질의>`·`session-end`·`lint`·`backup` 진입점을 안내하라. Codex 플러그인 훅은 `/hooks`에서 현재 정의를 검토하고 신뢰해야 실행되며, 주입이 없으면 스킬의 `session-start`로 복원한다. 생성 AGENTS의 사용자 규칙 참조와 rules 여섯 본문이 모두 보존됐는지 확인한다. 이 업그레이드로 Codex 전역 설정이나 Claude 권한 설정을 변경하지 않는다. Jarvis의 실행기는 계속 Claude CLI다.
- `0.9.0`의 세션 주입은 예산을 실제 출력에 적용하며 0은 주입 비활성화다. 주입은 검사기 `validate_config` 전체를 통과해야 동작한다 — 어떤 키든 형식 오류(정수 키에 문자열·실수, `null`, `./` 접두·끝 슬래시 경로, 확장자가 `.md`가 아닌 `hot_note`·`handoff_note`, 빈 enum 등)면 handoff·hot이 둘 다 조용히 빠지고 stderr 한 줄만 남으니, 업그레이드 직후 healthcheck로 config 오류부터 확인하라. 구판 설정값은 유지하고 이 의미 변경을 안내하라. 절대경로·상위 경로·deny zone·심볼릭 링크/정션을 가리키는 상태 파일은 주입되지 않으므로, 설정 오류를 우회하지 말고 사용자에게 정상적인 볼트 내부 경로를 제시하라.
- 새 `/vault-recall`은 플러그인 안의 `vault_recall.py`와 `vault_paths.py`, `vault_healthcheck.py`를 함께 사용한다. 스크립트 하나만 볼트에 복사하지 마라. 플러그인 갱신으로 세 파일을 같은 버전에서 로드한다. 기존 standalone healthcheck·git 훅 설치 절차는 유지한다.
- 새 백업은 `backup_target/snapshots/`에 독립 사본을 추가한다. 기존 `mirror/`·`bundles/`를 삭제하거나 덮어쓰지 않는다. 새 스냅샷을 검증한 뒤 필요하면 새 디렉터리로 복구할 수 있음을 `docs/reliability.md`의 CLI로 안내하라.
