# agentic-vault

**파일 기반 에이전틱 메모리** — 옵시디언 볼트를 Claude Code의 영구 상태 계층(Persistent State Layer)으로 쓰는 플러그인.

컨텍스트 윈도우는 세션이 끝나면 휘발되지만, 평문 마크다운 볼트는 영속한다. 이 플러그인은 볼트를 **LLM Wiki**(위키링크 그래프) + **계층형 메모리**(hot/handoff/전체 페이징) + **제텔카스텐**(원자 노트·밀집 링크)으로 운영하는 데 필요한 기계 장치 일체 — 명령 8종, 세션 훅, 무결성 검사기, 백업 스크립트, 노트 템플릿 — 를 제공한다.

**원칙: 플러그인 = 엔진, 볼트 = 데이터.** 볼트의 모든 정책(필수 프런트매터 키·deny zone·로그 태그·SSOT 사실)은 볼트 쪽의 `00-meta/vault-config.json` 하나로 선언하고, 플러그인은 그것을 읽어 동작한다. 볼트가 아닌 디렉토리에서는 모든 컴포넌트가 조용히 무동작한다.

> **English summary** — *agentic-vault* is a Claude Code plugin that turns a plain-Markdown Obsidian vault into a persistent, file-based memory layer for AI agents. It combines four ideas: file-based memory (plain text as ground truth), an LLM Wiki (wikilink graph traversal), tiered memory (a 500-word hot context, a session handoff cache, and grep/index paging over the full vault), and Zettelkasten discipline (atomic notes, dense linking). The plugin ships 8 slash commands, session hooks, a stdlib-only Python health checker with self-healing workflows, a backup script, and note templates. All vault policy lives in a single `00-meta/vault-config.json`; directories without that file are silently ignored. Engine and data are strictly separated — the plugin is generic, your vault is yours.

## 요구사항

- Claude Code (플러그인 지원 버전)
- Python 3.10+ — 표준 라이브러리만 사용, Windows/macOS/Linux 크로스 플랫폼

## 설치 (로컬 마켓플레이스)

```
# 1. 이 저장소를 로컬에 두고 (예: D:\agentic-vault-plugin)
/plugin marketplace add D:\agentic-vault-plugin

# 2. 플러그인 설치
/plugin install agentic-vault@agentic-vault-dev

# 3. Claude Code 재시작 (훅·명령 로드)
```

## 빠른 시작

```
cd my-vault                # 볼트로 쓸 디렉토리 (기존 옵시디언 볼트도 가능)
claude
/vault-init                # 디렉토리 골격 + vault-config.json + 템플릿 생성 (1회)
/vault-session-start       # 세션 복원 — hot/handoff 브리핑
... 작업 ...               # /vault-ingest, /vault-day, /vault-process-inbox 등
/vault-session-end         # 세션 마감 — handoff/hot/log 갱신 (+백업)
```

이후 매 세션은 `/vault-session-start` → 작업 → `/vault-session-end` 사이클이다. SessionStart 훅이 볼트를 감지하면 핫 컨텍스트를 자동 주입하므로, 대부분의 경우 세션 시작 즉시 직전 상태를 이어받는다.

## 명령 8종

| 명령 | 설명 |
|---|---|
| `/vault-init` | 현재 디렉토리를 볼트로 초기화 — 표준 디렉토리 골격(00-meta/10-inbox/20-knowledge/30-journal/40-people/50-projects/90-assets)과 `vault-config.json`, 시스템 노트·템플릿 생성 |
| `/vault-session-start` | 세션 복원 — hot·handoff·tasks를 읽고 현재 상태를 브리핑 |
| `/vault-session-end` | 세션 마감 — handoff(완료/확인 필요/보류/다음 지시)·hot(500단어)·log 갱신, `backup_target` 설정 시 백업 실행 |
| `/vault-day` | 오늘의 데일리 노트(`30-journal/YYYY/MM/`) 생성/추가 — 입력 텍스트를 위키링크와 함께 기록 |
| `/vault-ingest` | 소스 문서 1건을 원자 노트로 분해해 `20-knowledge/`에 통합 (LLM Wiki 패턴) |
| `/vault-process-inbox` | `10-inbox/` 수집 대기열을 정제해 영구 지식으로 병합하고 원본을 `_processed/`로 격리 |
| `/vault-lint` | 무결성 검증 + 자가 치유 — 헬스체크 스크립트 실행 후 프런트매터 보완·데드링크 해소·고아 노드 연결·노화 문서 아카이브·SSOT 모순 보고·로그 태그 정비 |
| `/vault-trace` | 키워드의 시계열 진화를 저널·미팅·지식·결정 노트 횡단으로 추적해 통찰 내러티브 생성 |

## vault-config.json — 볼트 정책 선언

볼트의 루트 `00-meta/vault-config.json`이 볼트 감지 마커이자 정책의 단일 출처다. `/vault-init`이 기본값으로 생성하며, 이후 자유롭게 수정한다.

