---
name: vault-upgrade
description: "기존 볼트를 현재 엔진 기능으로 업그레이드 — 레거시 검사 범위를 보존하며 누락된 템플릿·설정 키·git 훅·anchor를 멱등 설치"
disable-model-invocation: true
---

> **Antigravity CLI용 생성 문서.** 원본은 저장소의 `commands/vault-upgrade.md`이고 `scripts/build_antigravity_package.py`가 만든다. 이 파일을 직접 고치지 않는다.
> - `<PLUGIN_ROOT>`: 이 플러그인 설치 폴더의 절대 경로다. 이 SKILL.md가 있는 폴더의 두 단계 위 폴더다(보통 `~/.gemini/config/plugins/agentic-vault`). 셸 명령에는 펼친 절대 경로를 따옴표로 감싸 넣는다.
> - `<ARGUMENTS>`: 사용자가 슬래시 명령 뒤에 적은 텍스트다. 없으면 이번 요청에서 찾는다.
> - 볼트 루트는 작업 공간에서 `00-meta/vault-config.json`이 있는 폴더다. 엔진 스크립트에는 `--vault <볼트 루트>`로 넘긴다.
> - 본문의 `/vault-*` 표기는 이 플러그인의 같은 이름 스킬을 뜻한다. Claude Code 전용 단계(설정 파일 `.claude/…`, PreToolUse 훅)는 Antigravity 세션에서 실행하지 않는다.

# /vault-upgrade — 기존 볼트 업그레이드

기존 볼트에 이 플러그인 버전의 기능을 설치하라. **멱등(idempotent)**: 이미 있는 것은 건너뛰고, 기존 사용자 파일·값은 덮어쓰지 않으며, 아래 호환 범위 키 예외를 뺀 누락분과 명확히 식별되는 구버전 엔진 소유 파일만 절차대로 갱신한다. `/vault-init`이 새 볼트용이라면 이 명령은 살아 있는 볼트용이다.

## 0. 볼트 판별

- `00-meta/` 디렉토리가 없으면: "볼트 구조(00-meta/)가 없습니다 — 새 볼트는 /vault-init을 사용하세요." 안내 후 종료.
- `00-meta/`는 있는데 `00-meta/vault-config.json`이 **없으면** (플러그인 이전에 수제작한 볼트): 볼트의 기존 구조를 탐지해(hot·handoff·index·log 노트의 실제 경로를 grep/glob로 확인) 템플릿 `<PLUGIN_ROOT>/assets/templates/vault-config.json` 기반으로 생성을 **제안**하라. 탐지한 경로를 채워 보여주고 사용자 확인 후 생성한다.

## 1. 업그레이드 체크리스트 (누락분만, 순서대로)

각 항목을 검사하고 상태를 기록하라: `이미 있음(건너뜀)` / `추가함` / `사용자 거부` / `승인 대기(기존 유지)`.

1. **vault-config 일반 누락 키 보충**: 템플릿 vault-config.json과 현재 파일을 키 수준에서 대조해 **없는 키만** 기본값으로 추가하라(Edit — 기존 키의 값은 절대 변경 금지). 대표 누락: `jarvis` 블록(기본 `enabled: false`), `stale_days`, `index_scopes`.
   - 기존 config에 `jarvis` 블록 자체가 없으면 새 블록 전체를 템플릿 기본값으로 추가하는 기존 동작을 유지하라.
   - **레거시 단일 브리핑 예외**: 기존 `jarvis` 블록에 `briefing_time`이 있고 `briefing_times`가 없으면, 일반 누락 키 보충에서 `briefing_times`를 제외해 단일 시각 fallback 일정을 그대로 보존하라.
   - `briefing_times` 추가는 별도 **브리핑 일정 마이그레이션**이다. 기존 `briefing_time` 값을 배열의 유일한 값으로 사용하는 변경안을 보여주고, 사용자가 명시적으로 승인한 경우에만 추가하라. 템플릿 기본값 `07:30`으로 대체하지 마라.
   - **호환 범위 키 예외**: `frontmatter_roots`와 `frontmatter_exempt_paths`는 단순 기본값이 아니라 레거시 full 모드의 검사 범위를 선택하는 부재 marker다. 두 키를 일반 누락 키 보충에서 제외하라. 기존 config에 둘 중 하나라도 없으면 그 부재를 그대로 보존하고, 다른 키가 이미 있더라도 빠진 키를 자동 추가하지 마라.
   - `frontmatter_exempt_paths`가 없고 `fm_exempt_zones`가 있으면 기존 키가 호환 alias로 계속 적용된다. 템플릿 기본 `frontmatter_exempt_paths`를 넣어 alias를 가리지 마라.
   - 두 키 중 빠진 키를 추가하는 작업은 별도 **검사 범위 마이그레이션**이다. 추가 전 현재 full 모드의 유효 범위와 템플릿 값을 적용한 뒤의 범위를 비교해 보여주고, 범위가 넓어지거나 좁아지는 경로와 기존 alias 대체 여부를 설명하라. 그 뒤 사용자가 해당 키 추가를 명시적으로 승인한 경우에만 추가하고, 거부하거나 답이 없으면 config를 그대로 유지하라.
