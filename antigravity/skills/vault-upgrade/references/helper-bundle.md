# vault-upgrade: 보강 helper bundle과 선택 이행

> 원본은 저장소 `commands/vault-upgrade.md` 2단계의 '보강 helper bundle과 선택 이행' 절이다. Antigravity 스킬 본문 한도 때문에 `scripts/build_antigravity_package.py`가 이 파일로 옮겼다. 이 파일을 직접 고치지 않는다. `<PLUGIN_ROOT>`는 이 플러그인 설치 폴더의 절대 경로로, 이 파일이 있는 폴더의 세 단계 위 폴더다.

### 보강 helper bundle과 선택 이행

새 standalone 검사기 설치/교체 때 같은 플러그인의 `skills/agentic-vault/scripts/`에서
`vault_*.py`·`jev_client.py`·`jev_ask.py`·`bizrouter_client.py`와 `resources/lint-benign-v1.json`을
같은 상대 구조로 `00-meta/scripts/`에 함께 설치하고 원본과 바이트를 대조한다.
기존 helper도 사용자 편집·출처 불명을 자동 덮어쓰지 않는다. 이전 설치본 해시와
일치하는 엔진 파일만 교체하며 불명확한 파일은 기존 유지/검토로 보고한다.
의존성이 빠진 단일 검사기는 기존 fatal 게이트를 유지하되 새 경고는 incomplete다.
전체 bundle 확인 전에 훅을 활성화하지 않는다. 플러그인 checkpoint hook은 플러그인에
설치되며 이 절차가 Codex trust·전역 설정을 변경하지 않는다.

기존 `.gitignore`에 `00-meta/.agentic-vault/runtime/`가 없으면 그 한 줄만 추가한다.
runtime을 일반 노트 inventory나 백링크 대상으로 넣지 않는다. [시간 대장 템플릿](../../../assets/templates/ssot-ledger.md)은
선택 자료다. 실제 SSOT 이행 요청이 있을 때만 `vault_ssot.py --vault <볼트> prepare --path <실제 대장>`으로
dry-run을 준비하고 값·추가 열·본문의 보존과 diff를 검토한다. 이미 승인된 정확한 안에만
`apply --approve`와 proposal stdin을 사용한다. 업그레이드가 지식/교훈 대장을 자동 이행하지 않는다.
[보강 도구](../../../docs/hardening.md)의 출처·교훈 델타·literal 품질·토큰 보정은 명시적으로 선택한다.
기존 budget 계수·warning severity·aliases는 유지한다.
선택적으로 `python "<PLUGIN_ROOT>/skills/agentic-vault/scripts/vault_install_state.py" record --vault .`로 현재 엔진 소유 파일과 사용자 소유 파일의 소유권·SHA-256을 `00-meta/.vault-install-state.json`에 기록해 두면, 이후 `/vault-doctor --install-state`나 `vault_install_state.py uninstall`(기본 dry-run, 사용자 파일·수정본 자동 보존)으로 상태를 추적할 수 있다.
