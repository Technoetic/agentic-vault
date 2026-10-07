---
description: 새 프로젝트에 에이전틱 지식 볼트를 스캐폴딩 — 표준 트리 + 템플릿 + CLAUDE.md 행동 계약 + 검증
---

# /vault-init — 볼트 스캐폴딩

현재 프로젝트 디렉토리에 에이전틱 지식 볼트를 새로 구축하라. 인자: `$ARGUMENTS` = `<볼트명> [프로젝트명]` (둘 다 선택 — 없으면 질문).
아래 절차를 순서대로 수행하되, 사용자 확인이 필요한 단계(4-1·5·6)는 반드시 물어본 뒤 진행하라.

## 0. 가드 (이미 볼트면 중단)

- `00-meta/vault-config.json`이 이미 존재하면 **어떤 파일도 만들거나 덮어쓰지 말고 즉시 중단**하라. "이 디렉토리는 이미 볼트로 초기화되어 있다"고 한 줄로 보고만 한다(재구성·수리는 사용자가 명시적으로 요청할 때만 별도 진행).
- 디렉토리가 비어 있지 않으면(기존 파일 존재) 최상위 항목 목록을 간단히 보여주고 "이 디렉토리에 볼트를 구축할까요?"를 확인받아라.

## 1. 볼트명·프로젝트명 확정

- **볼트명**: `$ARGUMENTS`의 첫 토큰. 없으면 사용자에게 물어라(기본값 제안: 현재 디렉토리 이름).
- **프로젝트명(선택)**: 둘째 토큰. 없으면 "초기 프로젝트 미니볼트(50-projects/)도 만들까요? 만들면 프로젝트명을 알려주세요"라고 한 번만 물어라. 사용자가 생략하면 프로젝트 템플릿 5종(context/tasks/handoff/decisions/mistakes)은 건너뛴다.
- 오늘 날짜(ISO `YYYY-MM-DD`)를 `{{DATE}}` 치환값으로 쓴다.

## 2. 표준 트리 생성

크로스 플랫폼으로 다음 디렉토리를 생성하라 (python 표준 라이브러리, 상대 경로·슬래시만 사용):

```
python -c "import pathlib; [pathlib.Path(d).mkdir(parents=True, exist_ok=True) for d in ['00-meta/schemas','00-meta/scratch/step_archive','10-inbox/quick','10-inbox/voice','10-inbox/web','10-inbox/_processed','20-knowledge/concepts','20-knowledge/domains','20-knowledge/patterns','20-knowledge/references','20-knowledge/tools','20-knowledge/sources','20-knowledge/_archive','30-journal','40-people/individuals','40-people/organizations','40-people/interactions','50-projects/_completed','90-assets']]"
```

생성 후, 빈 상태로 남는 말단 디렉토리 전부에 내용 없는 `.gitkeep` 파일을 만들어라(빈 디렉토리는 git이 추적하지 못한다).

## 3. 템플릿 복사 + 플레이스홀더 치환

`${CLAUDE_PLUGIN_ROOT}/assets/templates/`의 각 템플릿을 Read로 읽고, `{{VAULT_NAME}}` → 볼트명, `{{PROJECT_NAME}}` → 프로젝트명, `{{DATE}}` → 오늘 날짜로 **모든 등장 위치를** 치환한 뒤 Write로 대상 경로에 생성하라:

| 템플릿 | 대상 경로 | 조건 |
|---|---|---|
| `vault-config.json` | `00-meta/vault-config.json` | 항상 (볼트 식별자 — 마지막에 쓰지 말고 여기서 생성) |
| `frontmatter-schema.md` | `00-meta/schemas/frontmatter.md` | 항상 |
| `hot.md` | `00-meta/hot.md` | 항상 |
| `index.md` | `00-meta/index.md` | 항상 |
| `log.md` | `00-meta/log.md` | 항상 |
| `lessons.md` | `00-meta/lessons.md` | 항상 (자기개선 루프 — 교훈 대장) |
| `context.md` | `50-projects/<프로젝트명>/<프로젝트명> context.md` | 프로젝트명 있을 때만 |
| `tasks.md` | `50-projects/<프로젝트명>/<프로젝트명> tasks.md` | 〃 |
| `handoff.md` | `50-projects/<프로젝트명>/<프로젝트명> handoff.md` | 〃 |
| `decisions.md` | `50-projects/<프로젝트명>/<프로젝트명> decisions.md` | 〃 |
| `mistakes.md` | `50-projects/<프로젝트명>/<프로젝트명> mistakes.md` | 〃 |

(`CLAUDE-vault-stub.md`·`AGENTS-vault-stub.md`·`rules/`·`settings-permissions.json`은 복사 대상이 아니라 4·5단계의 입력이다.)

