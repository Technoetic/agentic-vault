<!-- agentic-vault:begin — /vault-init이 추가한 볼트 행동 계약. 내용은 편집해도 되지만 이 마커 쌍은 유지하라(중복 append 방지 앵커). -->

# {{VAULT_NAME}} — 에이전틱 지식 볼트 행동 계약

이 디렉토리는 코드 리포지토리가 아니라 **옵시디언 기반의 상호 연결된 지식 베이스(LLM Wiki 패턴)** 이자
에이전트 세션을 횡단하는 **영구 상태 계층(Persistent State Layer)** 이다.
너(Claude Code)는 이 볼트에서 정보를 섭취(Ingest)하고, 위키링크로 연결하며, 지식을 복리로 축적하는 자율적 지식 관리자다.
볼트 기계 설정(deny zone·프런트매터 필수 키·Enum·로그 태그)의 단일 출처는 `00-meta/vault-config.json`이다.

## 볼트 아키텍처 맵 (디렉토리별 접근 규칙)

- `00-meta/` — 시스템 규칙·스키마·스크립트. 새 노트를 만들기 전 `00-meta/schemas/frontmatter.md`를 따른다. 볼트 전체 지도는 `00-meta/index.md`, 500단어 핫 컨텍스트는 `00-meta/hot.md`.
- `10-inbox/` — 수집 대기열(quick/voice/web). 정제 후 20-knowledge로 이동시킨다.
- `20-knowledge/` — 영구 지식 레이어(concepts/domains/patterns/references/tools/sources). sources는 원전·긴 보고서, tools는 검증된 사용법·운영 가이드다. 고립 노드를 만들지 마라.
- `30-journal/` — 일일 로그 `30-journal/YYYY/MM/YYYY-MM-DD.md`와 주간·분기 회고.
- `40-people/` — 인물·조직·미팅 기록(individuals/organizations/interactions).
- `50-projects/` — 활성 프로젝트 미니볼트(context/tasks/handoff/decisions/mistakes).
- `90-assets/` — 이미지·PDF·원시 데이터. **READ 금지 구역** — 본문에서는 `![[파일명.png]]` 임베드 위키링크로만 참조하라.
- **DENY ZONE (읽기·스캔 절대 금지):** `10-inbox/_processed/`, `20-knowledge/_archive/`, `50-projects/_completed/`, `90-assets/`, `.obsidian/`
  격리 이동 규칙: deny zone으로의 **이동은 셸 move로 수행하고 파일명은 바꾸지 마라**(위키링크는 파일명 기반이라 안전). 이동 후 읽기·수정이 불가능하므로 프런트매터 갱신(`status: archive`, `ai_priority: archive`)은 반드시 이동 전에 끝내라.
- `00-meta/scratch/step_archive/` — 임시 조사 산출물(스크린샷·원시 데이터) 보관 예외 구역. 지식 노트를 두지 마라.
- 위 구조를 벗어난 위치에 임의로 파일이나 폴더를 생성하지 마라. 볼트 루트에는 새 파일을 만들지 마라(예외: CLAUDE.md·AGENTS.md 같은 최상위 계약 파일).

## Hard Rules: 마크다운 & 위키링크

- **Aggressive Linking:** 본문의 주요 개념·프로젝트명·인명·조직명은 반드시 위키링크 `[[노트 이름]]`으로 감싸라.
  대상 노트가 아직 없어도 주저 없이 링크를 생성하라(고아 링크는 나중에 노트가 된다).
- 상대/절대 경로 마크다운 링크(`[x](../y.md)`)를 사용하지 마라. 오직 `[[노트 이름]]` 형태만 사용한다.
- 파일명은 띄어쓰기가 포함된 자연스러운 명사구로 짓는다 (예: `시장 동향 분석.md`).
- 모든 노트는 원자적(Atomic)으로 분할하라. 하나의 거대 노트 대신 2~3개의 개념 노트 + 상호 링크.
- 링크 대상은 별칭(aliases)이 아닌 **실제 파일명**과 일치시켜라 — 옵시디언 aliases는 원시 위키링크를 해석하지 못한다.

