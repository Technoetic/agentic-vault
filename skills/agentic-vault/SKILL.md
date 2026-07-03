---
name: agentic-vault
description: "Use when working in a directory containing 00-meta/vault-config.json (agentic vault) — 프런트매터 스키마·위키링크 규율·SSOT 룩업·세션 인계 워크플로우를 강제"
---

# Agentic Vault — 파일 기반 에이전틱 메모리 작업 규율

## 0. 적용 조건 (볼트 감지)

- **볼트** = 루트에 `00-meta/vault-config.json`이 존재하는 디렉토리. 이 파일이 없으면 이 스킬을 적용하지 마라 — 일반 디렉토리로 취급하고 조용히 물러난다(에러·경고 출력 금지).
- 볼트에서 작업을 시작하기 전 `00-meta/vault-config.json`을 먼저 읽어라. 아래 규율의 구체 값(필수 키 목록·enum·deny zone·로그 태그·특수 노트 경로)은 전부 이 설정 파일이 원천이다. 이 문서의 예시는 기본값일 뿐이다.
- `handoff_note`·`ssot_note`·`backup_target`이 빈 문자열이면 해당 기능은 생략한다(우아한 성능 저하 — 없는 기능을 요구하지 마라).

## 1. 이 볼트는 무엇인가 — 에이전틱 메모리의 4렌즈

같은 디렉토리를 4가지 렌즈로 겹쳐 보면 이 시스템의 설계가 보인다.

1. **파일 기반 메모리** — 컨텍스트 윈도우는 세션이 끝나면 휘발되지만 평문 마크다운 파일은 영속한다. DB·벡터스토어 대신 사람이 직접 읽고 git으로 버전관리되는 파일이 **진실의 원천(Ground Truth)** 이다. 기계 회상 계층(외부 메모리 도구)이 있더라도 볼트와 모순되면 볼트가 이긴다.
2. **LLM Wiki** — 모든 개념이 위키링크 `[[노트 이름]]`으로 상호 연결된 그래프. 에이전트는 grep으로 진입점을 찾고 링크를 따라 확장 탐색한다. 링크가 없는 고립 노드는 회상 경로가 끊긴 죽은 기억이다.
3. **계층형 메모리** — 상주 규칙(CLAUDE.md) → hot(500단어 스냅숏) → handoff(세션 캐시) → 볼트 전체(grep/index 페이징)로 접근 비용이 계단식으로 커진다. 상세 설계와 SSOT 규칙: [references/memory-tiers.md](references/memory-tiers.md)
4. **제텔카스텐** — 원자 노트 + 밀집 링크 + 창발적 구조. 하나의 거대 노트 대신 2~3개의 원자 개념 노트로 분할하고 상호 링크로 엮는다. 폴더 계층이 아니라 링크 네트워크가 지식의 본체다.

## 2. 작업 규율 체크리스트 — 노트를 만들 때마다 이 순서로

1. **중복 grep** — 생성 전에 `index_note`(기본 `00-meta/index.md`)와 대상 폴더를 grep/glob으로 확인하라. 같은 주제의 노트가 이미 있으면 **새로 만들지 말고 기존 노트를 보강**하라(중복 노트는 미래의 모순 원천).
2. **프런트매터 스키마 준수** — 모든 .md 최상단에 YAML 프런트매터. `required_keys` 전부 포함, `enums`에 열거된 값만 사용(임의 값 발명 금지), `frontmatter_max_lines` 이내.
   **CRITICAL:** 프런트매터 안의 위키링크는 반드시 이중 따옴표 — `related: ["[[노트 이름]]"]`. 따옴표 없는 `[[ ]]`는 YAML 중첩 배열로 오파싱되어 Dataview가 붕괴한다. 본문 인라인 필드(`[key:: value]`)는 금지 — 메타데이터는 프런트매터에만.
3. **원자 노트 + 밀집 링크** — 원자적으로 분할하고, 본문의 주요 개념·프로젝트명·인명·조직명을 위키링크로 감싸라. 대상 노트가 없어도 링크부터 만든다. 링크 규율 전체: [references/linking-rules.md](references/linking-rules.md)
4. **index 등록 + log 태그 기록** — 작업 종료 시 `index_note`에 새 노트를 등록하고, `log_note`(기본 `00-meta/log.md`) 최상단에 1줄 요약을 남겨라.
   형식: `- YYYY-MM-DD HH:MM | 행위자 | [태그] 요약` — 태그는 `log_tags` 중 하나(기본: `ingest`·`query`·`lint`·`build`·`ops`·`decision`)를 요약 맨 앞에 붙인다. 이 규약이 로그를 grep 가능한 데이터로 만든다.