프로젝트 미니볼트를 만들었으면 추가로:
- `00-meta/vault-config.json`의 `handoff_note` 값을 `"50-projects/<프로젝트명>/<프로젝트명> handoff.md"`로 Edit하라.
- `00-meta/index.md`의 "## 프로젝트" 섹션에 core 노트 5종을 등록하라 (`- [[<프로젝트명> context]] — 프로젝트 컨텍스트` 형식으로 5줄).

치환 검증: 쓰기 완료 후 생성 파일들에 `{{`가 남아 있지 않은지 grep으로 확인하라(남아 있으면 치환 누락 — 즉시 수정).

## 4. 행동 계약 설치 (rules + CLAUDE.md 스텁 + AGENTS.md)

행동 계약은 3층으로 설치한다 — **rules(엔진 소유 규칙) / CLAUDE.md 스텁(볼트 정체성·사용자 영역 안내) / AGENTS.md(타 에이전트용 생성 산출물)**.

1. **rules 설치**: `.claude/rules/` 디렉토리를 만들고 `${CLAUDE_PLUGIN_ROOT}/assets/templates/rules/`의 `vault-*.md` 6개(architecture·linking·frontmatter·workflow·collab·browser)를 그대로 복사하라(치환 불필요 — 엔진 규칙은 의도적으로 볼트 무관 내용만 담는다). 각 파일 첫 줄의 `engine=` 스탬프는 유지하라(/vault-upgrade의 교체 판단 기준).
2. **CLAUDE.md 스텁 append**: 루트 `CLAUDE.md`에 `agentic-vault:begin` 마커가 이미 있으면 건너뛰어라(중복 방지). `CLAUDE.md`가 존재하면 파일 끝에 빈 줄 하나를 두고 치환된 `${CLAUDE_PLUGIN_ROOT}/assets/templates/CLAUDE-vault-stub.md` 내용 전체를 append하라(Edit — 기존 내용을 절대 삭제·수정하지 마라). 존재하지 않으면 그 내용만으로 새로 생성하라(Write).
3. **AGENTS.md 생성**: 루트에 `AGENTS.md`가 없을 때만 생성하라. `${CLAUDE_PLUGIN_ROOT}/assets/templates/AGENTS-vault-stub.md`의 `{{VAULT_NAME}}`을 치환한 내용 전체를 먼저 쓰고, 설치된 rules 6개의 본문을 **architecture → linking → frontmatter → workflow → collab → browser** 순서로 빈 줄을 사이에 두고 이어 붙인다. 각 rule의 맨 앞 `agentic-vault:rule engine=` HTML 주석 블록만 제거하고 본문은 그대로 보존한다. 스텁의 `agentic-vault:generated` 소유권 마커를 유지한다. `CLAUDE-vault-stub.md`를 AGENTS에 복사하거나 플러그인 설치 절대경로를 박아 넣지 마라. 이 전용 스텁은 사용자 규칙을 루트 `CLAUDE.md`의 관리 마커 밖에서 안전하게 읽도록 안내한다. 이미 AGENTS.md가 존재하면 내용과 소유권을 그대로 유지하고 "/vault-upgrade가 재생성 경로"라고 한 줄 안내하라(Codex 표기는 `$agentic-vault:agentic-vault upgrade`).

## 4-1. 도구 어댑터 (선택 — 사용자 승인 후에만)

선택 기능이다. 설치된 도구에 맞춘 볼트 조항을 제안할 뿐 엔진 규칙을 바꾸지 않는다. 지금 제공하는 어댑터는 Aside(AI 브라우저 CLI) 하나다.