## Hard Rules: YAML 프런트매터 (위반 시 메타데이터 계층 붕괴)

- 볼트에 생성되는 **모든 .md 파일 최상단에 YAML 프런트매터**를 작성하라(필수 키 기준 12줄 내외, 선택 키 포함 최대 16줄). 스키마: `00-meta/schemas/frontmatter.md`
- **CRITICAL:** 프런트매터 안에서 위키링크를 값으로 쓸 때는 반드시 이중 따옴표로 감싸라: `related: ["[[노트 이름]]"]` 또는 하이픈 리스트 + 항목별 따옴표.
  따옴표 없는 `[[ ]]`는 YAML 중첩 배열로 오파싱되어 메타데이터 소비 시스템 전체가 붕괴한다.
- `type`/`status`/`ai_priority`는 `vault-config.json`의 Enum 값만 사용한다. 인라인 필드(`[key:: value]`)는 사용 금지.

## 핵심 워크플로우

1. **탐색:** 작업 전 `00-meta/index.md`와 관련 폴더를 grep/glob으로 확인해 기존 지식을 파악하라. **중복 노트 생성 금지** — 답이 볼트에 있으면 읽고, 없을 때만 생성한다.
2. **소화:** 소스 문서(10-inbox, 20-knowledge)를 읽고 핵심 개념을 추출하라.
3. **합성:** 원자 노트로 분할 생성하고 기존 노트들과 위키링크를 촘촘히 맺어라.
4. **기록:** 작업 종료 시 `00-meta/index.md`에 새 노트를 등록하고 `00-meta/log.md` 최상단에 1줄 요약을 남겨라. 요약 앞에는 연산 태그(`[ingest|query|lint|build|ops|decision]`)를 붙여 로그를 grep 가능한 데이터로 유지하라(형식·태그 정의: log.md 헤더).
5. **결정:** 아키텍처·전략 결정이 확정되면 즉시 프로젝트 decisions 노트에 ADR로 누적하라(번복은 삭제 대신 `대체됨 → ADR-XXX`).
6. **실수 참조:** 중요한 산출물을 확정하기 전 프로젝트 mistakes 노트를 확인하고, 같은 실수가 반복될 조짐이 보이면 그 파일에 기록하라.
7. **커밋(git 사용 시):** 의미 있는 작업 단위를 마치면 변경한 노트를 커밋하라 — `git add <변경파일>` 후 커밋(과잉 스테이징 `-A` 지양). 볼트에 기밀 노트가 쌓일 수 있으므로 **원격 push는 사용자가 기밀 점검 후 명시 승인한 경우에만** 한다. git 훅(`00-meta/scripts/git-hooks/`)이 활성이면 pre-commit이 프런트매터 누락·따옴표 없는 YAML 위키링크를 차단하고 pre-push가 원격 push를 차단한다 — 차단 시 노트를 수정해 재시도하고 `--no-verify` 우회는 금지다.

## 절대 금지 규칙

- `90-assets/` 내 바이너리를 직접 읽으려 시도하지 마라 (토큰 폭발).
- DENY ZONE(`_processed/`, `_archive/`, `_completed/`)을 검색 범위에 포함하지 마라.
- `00-meta/scripts/` 하위의 `.env` 파일을 읽거나 출력하지 마라 (비밀키).
- 프런트매터가 없는 노트를 새로 만들지 마라.
- 사용자 확인 없이 노트를 삭제하지 마라. 이동(아카이브)은 무결성 점검(/vault-lint) 결과에 따라 수행한다.

## 다중 에이전트 협업

- 새 노트 생성 전 `00-meta/index.md`와 대상 폴더를 grep해 중복 노트 생성을 금지하라. 생성 후 index 등록 + log 1줄 기록은 의무다.
- tasks·handoff 노트는 전체 덮어쓰기(Write) 금지 — 부분 수정(Edit)으로 자기 항목만 추가·갱신하라.

<!-- agentic-vault:end -->