1-1. **행동 계약 rules 구조 (v0.6.0+)** — 3단계로 검사하라:
   - **rules 설치/교체**: `.claude/rules/vault-*.md` 6종(architecture·linking·frontmatter·workflow·collab·browser)이 없으면 `<PLUGIN_ROOT>/assets/templates/rules/`에서 복사하라(v0.12.0에서 `vault-browser.md`가 추가됐고, v0.16.0에서 workflow·collab의 스탬프가 `engine=0.16.0`으로, v0.17.0에서 browser의 스탬프가 `engine=0.17.0`으로, v0.43.0에서 workflow의 스탬프가 `engine=0.43.0`으로 올라갔다(9번 창 기반 앱 규칙, 사실 서술보다 실측을 따르는 조항) — v0.12.0 이전 볼트에는 browser가 새로 들어가고 workflow·collab이 교체되며, v0.12.0~v0.16.x 볼트에서는 스탬프가 낮은 workflow·collab(해당 시)·browser가 교체된다. 내용이 바뀌지 않은 architecture·linking·frontmatter는 스탬프가 같아 교체되지 않는다). 있으면 각 파일 첫 줄의 `engine=` 스탬프를 **대응하는 원본 템플릿 파일의 스탬프**와 비교해 **낮은 파일만 통째로 교체**하라(엔진 소유 파일 — 아래 안전 규칙의 명시적 예외). 전체 플러그인 버전이나 prerelease 문자열로 rules 버전을 추정하지 마라. 교체 전 diff에서 템플릿에 없는 로컬 추가분이 보이면, 그 줄들을 사용자에게 보여주고 CLAUDE.md로 옮길지 물어본 뒤 진행하라.
   - **구판 모놀리스 마이그레이션**: 루트 CLAUDE.md의 `agentic-vault:begin`~`end` 마커 사이에 상세 규칙 섹션(`## 볼트 아키텍처 맵`, `## Hard Rules:` 등)이 남아 있으면 구판(v0.5.x 이하) 설치다. **사용자 확인 후** 마이그레이션하라: ①마커 사이 내용을 rules 템플릿 5종과 대조해 **볼트 고유 추가·수정분을 식별**하고(예: 프로젝트명·SSOT 규칙·커스텀 deny 경로) ②마커 사이를 치환된 `CLAUDE-vault-stub.md` 내용으로 교체하되 ③식별한 볼트 고유분은 마커 **밖**(CLAUDE.md 본문, "볼트 고유 규칙" 섹션 신설)으로 보존 이동하라. 고유분인지 엔진 표준인지 판단이 서지 않는 줄은 **삭제하지 말고 보존 쪽을 택하라**. 마이그레이션 전 CLAUDE.md 원본을 `00-meta/scratch/step_archive/CLAUDE-premigration-<날짜>.md`로 백업하라.
   - **AGENTS.md 생성/재생성**: AGENTS.md가 없으면 생성하고, `agentic-vault:generated` 주석이 있으면 생성 산출물로 재생성하라. 입력은 `<PLUGIN_ROOT>/assets/templates/AGENTS-vault-stub.md`의 `{{VAULT_NAME}}`을 현재 볼트명으로 치환한 내용 전체와 설치된 rules 6개 본문이다. **architecture → linking → frontmatter → workflow → collab → browser** 순서로 빈 줄을 두고 연결하며, 각 rule의 맨 앞 `agentic-vault:rule engine=` HTML 주석 블록만 제거한다. 스텁의 소유권 마커와 사용자 CLAUDE.md 관리 블록 밖을 안전하게 읽으라는 안내를 유지한다. Claude 전용 스텁을 재사용하거나 설치 절대경로를 넣지 마라. 기존 생성 파일에 사용자 추가·수정분이 보이면 먼저 CLAUDE.md 관리 마커 밖으로 보존 이동하는 변경안을 확인하고, 판단이 불명확하면 원본을 유지한다. 주석 없는 AGENTS.md(수제작)가 있으면 덮어쓰지 말고, 생성판으로의 전환 여부를 사용자에게 물어라(수제작 내용 중 rules에 없는 것은 CLAUDE.md 관리 마커 밖으로 보존 이동 대상).