- `python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_adapters.py" --vault . --format json`을 실행해 `adapters.aside`를 읽는다. 읽기 전용 진단이며 아무것도 설치하지 않는다.
- `actions`에 `offer_install`이 없으면 이 단계를 조용히 건너뛴다. 브라우저 도구는 사용자가 나중에 CLAUDE.md에 정한다(vault-browser 규칙 1).
- 있으면 "Aside CLI가 설치되어 있습니다. Aside를 이 볼트의 유일한 브라우저 도구로 정하는 어댑터를 켤까요? (CLAUDE.md 조항과 운영 가이드 노트를 추가하고, Windows에서는 기동할 때 새 창을 바로 최소화하고 포커스를 돌려주는 도우미도 설치합니다)"를 묻는다. 거부하거나 답이 없으면 아무것도 설치하지 않는다.
- 승인하면 아래 순서로 진행한다. 원본은 `${CLAUDE_PLUGIN_ROOT}/assets/adapters/aside/`에 있다.
  1. **CLAUDE.md 조항:** `browser-clause.md` 전체를 루트 `CLAUDE.md` 끝에 빈 줄 하나를 두고 append한다. `agentic-vault:begin`~`end` 관리 블록 **밖**에 둔다. `agentic-vault:adapter aside begin` 마커가 이미 있으면 건너뛴다. 기존 내용은 삭제·수정하지 않는다. CLAUDE.md에 다른 브라우저 도구 조항이 이미 있으면 append하지 말고 두 조항을 보여 주고 어느 쪽을 쓸지 묻는다.
  2. **기동 도우미(Windows만):** `platform`이 `windows`면 `aside-up.ps1`을 `00-meta/scripts/aside-up.ps1`로 바이트 그대로 복사한다(`agentic-vault:adapter aside-up engine=` 스탬프 보존). 다른 플랫폼에서는 복사하지 않고, 조항의 macOS·Linux 줄이 적용된다고 알린다.
  3. **운영 가이드 노트:** `20-knowledge/tools/Aside CLI 운영 가이드.md`가 없을 때만 `operations-guide.md`의 `{{DATE}}`를 치환해 생성하고 index의 도구 절에 서술 규격대로 등록한다. 프로젝트 decisions 노트가 있으면 "브라우저 도구 = Aside" 결정을 ADR로 남긴다. 이 노트의 「엔진 개발 실측」 표는 참고용이다. 이 기기에서 실행해 본 뒤 「이 볼트의 실측」 표를 채우고 `status: active`로 바꾸라고 안내한다.
  4. **자동 기동 훅(Claude Code·Windows만, 따로 확인):** `settings-hooks.json`을 보여 주고 "aside 명령 앞에서 기동 도우미를 자동으로 실행하는 훅을 이 기기의 `.claude/settings.local.json`에 병합할까요?"를 따로 묻는다. 훅은 기기마다 다르므로 기본 병합 대상은 공유되지 않는 `settings.local.json`이다. 공유 `.claude/settings.json`에 넣으려면 이유를 설명하고 따로 확인받는다. 거부하거나 답이 없으면 병합하지 않는다. 승인하면 JSON을 파싱해 `hooks.PreToolUse` 배열에 이 그룹을 추가한다. `aside-up.ps1`을 부르는 훅이 이미 있으면 건너뛰고, 기존 키와 훅은 모두 보존한다. 폴더 동기화(OneDrive 등)를 쓰는 볼트는 local 파일도 다른 기기로 퍼진다는 점을 알린다. 훅은 `if` 조건(`Bash(aside *)`·`PowerShell(aside *)`) 때문에 aside 명령에서만 실행되고, 도우미도 훅 입력을 다시 확인해 명령 위치에 aside가 없으면 곧바로 끝낸다. 결과와 상관없이 명령을 막지 않는다. 훅 명령은 작은따옴표로 감싼 `-Command` 안에서 `$env:CLAUDE_PROJECT_DIR`를 PowerShell이 풀도록 되어 있어 Git Bash·PowerShell 어느 훅 셸에서도 같은 경로를 가리킨다. 이 형태를 고치지 마라. Codex에서는 이 단계를 생략하고 조항의 기동 절차를 따르게 한다.
- 끝나면 같은 진단을 다시 실행해 `clause.state=present`를 확인한다. Windows에서는 `helper.state=current`도, 훅을 병합했다면 `hook.state=configured`도 확인한다.

## 5. 권한 병합 (사용자 확인 후에만)

- **Claude Code 전용 단계다.** Codex에서 시작한 초기화는 생략한다. 사용자가 Claude Code 권한 설정도 명시적으로 요청한 경우에만 아래 절차를 적용한다. 이 deny 블록을 Codex 권한 설정으로 변환하지 마라.
- `${CLAUDE_PLUGIN_ROOT}/assets/templates/settings-permissions.json`의 deny 블록을 사용자에게 보여주고 "deny zone 읽기 차단 권한을 `.claude/settings.json`에 병합할까요?"를 물어라.
- **승인 시**: `.claude/settings.json`이 있으면 JSON을 파싱해 `permissions.deny` 배열에 **없는 항목만 추가**하라(기존 항목·다른 키는 전부 보존, 중복 금지). 파일이 없으면 이 블록만으로 새로 생성하라.
- **거부 시**: 건너뛰고, deny zone 보호가 CLAUDE.md 행동 계약(산문 규칙)만으로 동작함을 한 줄로 알려라.

## 6. git init 제안

- 이미 git 리포지토리면 이 단계를 생략하라.
- 아니면 "로컬 git 버전관리를 켤까요? (권장 — 노트를 잘못 편집했을 때의 유일한 복구 수단)"을 물어라.
- **승인 시**: `git init` → 아래 내용으로 `.gitignore` 생성 → 지금까지 생성한 파일만 스테이징해 초기 커밋(`-A` 남발 금지, 기존 무관 파일 포함 주의):

```gitignore
# agentic-vault: 지식 노트(.md) 중심 추적 — 바이너리·비밀·자동생성물 제외
90-assets/
.obsidian/
**/.env
.claude/settings.local.json
00-meta/health-report.md
00-meta/scratch/step_archive/
00-meta/.agentic-vault/runtime/
*.pptx
*.pdf
*.docx
*.xlsx
*.png
*.jpg
*.zip
```

