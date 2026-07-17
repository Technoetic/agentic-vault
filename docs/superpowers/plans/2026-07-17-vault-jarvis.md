# Vault Jarvis v0.3.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agentic-vault에 Telegram 단일 채널 자비스(브리핑·캡처·읽기전용 Q&A·집사)를 추가한다.

**Architecture:** 상시 데몬 `jarvis_bridge.py`(stdlib-only)가 Telegram 롱폴링·화이트리스트·라우팅·스케줄을 전담한다. 쓰기는 브리지의 결정론적 인박스 저장뿐이고, LLM은 `claude -p` 읽기 전용 세션(Read·Grep·Glob)으로만 호출된다. `vault-config.json`의 `jarvis` 블록이 없으면 전 기능 침묵.

**Tech Stack:** Python 3.10+ stdlib-only(urllib·subprocess·pathlib), Telegram Bot API, Claude Code CLI(`claude -p`).

## Global Constraints

- 외부 pip 패키지 0개 (기존 healthcheck·backup 스크립트와 동일 원칙)
- 봇 토큰은 env `JARVIS_TELEGRAM_TOKEN`만 — 볼트·리포에 절대 저장 금지
- Q&A·브리핑 세션 도구는 `Read Grep Glob` 고정 — 쓰기·Bash 영구 불허
- 브리지의 볼트 쓰기는 `10-inbox/jarvis/` 한 곳뿐
- 로그는 볼트 밖 `~/.vault-jarvis/jarvis.log` (1MB 회전)
- 화이트리스트는 숫자 user ID 배열, 미등재 발신자는 무응답 폐기

---

### Task 1: `jarvis_bridge.py` + `--self-test`

**Files:**
- Create: `skills/agentic-vault/scripts/jarvis_bridge.py`

**Interfaces (Produces):**
- `load_jarvis_config(vault: Path) -> dict | None` — 볼트 아님/블록 없음/`enabled==False`면 None
- `route(text: str) -> tuple[str, str]` — `("capture", 본문)` | `("brief","")` | `("status","")` | `("qa", 원문)`; 캡처 접두: `기억해`·`메모`·`remember`(대소문자 무시, 뒤 `:` 허용)
- `do_capture(vault: Path, body: str, source: str) -> str` — `10-inbox/jarvis/YYYY-MM-DD HHMMSS.md` 생성, 파일명 반환
- `run_claude(vault: Path, cfg: dict, prompt: str) -> str` — `claude -p <prompt> --allowedTools Read Grep Glob --append-system-prompt <가드>`; 타임아웃 시 안내문 반환
- `do_status(vault: Path) -> str` — LLM 없이 HEAD·인박스 건수·가동 시간
- `do_butler(vault: Path, cfg: dict) -> str` — healthcheck 실행(치명/관리성 건수만 보고, 치유 없음) + `mirror` 원격 존재 시 push + 인박스 건수
- 메인 루프 — getUpdates(timeout=50) 롱폴링, private 챗 + 화이트리스트 필터, 라우팅·응답, `briefing_time` 1일 1회·`butler_interval_hours` 주기 스케줄, `qa_hourly_limit` 레이트리밋
- `--self-test` — 네트워크 없이 임시 볼트 스캐폴드로 검증

- [x] **Step 1: 스크립트 작성** (계약 위 정의대로; Q&A 시스템 프롬프트 가드에 deny_zones·`.env` 금지·hot→index→grep 순서·언어 키 포함)
- [x] **Step 2: self-test 케이스** — ①config: 볼트 아님→None / 블록 없음→None / enabled false→None / 정상→dict ②route 5분기(기억해:·메모·remember·/brief·/status·일반 질문) ③capture 임시 인박스에 파일 생성·내용 일치 ④claude CLI 탐지(부재 시 WARN, FAIL 아님) ⑤로그 디렉토리 쓰기. PASS/FAIL 표 출력, FAIL>0이면 exit 1
- [x] **Step 3: `python jarvis_bridge.py --self-test` 실행 → 전 항목 PASS 확인**
- [x] **Step 4: Commit** `feat: jarvis_bridge.py — Telegram 브리지 + self-test`

### Task 2: 설정 표면 — config 템플릿 + `/vault-jarvis-setup`

**Files:**
- Modify: `assets/templates/vault-config.json` — `"jarvis"` 블록 추가(`enabled:false` 기본, 전 키 문서화 겸용)
- Create: `commands/vault-jarvis-setup.md` — 볼트 감지 가드 → BotFather 토큰·env 등록 안내 → 최초 실행으로 본인 숫자 ID 확인 → `jarvis` 블록 Edit → `--self-test` → Windows 작업 스케줄러(`pythonw`) 등록 안내 → 라이브 검증 순서(`/status`→`/brief`→캡처→질문)

**Interfaces (Consumes):** Task 1의 CLI 플래그(`--vault`, `--self-test`)와 config 키 이름 그대로.

- [x] **Step 1: 두 파일 작성**
- [x] **Step 2: 기존 볼트 감지 명령들과 가드 문구 일치 확인**
- [x] **Step 3: Commit** `feat: /vault-jarvis-setup + config jarvis 블록`

### Task 3: 문서·버전 — README + v0.3.0

**Files:**
- Modify: `README.md` — 명령 표에 `/vault-jarvis-setup` 행, 트리에 `jarvis_bridge.py`·`vault-jarvis-setup.md`, "🤖 Jarvis 계층" 섹션(아키텍처·보안 경계·비목표), 버전 표기 v0.3.0
- Modify: `.claude-plugin/plugin.json`·`.claude-plugin/marketplace.json` — `0.3.0`

- [x] **Step 1: README·버전 수정**
- [x] **Step 2: Commit + tag `v0.3.0` + push** `feat: Vault Jarvis 계층 (v0.3.0)`

### Task 4: NS 볼트 라이브 연동 (사용자 협업 단계)

**Files:**
- Modify(NS): `D:\NS\00-meta\vault-config.json` — 존재 확인 후 `jarvis` 블록 추가(없으면 최소 config 생성 여부를 먼저 확인)

- [x] **Step 1: NS vault-config 상태 확인·jarvis 블록 기입(화이트리스트는 사용자 ID 확인 후)**
- [x] **Step 2: 사용자 — BotFather로 봇 생성, 토큰을 env로 등록** *(사용자만 가능)*
- [x] **Step 3: 브리지 기동(백그라운드) → 사용자 첫 메시지로 숫자 ID 확보 → 화이트리스트 기입 → 재기동**
- [x] **Step 4: 라이브 검증 — `/status` → `/brief` → `기억해:` 캡처(인박스 파일 확인) → 자유 질문(Q&A) 순서로 4종 확인, 결과를 NS log에 기록**