2. **교훈 대장**: `00-meta/lessons.md`가 없으면 템플릿 `lessons.md`를 `{{DATE}}` 치환해 생성하라 — 자기개선 루프가 이 파일 존재로 켜진다. 이미 있는데 `## 기각 대장` 섹션이 없으면(v0.8.2 이하 생성분) 두 가지를 함께 하라 — 하나만 하면 파일이 자기모순이 된다: ①템플릿의 `## 기각 대장` 섹션을 파일 끝에 멱등 추가 ②`## 규칙` 섹션의 구판 보일러플레이트를 신판으로 정합 — 구판 문장 "기각하면 … 다시 제안하지 않는다"와 구판 형식 줄(상태 enum에 `검증중`·`롤백` 없음)을 템플릿의 해당 줄로 치환하고, 없는 규칙 불릿(승격 검증·하위 호환)을 추가하라. `## 규칙`·형식 줄은 엔진 보일러플레이트라 이 치환이 비파괴 계약의 예외이며, **`## 대장` 이하의 교훈·기각 데이터 줄은 절대 건드리지 마라.** 사용자가 규칙 섹션을 로컬 수정한 흔적(템플릿 구판과도 다른 문구)이 보이면 자동 치환하지 말고 diff를 보여주고 물어라.
3. **git 무결성 게이트** (볼트가 git 저장소일 때만): 다음 세 엔진 표면을 각각 검사하라. 원본은 `<PLUGIN_ROOT>/skills/agentic-vault/scripts/vault_healthcheck.py`와 `<PLUGIN_ROOT>/assets/git-hooks/`의 `pre-commit`·`pre-push`, 설치 대상은 `00-meta/scripts/vault_healthcheck.py`와 `00-meta/scripts/git-hooks/`의 두 훅이다. healthcheck의 `agentic-vault:healthcheck engine=` 및 훅의 `agentic-vault:hook engine=` 스탬프를 읽어 점(.) 단위 숫자 버전으로 비교한다(문자열 사전순 비교 금지). 세 파일은 독립적으로 아래 상태를 판정하고, 모든 diff는 비밀값이 없는 엔진 파일끼리만 산출한다.
   - **누락**: 추가 대상으로 기록한다. 설치 순서는 반드시 healthcheck 엔진 → pre-commit → pre-push이며, 앞 단계가 실패하면 뒤 파일과 hooksPath를 활성화하지 않는다.
   - **스탬프 없음**: 사용자 제작·출처 불명 파일로 간주한다. 원본과의 diff를 보여주되 **자동 덮어쓰기 금지**이며 기존 파일을 유지한다. 교체는 사용자가 diff를 보고 명시 승인한 경우에만 가능하다.
   - **설치 버전이 낮음**: 스탬프가 맞는 엔진 소유 구버전만 교체 후보다. 교체 전에 원본과의 diff를 보여준다. 로컬 수정·추가가 없다고 확인된 파일만 새 원본으로 교체하고, **로컬 수정**이 하나라도 있거나 판단이 불명확하면 자동 덮어쓰기 금지, 기존 파일 유지 후 사용자에게 처리 방향을 묻는다.
   - **설치 버전과 동일**: 내용도 원본과 같으면 `이미 있음(건너뜀)`. 내용이 다르면 로컬 수정 파일이므로 diff를 보여주고 자동 덮어쓰기 금지, 기존 파일을 유지한다.
   - **설치 버전이 높음**: 신버전 또는 별도 배포본으로 보고 절대 다운그레이드하지 않는다. 버전과 원본 diff를 보여주고 기존 파일을 유지한다.
   - 복사하는 Python·훅 파일은 LF와 `engine=` 스탬프를 보존하고, 두 훅은 실행 권한도 보존한다. pre-push의 로컬 전용 원격 차단 정책은 변경하지 않는다.
   - 파일 처리가 끝나면 `git config --get core.hooksPath`로 현재 유효값을 확인한다. 값이 없으면 사용자 확인 후 `git config core.hooksPath 00-meta/scripts/git-hooks`를 실행하고, 이미 같은 값이면 유지한다. **다른 값**이면 현재 값을 보여주고 교체할지 **명시적 확인**을 받아라. 승인 없이는 설정과 기존 훅을 그대로 유지한다.
   - 효과 1줄 안내: "pre-commit은 설치된 staged 검사기의 결과를 그대로 반영하고 Python·검사기 부재 시 fail-closed, pre-push는 네트워크 push 차단(로컬 미러 허용)". 두 훅은 로컬 강제 장치이며 `--no-verify`로 명시 우회할 수 있다.