- **원격 push는 권하지 마라.** 볼트에는 기밀 노트가 쌓일 수 있으므로, 원격 도입은 사용자가 기밀 여부를 점검한 뒤 별도로 결정할 사안이라고 한 줄로만 안내하라(로컬 전용 권고).
- **git 무결성 게이트 설치** (git을 켠 경우에만): 아래 순서를 지켜 엔진과 훅을 설치하라. 세 파일의 `engine=` 스탬프(훅 두 파일은 `agentic-vault:hook engine=0.8.2`, 검사기는 원본의 `agentic-vault:healthcheck engine=` 값)와 LF 줄바꿈, 훅의 실행 권한을 유지한다.
  1. `00-meta/scripts/git-hooks/`를 만든 뒤 `${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_healthcheck.py`를 먼저 `00-meta/scripts/vault_healthcheck.py`로 복사한다.
     새 검사기는 같은 버전의 helper bundle을 필요로 한다. 플러그인 `skills/agentic-vault/scripts/`의 `vault_*.py`·`jev_client.py`·`jev_ask.py`와 `resources/lint-benign-v1.json`을 동일한 상대 구조로 `00-meta/scripts/`에 함께 복사하고 바이트를 대조한다. 어느 의존성 복사든 실패하면 훅을 활성화하지 않는다. 단일 파일 fallback은 기존 fatal 검사만 보존하며 새로운 경고 분석은 incomplete다. 누락을 정상 검증으로 표시하지 않는다. runtime ignore 줄은 기존 `.gitignore`에도 없을 때만 추가한다.
  2. 엔진 복사가 성공한 뒤에만 `${CLAUDE_PLUGIN_ROOT}/assets/git-hooks/`의 `pre-commit`·`pre-push`를 `00-meta/scripts/git-hooks/`로 복사한다. 어느 복사든 실패하면 활성화하지 말고 fail-closed로 중단한다.
  3. `git config --get core.hooksPath`의 유효 설정을 확인한다. 값이 없으면 `git config core.hooksPath 00-meta/scripts/git-hooks`로 활성화하고, 이미 같은 값이면 유지한다. **다른 `core.hooksPath` 값**이 있으면 그 값을 보여주고 교체해도 되는지 **명시적 확인**을 받은 경우에만 위 활성화 명령을 실행한다. 거부하거나 답이 불명확하면 기존 값을 유지한다.
- 설치 효과를 한 줄로 안내하라: pre-commit은 설치된 Python staged 검사기로 Git index의 프런트매터·YAML 위키링크·삭제 백링크를 검사하고 검사기의 종료 코드를 그대로 반환하며, 검사기나 Python이 없으면 fail-closed로 차단한다. pre-push는 기존 로컬 전용 정책대로 원격 push를 차단한다(두 훅 모두 `--no-verify` 명시 우회 가능).

## 7. 검증 (healthcheck)

- `python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_healthcheck.py" --vault .`를 실행하라.
- 스크립트가 그 경로에 없으면 `${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/`에서 이름에 healthcheck가 들어간 .py를 찾아 같은 인자로 실행하라. 그래도 없으면 검증을 건너뛰되 그 사실을 보고하고 `/vault-lint`로 추후 검증을 안내하라.
- exit code가 0이 아니면 `00-meta/health-report.md`를 읽고 치명 위반(대개 플레이스홀더 치환 누락·프런트매터 오류)을 즉시 수정한 뒤 재실행해 0을 확인하라.

## 8. 첫 로그 기록

`00-meta/log.md` 목록의 최상단에 첫 항목을 기록하라(템플릿의 "(아직 항목이 없다…)" 안내줄은 삭제):

```
- <YYYY-MM-DD HH:MM> | Claude | [ops] 볼트 초기화 — /vault-init로 표준 트리·템플릿 스캐폴딩 (<볼트명>)
```

## 9. 완료 보고

사용자에게 보고하라: ① 생성된 트리 요약(디렉토리 수·파일 수) ② 수행/생략된 선택 단계(프로젝트 미니볼트·도구 어댑터·권한 병합·git) ③ healthcheck 결과 ④ 다음 단계 안내 — 권한·훅 반영을 위해 세션 재시작 권장, 첫 지식은 `10-inbox/`에 수집한 뒤 볼트 명령(`/vault-*`, Codex는 `$agentic-vault:agentic-vault <작업>`)으로 처리, 세션 시작 시 이미 주입된 컨텍스트를 재사용하고 없으면 검증·예산이 적용된 session-start 절차로 복원. Codex의 플러그인 훅은 `/hooks`에서 현재 정의를 검토하고 신뢰한 뒤 실행된다.
