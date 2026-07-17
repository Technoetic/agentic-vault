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
- 이 명령은 볼트 내용(지식 노트)에 손대지 않는다 — 엔진 표면(설정·훅·시스템 파일)만 다룬다.