4. **handoff anchor**: `handoff_note`가 설정돼 있고 그 파일 제목 아래에 "기준 커밋(anchor)" 줄이 없으면 삽입하라 — git 볼트면 `git rev-parse --short HEAD` 값으로, 아니면 `(없음)`으로.
5. **Jarvis 안내** (설치는 하지 않음): `jarvis.enabled`가 false면 "Telegram 자비스를 켜려면 /vault-jarvis-setup" 한 줄만 안내하라 — 토큰 발급은 사용자 행위라 자동화 불가.
6. **도구 어댑터(선택)**: `python "<PLUGIN_ROOT>/skills/agentic-vault/scripts/vault_adapters.py" --vault . --format json --offer-aside-command`를 실행하고 `adapters.aside.actions`와 `adapters.aside_command.actions`(Claude Code에서는 `adapters.deny_guard.actions`도)에 따라 처리하라. 진단은 읽기 전용이다. 교체·설치는 아래 각 항목의 조건을 따르며, 사용자 승인 없이 새로 설치하는 것은 없다.
   - `offer_helper_update`: 설치된 `00-meta/scripts/aside-up.ps1`의 `agentic-vault:adapter aside-up engine=` 스탬프가 번들 원본보다 낮다. 엔진 소유 구버전이므로 원본과의 diff를 보여 주고, 릴리스 노트로 설명되지 않는 로컬 수정이 없을 때만 원본으로 교체한다. 판단이 불명확하면 기존 파일을 유지하고 `승인 대기(기존 유지)`로 기록한다.
   - `review_helper`: 스탬프가 없거나 숫자 버전이 아니거나(사용자 제작), 같은 스탬프인데 내용이 다르거나(로컬 수정), 더 높거나(다른 배포본), 읽을 수 없거나, 링크·정션을 거치는 경로다. 자동 덮어쓰기 금지 — diff만 보여 주고 기존 파일을 유지한다.
   - `offer_install`: Aside CLI가 있는데 어댑터가 없다. `/vault-init` 4-1과 같은 질문·순서로 제안만 하고, 승인할 때만 설치한다.
   - `offer_helper`·`offer_guide_note`: 조항은 있는데 도우미(Windows) 또는 운영 가이드 노트가 없다. 빠진 것만 제안한다. 가이드 노트를 만들면 index의 도구 절에 서술 규격대로 등록하고 log에 기록한다.
   - `hook_optional`: Claude Code에서 자동 기동 훅을 켤 수 있다고 한 줄만 안내한다. 병합은 `/vault-init` 4-1의 4단계와 같은 확인을 거친다(기본 대상 `.claude/settings.local.json`).
   - `review_clause`: 조항 마커가 짝이 맞지 않거나, CLAUDE.md를 읽을 수 없거나, 링크·정션을 거친다. 수정하지 말고 보고한다.
   - `review_settings`: 설정 파일을 읽을 수 없거나 훅에 문제가 있다(`hook.problems`). `"$CLAUDE_PROJECT_DIR/..."` 형태의 명령은 PowerShell 훅 셸에서 경로가 비어 드라이브 루트의 같은 이름 파일을 실행할 수 있으므로, 현재 `settings-hooks.json`의 명령으로 바꾸는 변경안을 보여 주고 승인받아 교체한다. `if` 필터가 없는 aside-up 훅도 같은 방식으로 제안한다.
   - CLAUDE.md의 Aside 조항은 볼트 소유다. 업그레이드가 내용을 바꾸지 않는다(아래 `/aside-repl` 항목에서 사용자가 승인한 한 줄 추가만 예외다). 조항 시작 마커의 `engine=` 값은 그 조항을 넣은 엔진 판일 뿐 교체 기준이 아니다.
   - **`/aside-repl` 절차 명령(`adapters.aside_command.actions`):** `--offer-aside-command`를 붙였으므로 JSON에는 이 항목이 항상 있다. Aside 조항이 없고 `state`가 `missing`이면 사용자에게 말하지 않고 넘어간다.
     - `offer_command`: Aside 조항이 있는데 `.claude/commands/aside-repl.md`가 없다. `/vault-init` 4-1의 5단계와 같은 질문·조건으로 제안만 하고, 승인할 때만 넣는다.
     - `offer_command_update`: 설치된 명령의 `agentic-vault:adapter aside-repl engine=` 스탬프가 번들 원본보다 낮다. `offer_helper_update`와 같이 diff를 보여 주고 로컬 수정이 없을 때만 교체한다.
     - `review_command`: 스탬프가 없거나(볼트가 직접 만든 같은 이름 명령일 수 있다) 숫자 버전이 아니거나, 같은 스탬프인데 내용이 다르거나, 더 높거나, 읽을 수 없거나, 링크·정션을 거치는 경로다. 자동 덮어쓰기 금지 — diff만 보여 주고 기존 파일을 유지한다.
     - `state`가 `current`인데 Aside 조항에 `**repl 스크립트 전달:**` 줄이 없으면, 번들 `browser-clause.md`의 그 줄을 보여 주고 조항 끝 마커 바로 앞에 넣을지 따로 묻는다. 답이 없으면 넣지 않고, 조항의 다른 내용은 바꾸지 않는다.
   - **deny zone 가드(`adapters.deny_guard.actions`, Claude Code 전용 — Codex는 건너뛴다):** 업그레이드는 가드를 새로 설치하거나 제안하지 않는다(`--offer-deny-guard`를 붙이지 않는다. 새 설치는 `/vault-init` 4-2나 `assets/adapters/deny-guard/guide.md`의 수동 병합).
     - `offer_guard_update`: 설치된 `00-meta/scripts/deny-guard.py`·`ps-parse.ps1`의 스탬프(`agentic-vault:adapter deny-guard engine=`·`agentic-vault:adapter ps-parse engine=`)가 번들 원본보다 낮다. `offer_helper_update`와 같이 diff를 보여 주고 로컬 수정이 없을 때만 교체하며, 두 파일은 같은 버전으로 함께 바꾼다.
     - `review_guard`: 스크립트가 로컬 수정·스탬프 없음·더 높음·읽기 실패·링크/정션이거나, 일부만 있다(`state=incomplete`: 스크립트만 있고 훅이 없거나, 훅만 있고 스크립트가 없거나, `ps-parse.ps1`이 없음). 자동 덮어쓰기·삭제 금지 — 상태와 diff만 보여 주고, 빠진 파일 복사나 남은 훅 그룹 제거는 사용자가 승인할 때만 한다. 훅이 있는데 `deny-guard.py`가 없으면 python이 종료 코드 2로 끝나 대상 도구 호출이 막힌다는 점을 함께 알린다.
     - `review_settings`: 가드 훅에 문제가 있거나(`hook.problems`) 설정 파일을 읽을 수 없다. 현재 `settings-hooks.json`의 그룹으로 바꾸는 변경안을 보여 주고 승인받아 교체한다.
   - **교훈 단서 훅(`adapters.lesson_cues.actions`, Claude Code 전용 — Codex는 건너뛴다):** 설치 흔적이 있을 때만 이 항목이 보고된다. 업그레이드는 이 훅을 새로 설치하거나 제안하지 않는다(새 설치는 `/vault-init` 4-3이나 `assets/adapters/lesson-cues/guide.md`의 수동 병합).
     - `offer_cues_update`: 설치된 `00-meta/scripts/lesson-cues.py`의 스탬프가 번들 원본보다 낮다. diff를 보여 주고 로컬 수정이 없을 때만 교체한다.
     - `review_lesson_cues`: 스크립트가 로컬 수정·스탬프 없음·더 높음·읽기 실패·링크/정션이거나, 스크립트와 훅 가운데 하나만 있다. 자동 덮어쓰기·삭제 금지 — 상태와 diff만 보여 주고 사용자 승인으로 처리한다.
     - `review_settings`: 훅에 문제가 있거나 설정 파일을 읽을 수 없다. 현재 `settings-hooks.json`의 그룹으로 바꾸는 변경안을 보여 주고 승인받아 교체한다.
     - `build_lesson_cues`: 컴파일 목록 `00-meta/lesson-cues.json`이 없다. 대장에 단서가 있으면 `vault_lesson_cues.py --vault . build --write`를 안내한다.

