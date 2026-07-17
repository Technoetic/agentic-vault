# Vault Jarvis 설계 스펙 (v0.3.0)

날짜: 2026-07-17 / 상태: 승인됨 (사용자 위임 — "스스로 생각해봐")

## 1. 목적

agentic-vault에 "자비스" 계층을 추가한다: **먼저 말 걸고(브리핑), 받아적고(캡처), 물으면 답하고(질의응답), 알아서 정리하는(집사)** 개인 비서. OpenClaw의 채널 편재성·상시성과 hermes-agent의 선별 메모리 사상을 흡수하되, 몸통은 최소(브리지 1개 + Telegram 1채널)로 유지하고 두뇌(기억·지식)는 볼트 4축이 담당한다.

## 2. 비목표 (Non-goals)

- 다채널 게이트웨이(WhatsApp·Discord 등) — OpenClaw의 영역, 재발명하지 않는다
- 음성 웨이크 — 후순위
- 무인 지식 노트 생성 — 인박스 정제는 사람 있는 세션에서만 (품질 게이트)
- 자기개선 스킬 루프(Hermes 흡수 핵심) — **독립 스펙으로 2단계**
- 원격 셸/쓰기 명령 실행 — Q&A는 영구히 읽기 전용

## 3. 아키텍처

컴포넌트 4개, 전부 이 플러그인 리포에 위치. Python은 기존 관례대로 **3.10+ stdlib-only**.

```
Telegram ⟷ jarvis_bridge.py(상시 데몬) ─┬─ 캡처: 10-inbox/jarvis/ 파일 쓰기 (LLM 무관여)
                                        ├─ Q&A: claude -p 읽기전용 세션 (Read·Grep·Glob만)
                                        ├─ 브리핑: 매일 지정 시각, claude -p 읽기전용 세션
                                        └─ 집사: N시간마다 healthcheck 보고 + mirror push + 인박스 현황 (LLM 무관여)
```

### 3.1 브리지 데몬 — `skills/agentic-vault/scripts/jarvis_bridge.py`

- Telegram Bot API `getUpdates` 롱폴링(urllib, 외부 패키지 0개). 볼트 루트에서 실행(`python jarvis_bridge.py --vault <경로>`).
- **화이트리스트**: `vault-config.json`의 `jarvis.telegram_user_ids`(숫자 ID 배열)에 없는 발신자의 메시지는 **응답 없이 폐기**(로그만). 화이트리스트가 비어 있으면 모든 메시지를 폐기하되 콘솔 로그에 발신자 ID를 출력해 최초 설정을 돕는다.
- **라우팅**: ① `기억해`/`메모`/`remember` 접두 → 캡처 ② `/brief` → 즉석 브리핑 ③ `/status` → 무LLM 상태 응답(HEAD·인박스 건수·마지막 브리핑 시각) ④ 그 외 텍스트 → Q&A.
- 스케줄러 내장(브리핑 시각·집사 주기) — 외부 스케줄러 불요. Windows 상시 실행은 작업 스케줄러 등록(설정 명령이 안내).

### 3.2 캡처 경로 (쓰기 유일 경로)

- 브리지가 `10-inbox/jarvis/YYYY-MM-DD HHMMSS.md`에 **결정론적으로** 저장(본문 + 수신 시각·채널 메타 1줄). LLM이 쓰기 경로에 개입하지 않는다.
- 지식 레이어(20-knowledge 등)에는 절대 쓰지 않는다 — 정제는 기존 `/vault-process-inbox`가 담당. pre-commit 훅의 10-inbox 프런트매터 면제와 정합.
- 응답: "적어뒀습니다 → 10-inbox/jarvis/<파일명>".

### 3.3 Q&A 세션 (읽기 전용)

