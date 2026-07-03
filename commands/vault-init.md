---
description: 새 프로젝트에 에이전틱 지식 볼트를 스캐폴딩 — 표준 트리 + 템플릿 + CLAUDE.md 행동 계약 + 검증
---

# /vault-init — 볼트 스캐폴딩

현재 프로젝트 디렉토리에 에이전틱 지식 볼트를 새로 구축하라. 인자: `$ARGUMENTS` = `<볼트명> [프로젝트명]` (둘 다 선택 — 없으면 질문).
아래 절차를 순서대로 수행하되, 사용자 확인이 필요한 단계(5·6)는 반드시 물어본 뒤 진행하라.

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
| `context.md` | `50-projects/<프로젝트명>/<프로젝트명> context.md` | 프로젝트명 있을 때만 |
| `tasks.md` | `50-projects/<프로젝트명>/<프로젝트명> tasks.md` | 〃 |
| `handoff.md` | `50-projects/<프로젝트명>/<프로젝트명> handoff.md` | 〃 |
| `decisions.md` | `50-projects/<프로젝트명>/<프로젝트명> decisions.md` | 〃 |
| `mistakes.md` | `50-projects/<프로젝트명>/<프로젝트명> mistakes.md` | 〃 |

(`CLAUDE-vault-section.md`와 `settings-permissions.json`은 복사 대상이 아니라 4·5단계의 입력이다.)

프로젝트 미니볼트를 만들었으면 추가로:
- `00-meta/vault-config.json`의 `handoff_note` 값을 `"50-projects/<프로젝트명>/<프로젝트명> handoff.md"`로 Edit하라.
- `00-meta/index.md`의 "## 프로젝트" 섹션에 core 노트 5종을 등록하라 (`- [[<프로젝트명> context]] — 프로젝트 컨텍스트` 형식으로 5줄).

치환 검증: 쓰기 완료 후 생성 파일들에 `{{`가 남아 있지 않은지 grep으로 확인하라(남아 있으면 치환 누락 — 즉시 수정).

## 4. CLAUDE.md 행동 계약 append

- 프로젝트 루트 `CLAUDE.md`에 `agentic-vault:begin` 마커가 이미 있으면 이 단계를 건너뛰어라(중복 방지).
- `CLAUDE.md`가 존재하면: 파일 끝에 빈 줄 하나를 두고, 치환된 `${CLAUDE_PLUGIN_ROOT}/assets/templates/CLAUDE-vault-section.md` 내용 전체를 append하라(Edit — 기존 내용을 절대 삭제·수정하지 마라).
- 존재하지 않으면: 그 내용만으로 `CLAUDE.md`를 새로 생성하라(Write).

## 5. 권한 병합 (사용자 확인 후에만)

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
00-meta/health-report.md
00-meta/scratch/step_archive/
*.pptx
*.pdf
*.docx
*.xlsx
*.png
*.jpg
*.zip
```

- **원격 push는 권하지 마라.** 볼트에는 기밀 노트가 쌓일 수 있으므로, 원격 도입은 사용자가 기밀 여부를 점검한 뒤 별도로 결정할 사안이라고 한 줄로만 안내하라(로컬 전용 권고).

## 7. 검증 (healthcheck)

- `python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_healthcheck.py" --vault . --output 00-meta/health-report.md`를 실행하라.
- 스크립트가 그 경로에 없으면 `${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/`에서 이름에 healthcheck가 들어간 .py를 찾아 같은 인자로 실행하라. 그래도 없으면 검증을 건너뛰되 그 사실을 보고하고 `/vault-lint`로 추후 검증을 안내하라.
- exit code가 0이 아니면 `00-meta/health-report.md`를 읽고 치명 위반(대개 플레이스홀더 치환 누락·프런트매터 오류)을 즉시 수정한 뒤 재실행해 0을 확인하라.

## 8. 첫 로그 기록

`00-meta/log.md` 목록의 최상단에 첫 항목을 기록하라(템플릿의 "(아직 항목이 없다…)" 안내줄은 삭제):

```
- <YYYY-MM-DD HH:MM> | Claude | [ops] 볼트 초기화 — /vault-init로 표준 트리·템플릿 스캐폴딩 (<볼트명>)
```

## 9. 완료 보고

사용자에게 보고하라: ① 생성된 트리 요약(디렉토리 수·파일 수) ② 수행/생략된 선택 단계(프로젝트 미니볼트·권한 병합·git) ③ healthcheck 결과 ④ 다음 단계 안내 — 권한·훅 반영을 위해 세션 재시작 권장, 첫 지식은 `10-inbox/`에 수집한 뒤 볼트 명령(`/vault-*`)으로 처리, 세션 시작 시 `00-meta/hot.md`부터 읽기.