| 키 | 설명 |
|---|---|
| `vault_name` | 볼트 표시 이름 |
| `language` | 볼트 주 언어 (`ko`, `en` 등) — 생성 노트·브리핑 언어 |
| `deny_zones` | 읽기·스캔 절대 금지 경로 목록 (격리·아카이브·바이너리 구역) |
| `exclude_dirs` | 스캔 제외 디렉토리 (node_modules, .git 등 — 금지 구역은 아님) |
| `required_keys` | 모든 노트 프런트매터의 필수 키 목록 |
| `enums` | `type`·`status`·`ai_priority`의 허용 값 목록 — 임의 값 발명 차단 |
| `frontmatter_max_lines` | 프런트매터 최대 줄 수 예산 |
| `index_note` | 볼트 전체 지도 노트 경로 (노트 목록의 단일 원천) |
| `log_note` | 작업 로그 노트 경로 (최상단 append, 1줄/작업) |
| `log_tags` | 로그 연산 태그 허용 목록 — 로그를 grep 가능한 데이터로 유지 |
| `log_tag_epoch` | 로그 태그 강제 시작일 — 이 날짜 이후 항목만 태그 검사(과거 소급 금지) |
| `hot_note` | 핫 컨텍스트 노트 경로 (500단어 현재 상태 스냅숏) |
| `handoff_note` | 세션 인계 노트 경로 — **빈 문자열이면 인계 기능 생략** |
| `ssot_note` | 핵심 사실 SSOT 노트 경로 — **빈 문자열이면 SSOT 기능 생략** |
| `ssot_facts` | `[{"label": "사실명", "pattern": "정규식"}]` — 볼트 전체에서 매치 값이 2종 이상이면 모순 보고 |
| `health_report` | 헬스체크 리포트 출력 경로 |
| `backup_target` | 백업 대상 경로 — **빈 문자열이면 백업 생략** |
| `stale_days` | (선택 확장) 노화 문서 검사 임계 일수 — 미설정 또는 0이면 검사 생략 |
| `index_scopes` | (선택 확장) 인덱스 미등록 검사 대상 최상위 폴더 목록 — 미설정 시 기본 스코프(메타·구조 노트·저널 제외 전체) |

`handoff_note`·`ssot_note`·`backup_target`을 비워두면 해당 기능만 조용히 꺼진다(우아한 성능 저하) — 최소 구성으로 시작해 필요할 때 켜면 된다.

## 훅 동작

- **SessionStart** — `CLAUDE_PROJECT_DIR`에 `00-meta/vault-config.json`이 존재하면(=볼트) hot/handoff 핫 컨텍스트를 세션에 자동 주입한다. 볼트가 아니면 아무 출력 없이 종료한다.
- 훅 스크립트는 볼트 위치를 환경변수 `CLAUDE_PROJECT_DIR`에서 읽고, `hooks.json`의 경로는 `${CLAUDE_PLUGIN_ROOT}` 변수를 사용한다 — 설치 위치와 무관하게 동작한다.
- 모든 훅·스크립트는 Python 표준 라이브러리만 사용한다 (PowerShell 등 셸 의존 없음).

## 구성 요소

```
agentic-vault-plugin/
├── .claude-plugin/          # plugin.json · marketplace.json
├── commands/                # vault-*.md 슬래시 명령 8종
├── hooks/                   # hooks.json + 세션 훅 스크립트
├── skills/agentic-vault/    # 볼트 작업 규율 스킬 (SKILL.md + references/)
│   ├── SKILL.md             #   프런트매터·위키링크·SSOT·세션 인계 규율
│   └── references/          #   linking-rules.md · memory-tiers.md
└── assets/                  # 노트·시스템 파일 템플릿 12종 (/vault-init이 사용)
```

무결성 검사(헬스체크)와 백업은 플러그인 동봉 Python 스크립트가 수행하며 각각 `/vault-lint`, `/vault-session-end`가 호출한다.

## 스킬: 작업 규율

`skills/agentic-vault/SKILL.md`는 볼트 디렉토리에서 작업할 때 Claude가 따르는 규율을 정의한다:

1. **노트 생성 전 중복 grep** — index·대상 폴더 확인, 중복 생성 금지
2. **프런트매터 스키마** — 필수 키·enum 준수, 프런트매터 내 위키링크는 이중 따옴표
3. **원자 노트 + Aggressive Linking** — 고아 링크는 허용(다음 노트의 예약), 경로형 링크는 금지
4. **index 등록 + log 태그 기록** — 작업의 기계 가독 흔적 남기기
5. **SSOT 룩업** — 핵심 사실 값은 한 곳, 나머지는 위키링크 참조
6. **계층형 메모리** — hot/handoff는 스냅숏, 모순 시 볼트 원본 우선

## 라이선스

MIT — [LICENSE](LICENSE) 참조.