## 2. 검증

> 보강 helper bundle과 선택 이행 절차는 [references/helper-bundle.md](references/helper-bundle.md)로 옮겼다(Antigravity 스킬 본문 한도). 해당 단계에서 그 파일을 읽는다.

## 3. 보고

표로 보고하라: 항목 | 상태(승인 대기/이미 있음/추가함/거부) | 비고. `승인 대기` 행(답을 받지 못한 마이그레이션·로컬 수정 때문에 교체를 보류한 구버전 엔진 파일·다른 hooksPath 등)을 표 맨 위에 두고, 비고에 무엇을 결정해야 하는지 한 줄로 적어라. 추가분이 있으면 git 볼트에선 커밋을 권하라(`ops:` 태그). `log_note` 최상단에 `[ops] /vault-upgrade — <추가 항목 요약>` 1줄을 남겨라.

## 안전 규칙

- 기존 파일·키·값을 덮어쓰지 마라. 충돌이 의심되면 멈추고 물어라.
- **예외(엔진 소유 표면)**: `agentic-vault:rule engine=` 헤더가 있는 `.claude/rules/vault-*.md`, `agentic-vault:generated` 헤더가 있는 `AGENTS.md`, 1-3의 올바른 `engine=` 스탬프가 있는 healthcheck·git 훅, 그리고 6번의 `agentic-vault:adapter aside-up engine=` 스탬프가 있는 `00-meta/scripts/aside-up.ps1`은 엔진 소유 파일이다. 6번에서 사용자가 승인한 aside-up 훅 명령 교체도 같은 예외에 든다. 6번의 `agentic-vault:adapter deny-guard engine=`·`agentic-vault:adapter ps-parse engine=` 스탬프가 있는 `00-meta/scripts/deny-guard.py`·`00-meta/scripts/ps-parse.ps1`(Claude Code)도 엔진 소유 파일이며, 사용자가 승인한 가드 훅 그룹 교체도 같은 예외에 든다. 6번의 `agentic-vault:adapter aside-repl engine=` 스탬프가 있는 `.claude/commands/aside-repl.md`도 엔진 소유 파일이다. 구버전 교체 전에는 각 절의 로컬 편집분 보존 절차를 반드시 따른다. 스탬프가 없거나 동일·신버전인 파일은 자동 교체 예외에 포함되지 않는다.
- 이 명령은 볼트 내용(지식 노트)에 손대지 않는다 — 엔진 표면(설정·훅·시스템 파일)만 다룬다. 단, 6번에서 사용자가 승인한 어댑터 설치(운영 가이드 노트 생성과 index·log 기록, CLAUDE.md 관리 블록 밖 조항 추가, 사용자가 승인한 Aside 조항 한 줄 추가와 `/aside-repl` 명령 설치)는 예외다.
