<!-- agentic-vault:rule engine=0.6.0 — 엔진 소유 파일. /vault-upgrade가 신버전으로 통째 교체한다.
     볼트 고유 규칙은 여기가 아니라 CLAUDE.md에 쓰라 — 이 파일의 로컬 편집은 업그레이드 때 사라진다. -->

# 볼트 아키텍처 맵 (디렉토리별 접근 규칙)

- `00-meta/` — 시스템 규칙·스키마·스크립트. 새 노트를 만들기 전 `00-meta/schemas/frontmatter.md`를 따른다. 볼트 전체 지도는 `00-meta/index.md`, 500단어 핫 컨텍스트는 `00-meta/hot.md`.
- `10-inbox/` — 수집 대기열(quick/voice/web). 정제 후 20-knowledge로 이동시킨다.
- `20-knowledge/` — 영구 지식 레이어(concepts/domains/patterns/references/tools/sources). sources는 원전·긴 보고서, tools는 검증된 사용법·운영 가이드다. 고립 노드를 만들지 마라.
- `30-journal/` — 일일 로그 `30-journal/YYYY/MM/YYYY-MM-DD.md`와 주간·분기 회고.
- `40-people/` — 인물·조직·미팅 기록(individuals/organizations/interactions).
- `50-projects/` — 활성 프로젝트 미니볼트(context/tasks/handoff/decisions/mistakes).
- `90-assets/` — 이미지·PDF·원시 데이터. **READ 금지 구역** — 본문에서는 `![[파일명.png]]` 임베드 위키링크로만 참조하라. 바이너리를 직접 읽으려 시도하지 마라(토큰 폭발).
- **DENY ZONE (읽기·스캔 절대 금지):** `10-inbox/_processed/`, `20-knowledge/_archive/`, `50-projects/_completed/`, `90-assets/`, `.obsidian/` — 검색 범위에도 포함하지 마라.
  격리 이동 규칙: deny zone으로의 **이동은 셸 move로 수행하고 파일명은 바꾸지 마라**(위키링크는 파일명 기반이라 안전). 이동 후 읽기·수정이 불가능하므로 프런트매터 갱신(`status: archive`, `ai_priority: archive`)은 반드시 이동 전에 끝내라.
- `00-meta/scratch/step_archive/` — 임시 조사 산출물(스크린샷·원시 데이터) 보관 예외 구역. 지식 노트를 두지 마라.
- `00-meta/scripts/` 하위의 `.env` 파일을 읽거나 출력하지 마라 (비밀키).
- 위 구조를 벗어난 위치에 임의로 파일이나 폴더를 생성하지 마라. 볼트 루트에는 새 파일을 만들지 마라(예외: CLAUDE.md·AGENTS.md 같은 최상위 계약 파일).