## 3. Deny Zone — 읽기·스캔 절대 금지 구역

- `deny_zones`에 열거된 경로(기본: `10-inbox/_processed`, `20-knowledge/_archive`, `50-projects/_completed`, `90-assets`, `.obsidian`)는 읽기·검색 범위에 절대 포함하지 마라. `90-assets`의 바이너리는 토큰 폭발 위험 — 본문에서 `![[파일명.png]]` 임베드 위키링크로만 참조한다.
- **격리 이동 규칙:** deny zone으로의 이동은 셸 move로 수행하고 **파일명은 바꾸지 마라**(위키링크는 파일명 기반이라 안전). 이동 후에는 읽기·수정이 불가능하므로 프런트매터 갱신(`status: archive` 등)은 반드시 **이동 전에** 끝내라.
- `exclude_dirs`(node_modules, .git 등)는 스캔에서 제외하되 deny zone과 달리 금지 구역은 아니다.

## 4. SSOT 룩업 — 값은 한 곳, 나머지는 참조

`ssot_note`가 설정된 볼트에서는 핵심 사실(연락처·식별번호·정격·가격 등)의 **값은 SSOT 노트 한 곳에만** 둔다. 새 노트는 값을 베끼지 말고 "→ `[[SSOT 노트]]` 참조"로 가리켜라 — 베끼는 순간 모순 원천이 생긴다. `ssot_facts`의 정규식 패턴이 볼트 전체에서 2종 이상의 값과 매치되면 모순이며 `/vault-lint`가 보고한다. 모순을 발견해도 **임의로 하나를 고르지 마라** — SSOT의 확정 여부에 따라 수렴시키거나 사용자에게 정합을 요청한다. 상세: [references/memory-tiers.md](references/memory-tiers.md)

## 5. 명령 8종 — 언제 쓰는가

| 명령 | 언제 쓰는가 |
|---|---|
| `/vault-init` | 새 디렉토리를 볼트로 초기화할 때 (디렉토리 골격 + vault-config.json + 템플릿, 1회) |
| `/vault-session-start` | 세션 시작 시 — hot/handoff/tasks를 로드해 직전 상태를 복원·브리핑 |
| `/vault-session-end` | 세션 종료 시 — handoff·hot·log 갱신, 설정 시 백업 실행 |
| `/vault-day` | 오늘의 사건·생각을 데일리 노트(`30-journal/`)에 위키링크와 함께 기록 |
| `/vault-ingest` | 소스 문서 1건을 원자 노트로 분해해 지식 레이어에 통합 (LLM Wiki 패턴) |
| `/vault-process-inbox` | `10-inbox/` 수집 대기열을 일괄 정제·병합하고 원본을 격리 |
| `/vault-lint` | 주기적으로, 또는 대량 변경 후 — 무결성 검증 + 자가 치유 (프런트매터/데드링크/고아/노화/SSOT 모순/로그 태그) |
| `/vault-trace` | 키워드의 시계열 진화를 저널·미팅·지식·결정 노트 횡단으로 추적해 통찰 내러티브 생성 |

## 6. 세션 인계 워크플로우

- **시작:** SessionStart 훅이 핫 컨텍스트를 주입했으면 재독하지 말고, 없을 때만 `/vault-session-start`(또는 `hot_note` 직접 읽기)로 복원하라.
- **종료:** `/vault-session-end` — `handoff_note`를 4섹션(최근 완료 / 확인 필요 / 보류 / 다음 세션 지시)으로 갱신하고, `hot_note`를 500단어 이내로 재작성하고, `log_note`에 1줄을 남긴다.
- hot·handoff는 **point-in-time 스냅숏**이다 — 볼트 원본과 모순되면 볼트를 우선하고, 스냅숏만 믿고 단정하지 마라. 노화 방지 원칙: [references/memory-tiers.md](references/memory-tiers.md)

## 7. 참조 문서

- [references/linking-rules.md](references/linking-rules.md) — 위키링크 규율 전체 (Aggressive Linking·파일명 링크·고아 링크 철학·앵커 참조)
- [references/memory-tiers.md](references/memory-tiers.md) — 계층형 메모리 설계·SSOT 룩업·문서 부패 교훈
