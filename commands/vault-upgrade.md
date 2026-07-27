---
description: 기존 볼트를 현재 엔진 기능으로 업그레이드 — 누락된 템플릿·설정 키·git 훅·anchor를 멱등 설치
---

# /vault-upgrade — 기존 볼트 업그레이드

기존 볼트에 이 플러그인 버전의 기능을 설치하라. **멱등(idempotent)**: 이미 있는 것은 건너뛰고, **기존 값은 절대 덮어쓰지 않으며**, 누락분만 추가한다. `/vault-init`이 새 볼트용이라면 이 명령은 살아 있는 볼트용이다.

## 0. 볼트 판별

- `00-meta/` 디렉토리가 없으면: "볼트 구조(00-meta/)가 없습니다 — 새 볼트는 /vault-init을 사용하세요." 안내 후 종료.
- `00-meta/`는 있는데 `00-meta/vault-config.json`이 **없으면** (플러그인 이전에 수제작한 볼트): 볼트의 기존 구조를 탐지해(hot·handoff·index·log 노트의 실제 경로를 grep/glob로 확인) 템플릿 `${CLAUDE_PLUGIN_ROOT}/assets/templates/vault-config.json` 기반으로 생성을 **제안**하라. 탐지한 경로를 채워 보여주고 사용자 확인 후 생성한다.

## 1. 업그레이드 체크리스트 (누락분만, 순서대로)

각 항목을 검사하고 상태를 기록하라: `이미 있음(건너뜀)` / `추가함` / `사용자 거부`.

1. **vault-config 누락 키 보충**: 템플릿 vault-config.json과 현재 파일을 키 수준에서 대조해 **없는 키만** 기본값으로 추가하라(Edit — 기존 키의 값은 절대 변경 금지). 대표 누락: `jarvis` 블록(기본 `enabled: false`), `stale_days`, `index_scopes`.
1-1. **행동 계약 rules 구조 (v0.6.0+)** — 3단계로 검사하라:
   - **rules 설치/교체**: `.claude/rules/vault-*.md` 5종(architecture·linking·frontmatter·workflow·collab)이 없으면 `${CLAUDE_PLUGIN_ROOT}/assets/templates/rules/`에서 복사하라. 있으면 각 파일 첫 줄의 `engine=` 스탬프를 플러그인 버전과 비교해 **낮은 파일만 통째로 교체**하라(엔진 소유 파일 — 아래 안전 규칙의 명시적 예외). 교체 전 diff에서 템플릿에 없는 로컬 추가분이 보이면, 그 줄들을 사용자에게 보여주고 CLAUDE.md로 옮길지 물어본 뒤 진행하라.
   - **구판 모놀리스 마이그레이션**: 루트 CLAUDE.md의 `agentic-vault:begin`~`end` 마커 사이에 상세 규칙 섹션(`## 볼트 아키텍처 맵`, `## Hard Rules:` 등)이 남아 있으면 구판(v0.5.x 이하) 설치다. **사용자 확인 후** 마이그레이션하라: ①마커 사이 내용을 rules 템플릿 5종과 대조해 **볼트 고유 추가·수정분을 식별**하고(예: 프로젝트명·SSOT 규칙·커스텀 deny 경로) ②마커 사이를 치환된 `CLAUDE-vault-stub.md` 내용으로 교체하되 ③식별한 볼트 고유분은 마커 **밖**(CLAUDE.md 본문, "볼트 고유 규칙" 섹션 신설)으로 보존 이동하라. 고유분인지 엔진 표준인지 판단이 서지 않는 줄은 **삭제하지 말고 보존 쪽을 택하라**. 마이그레이션 전 CLAUDE.md 원본을 `00-meta/scratch/step_archive/CLAUDE-premigration-<날짜>.md`로 백업하라.
   - **AGENTS.md 생성/재생성**: `agentic-vault:generated` 주석이 있는 AGENTS.md는 스텁+rules 본문으로 재생성하라. 주석 없는 AGENTS.md(수제작)가 있으면 덮어쓰지 말고, 생성판으로의 전환 여부를 사용자에게 물어라(수제작 내용 중 rules에 없는 것은 CLAUDE.md 보존 이동 대상).
2. **교훈 대장**: `00-meta/lessons.md`가 없으면 템플릿 `lessons.md`를 `{{DATE}}` 치환해 생성하라 — 자기개선 루프가 이 파일 존재로 켜진다.
3. **git 무결성 게이트** (볼트가 git 저장소일 때만): `00-meta/scripts/git-hooks/`에 pre-commit·pre-push가 없으면 `${CLAUDE_PLUGIN_ROOT}/assets/git-hooks/`에서 복사(LF 유지)하고 `git config core.hooksPath 00-meta/scripts/git-hooks`를 **사용자 확인 후** 실행하라. 효과 1줄 안내: "커밋 시 프런트매터·YAML 위키링크 검증 + 네트워크 push 차단(로컬 미러 허용)".
4. **handoff anchor**: `handoff_note`가 설정돼 있고 그 파일 제목 아래에 "기준 커밋(anchor)" 줄이 없으면 삽입하라 — git 볼트면 `git rev-parse --short HEAD` 값으로, 아니면 `(없음)`으로.
5. **Jarvis 안내** (설치는 하지 않음): `jarvis.enabled`가 false면 "Telegram 자비스를 켜려면 /vault-jarvis-setup" 한 줄만 안내하라 — 토큰 발급은 사용자 행위라 자동화 불가.

## 2. 검증

- 볼트가 git 저장소이고 훅을 설치했으면: 임시 검증 없이 다음 실제 커밋이 게이트를 통과하는지로 확인된다는 점을 안내하라.
- `python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_healthcheck.py" --vault . --output <health_report>`를 실행해 exit 0을 확인하라(치명 위반이 있으면 업그레이드가 아니라 기존 문제 — /vault-lint 안내).

## 3. 보고

표로 보고하라: 항목 | 상태(이미 있음/추가함/거부) | 비고. 추가분이 있으면 git 볼트에선 커밋을 권하라(`ops:` 태그). `log_note` 최상단에 `[ops] /vault-upgrade — <추가 항목 요약>` 1줄을 남겨라.

## 안전 규칙

- 기존 파일·키·값을 덮어쓰지 마라. 충돌이 의심되면 멈추고 물어라.
- **예외(유일)**: `agentic-vault:rule engine=` 헤더가 있는 `.claude/rules/vault-*.md`와 `agentic-vault:generated` 헤더가 있는 `AGENTS.md`는 엔진 소유 파일로, 구버전이면 통째 교체가 정상 동작이다(교체 전 로컬 편집분 보존 확인은 1-1 절차를 따른다).
- 이 명령은 볼트 내용(지식 노트)에 손대지 않는다 — 엔진 표면(설정·훅·시스템 파일)만 다룬다.