- `claude -p "<질문>" --allowedTools Read Grep Glob` — 쓰기·Bash·네트워크 도구 불허, cwd=볼트.
- 시스템 프롬프트(append)가 강제: hot → index → grep 순서로 탐색, deny zone(`vault-config.json` `deny_zones`)·`.env`·비밀 경로 접근 금지, 한국어(`language` 키) 답변, 근거 노트명 인용.
- **비용 가드**: `jarvis.qa_hourly_limit`(기본 6) 초과 시 "시간당 한도 도달" 응답. 타임아웃(기본 180초) 시 사과 응답.
- 프롬프트 주입 분석: 공격 표면 = 화이트리스트 통과 메시지뿐. 주입이 성공해도 도구가 읽기 3종뿐이라 변조·유출 실행 불가. 잔여 리스크: 응답 내용에 볼트 정보 포함 — 화이트리스트가 본인뿐이므로 수용.

### 3.4 브리핑·집사

- **브리핑**(기본 07:30, `jarvis.briefing_time`): Q&A와 동일한 읽기 전용 `claude -p` 세션으로 hot·handoff·tasks·`git log <anchor>..HEAD`를 종합한 아침 브리핑을 생성해 전송. `/brief`로 즉석 호출 가능.
- **집사**(기본 24시간, `jarvis.butler_interval_hours`): **LLM 없이** ① `vault_healthcheck.py` 실행 → 치명/관리성 건수 보고(자가 치유는 하지 않음 — 사람 세션의 몫) ② `mirror` 원격이 있으면 `git push mirror` ③ 인박스 대기 건수 보고. 결과를 Telegram 1메시지로.

## 4. 설정과 비밀

- `vault-config.json`에 `jarvis` 블록(선택): `enabled`, `telegram_user_ids`, `briefing_time`, `butler_interval_hours`, `qa_hourly_limit`, `claude_cmd`. **블록이 없거나 `enabled: false`면 전 기능 침묵** — 우아한 성능 저하 유지.
- 봇 토큰은 볼트에 절대 넣지 않는다: 환경변수 `JARVIS_TELEGRAM_TOKEN` (memoryhub `.env` 패턴과 동일 원칙).
- 로그는 볼트 밖 `~/.vault-jarvis/jarvis.log`(회전 1MB) — 볼트 오염 방지.

## 5. 설정 명령 — `commands/vault-jarvis-setup.md`

가이드형 명령: ① BotFather로 봇 생성·토큰 env 등록 안내 ② 브리지 최초 실행 → 본인에게 메시지 → 콘솔의 숫자 ID를 화이트리스트에 기입 ③ `vault-config.json`에 `jarvis` 블록 Edit ④ `--self-test` 실행 확인 ⑤ Windows 작업 스케줄러/`pythonw` 등록 안내. 볼트 감지 가드(`vault-config.json` 부재 시 침묵)는 기존 명령과 동일.

## 6. 검증

- `jarvis_bridge.py --self-test`: 네트워크 없이 — config 파싱·볼트 감지·라우팅 판정(캡처/명령/Q&A 분기)·인박스 쓰기(임시 파일)·claude CLI 존재를 검사하고 PASS/FAIL 표를 출력, 실패 시 exit 1.
- 라이브 검증(토큰 필요)은 사용자 몫: `/status` → `/brief` → 캡처 → 질문 순서를 setup 명령이 안내.

## 7. 이름에 대한 각주

"Jarvis"는 마블 상표라 제품명이 아닌 **기능 별칭**으로만 쓴다(명령·파일은 `vault-jarvis-*`). 중립명 개명이 필요해지면 파일명 3곳 치환으로 끝나도록 명칭 결합을 최소화한다.

## 8. 단계

- **1단계(이 스펙)**: 브리지 + 캡처 + Q&A + 브리핑 + 집사 = v0.3.0
- **2단계(별도 스펙)**: 자기개선 루프 — 세션 교훈의 반복 패턴을 스킬 파일로 승격 제안 (Hermes 흡수 핵심)
- **3단계(선택)**: 채널 추가 또는 OpenClaw 어댑터, 음성
