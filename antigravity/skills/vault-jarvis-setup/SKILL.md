---
name: vault-jarvis-setup
description: "Vault Jarvis 설정 — Telegram 봇 연동으로 브리핑·캡처·읽기전용 Q&A·집사 활성화"
disable-model-invocation: true
---

> **Antigravity CLI용 생성 문서.** 원본은 저장소의 `commands/vault-jarvis-setup.md`이고 `scripts/build_antigravity_package.py`가 만든다. 이 파일을 직접 고치지 않는다.
> - `<PLUGIN_ROOT>`: 이 플러그인 설치 폴더의 절대 경로다. 이 SKILL.md가 있는 폴더의 두 단계 위 폴더다(보통 `~/.gemini/config/plugins/agentic-vault`). 셸 명령에는 펼친 절대 경로를 따옴표로 감싸 넣는다.
> - `<ARGUMENTS>`: 사용자가 슬래시 명령 뒤에 적은 텍스트다. 없으면 이번 요청에서 찾는다.
> - 볼트 루트는 작업 공간에서 `00-meta/vault-config.json`이 있는 폴더다. 볼트를 다루는 엔진 스크립트에 볼트를 넘길 때는 `--vault <볼트 루트>`를 쓴다. 본문 명령의 인자는 적힌 대로 쓴다.
> - 본문의 `/vault-*` 표기는 이 플러그인의 같은 이름 스킬을 뜻한다. Claude Code 전용 단계(`.claude/settings.json`·`.claude/settings.local.json` 병합, PreToolUse 훅 설치)는 Antigravity 세션에서 실행하지 않는다. `.claude/rules/`와 AGENTS.md는 본문대로 만든다.

# /vault-jarvis-setup — 자비스 계층 설정

이 볼트에 Telegram 자비스(브리핑·캡처·읽기전용 Q&A·집사)를 설정하라. 브리지 스크립트: `<PLUGIN_ROOT>/skills/agentic-vault/scripts/jarvis_bridge.py`

## 0. 볼트 감지 (필수 게이트)

현재 작업 디렉토리 루트에 `00-meta/vault-config.json`이 존재하는지 확인하라.
- **없으면**: "이 디렉토리는 agentic-vault 볼트가 아니므로 /vault-jarvis-setup을 생략합니다." 한 줄만 정중히 안내하고 즉시 종료하라.
- **있으면**: 진행한다.

## 1. 봇 토큰 준비 (사용자 안내)

사용자에게 다음을 안내하라 (Claude가 대신할 수 없는 단계):
1. Telegram에서 `@BotFather`에게 `/newbot` → 봇 이름·유저명 지정 → **토큰 수령**.
2. 토큰을 환경변수로 등록 (볼트·리포에 저장 금지):
   - Windows(영구): `setx JARVIS_TELEGRAM_TOKEN "<토큰>"` (새 터미널부터 적용)
   - macOS/Linux: 셸 프로필에 `export JARVIS_TELEGRAM_TOKEN="<토큰>"`
3. ⚠️ 토큰을 채팅·노트에 붙여넣지 말 것 — 유출 시 BotFather `/revoke`로 재발급.

## 2. self-test

`python "<PLUGIN_ROOT>/skills/agentic-vault/scripts/jarvis_bridge.py" --self-test`를 실행하라. FAIL이 있으면 원인을 해결한 뒤 진행한다. (`env: JARVIS_TELEGRAM_TOKEN` WARN은 1단계 완료 전이면 정상) `env: claude CLI 탐지(배치 런처 제외)`가 WARN이면 PATH에 claude가 없거나, Windows에서 npm 설치의 `claude.cmd`만 잡힌 경우다. 이때는 네이티브 `claude.exe`를 설치하거나 `jarvis.claude_cmd`에 `claude.exe` 전체 경로를 지정하라.

## 3. 본인 숫자 ID 확인 → 화이트리스트

1. 브리지를 임시 실행: `python .../jarvis_bridge.py --vault .` — 화이트리스트가 비어 있으면 모든 메시지를 폐기하며 발신자 숫자 ID만 로그에 표시한다.
2. 사용자가 자신의 봇에게 아무 텍스트 메시지나 1통 전송 → 콘솔의 `미승인 또는 비공개 아닌 발신자 폐기: from=<숫자>`에서 ID 확보.
3. `00-meta/vault-config.json`의 `jarvis` 블록을 Edit하라: `"enabled": true`, `"telegram_user_ids": [<확보한 숫자>]`, `"briefing_times": ["07:30"]`. `briefing_times`에는 하루 한 개 이상의 `HH:MM` 시각을 배열로 지정한다. 블록이 없으면 템플릿(`<PLUGIN_ROOT>/assets/templates/vault-config.json`의 `jarvis` 키)을 참고해 추가하라. 기존 볼트의 단일 `briefing_time`은 `briefing_times`가 없을 때만 하위 호환 fallback으로 사용한다. 응답에 쓸 모델과 노력 수준을 정하려면 같은 블록의 `jarvis.model`·`jarvis.effort`를 Edit하라. 두 키의 기본값은 빈 문자열이며, 비어 있으면 브리지는 `--model`·`--effort`를 넘기지 않아 claude CLI의 기본값을 쓴다. 값이 있으면 `--model <값>`·`--effort <값>`을 더한다. `effort`는 `low`·`medium`·`high`·`xhigh`·`max` 중 하나이고, `model`은 ASCII 영문자·숫자로 시작해 영문자·숫자와 `. _ : @ / + [ ] -`만 쓴 256자 이하의 이름이다. 다른 값은 설정 오류로 브리지를 시작하지 않는다. 브리지는 모델 이름이 CLI에서 쓸 수 있는지는 검사하지 않으므로, 지정했으면 5단계의 `/brief`로 응답을 확인하라.
4. 브리지 재시작.

## 4. 상시 실행 등록 (Windows 권장 경로)

작업 스케줄러에 로그온 시 자동 시작으로 등록하도록 안내하라 (사용자 확인 후 실행):

볼트·봇 네임스페이스마다 자비스 데몬을 정확히 하나만 실행해야 한다. 작업 스케줄러를 활성화하기 전에 수동으로 실행한 브리지를 중지한다. 예약 브리핑은 due 배치가 `pending`으로 기록된 뒤에 한해 at-least-once로 전송한다. Telegram 수락 후 응답 유실, 일부 청크 전송, 전송 성공 후 `fired` 기록 실패에서는 중복될 수 있다. `pending` 기록 전 프로세스가 중단되면 재시작 시 지난 슬롯은 cold-start miss로 건너뛴다. 이 보장에도 조건이 붙는다. 같은 회차가 8번 연속 실패하면(응답 생성 실패, 생성 중 예외, Telegram 전송 실패를 모두 센다) 그 회차를 `pending`에서 빼고 그날은 다시 시도하지 않으며, 소유자에게 회차의 날짜·시각·이름과 시도 횟수를 적고 원인은 짐작하지 않는 안내를 한 번 보낸다. 이 안내가 전달되지 않아도 다시 보내지 않고 로그에만 남긴다. 날이 바뀔 때 전날의 `pending` 회차는 한 번만 더 시도하고, 실패하면 같은 방식으로 건너뛴 뒤 그날 회차를 이어서 처리한다. 시도 횟수는 브리지 메모리에만 두므로 브리지를 다시 시작하면 0부터 센다.

```
schtasks /Create /TN "VaultJarvis" /SC ONLOGON /TR "pythonw <브리지 절대경로> --vault <볼트 절대경로>" /F
```

- 즉시 시작: `schtasks /Run /TN "VaultJarvis"` / 중지: 작업 관리자에서 pythonw 종료.
- macOS/Linux는 launchd/systemd 유저 유닛을 같은 명령으로 안내.

## 5. 라이브 검증 (순서 고정)

사용자와 함께 Telegram에서 순서대로 확인하라:
1. `/status` → HEAD·인박스 건수 응답 (무LLM 경로 확인)
2. `/brief` → 브리핑 생성 (읽기전용 claude 세션 확인)
3. `기억해: 테스트 메모` → `10-inbox/jarvis/`에 파일 생성 확인 (쓰기 경로 확인)
4. 자유 질문 1개 → 볼트 근거 인용 답변 (Q&A 확인)

4종 통과 시 설정 완료를 보고하고, 볼트가 git 저장소면 `vault-config.json` 변경을 커밋하라. `log_note`에 `[ops]` 태그로 1줄 기록하라.

## 접근 정책과 잔여 위험 (사용자에게 요약 고지)

- 화이트리스트 숫자 ID 외 발신자는 무응답 폐기된다.
- Q&A·브리핑 세션은 호스트가 선택한 근거와 질문을 표준 입력으로 넘기고 `--tools ""`·`--strict-mcp-config`로 모델 도구와 MCP를 제한한다. v0.18.0은 `--bare`·`--setting-sources ""`·`--disable-slash-commands`·`--no-session-persistence`도 사용한다. **`--bare`는 구독 OAuth/키체인 대신 API 인증이 필요하다.**
- 이 제한은 Claude CLI의 도구 가용성과 프롬프트 정책이며 OS 수준 샌드박스가 아니다.
- 훅은 도구가 아니라 이 제한으로 막히지 않으므로 `--settings '{"disableAllHooks":true}'`로 사용자·플러그인·볼트 훅을 모두 끈다.
- 선택 키 `jarvis.model`·`jarvis.effort`가 비어 있지 않을 때만 `--model`·`--effort`를 더한다. 두 옵션은 모델과 노력 수준만 고르며 위 도구·설정·훅 제한을 바꾸지 않는다. 이 옵션이 없는 이전 claude CLI에서는 실행이 실패할 수 있다. claude CLI 2.1.252에서 확인한 범위(2026-11-13, 경우마다 1회): 브리지의 `--bare` 실행에서는 두 옵션이 인자 검사를 통과하는 것까지만 확인했다(릴리스 기기에 API 키가 없어 응답은 만들지 못했다). `--bare` 없이 로그인한 상태에서는 `haiku`·`low`로 응답했고, CLI가 받지 않는 모델 이름 하나는 0이 아닌 종료 코드로 끝났다 — 그런 이름을 넣으면 질문과 브리핑이 모두 실패한다. `--bare`에서 인증된 응답 생성과 노력 수준이 실제로 적용되는지는 확인하지 않았다.
- v0.18.0은 모델의 직접 파일 읽기를 끄고 호스트가 허용 경로·하드링크·크기·알려진 비밀을 검사한 근거만 공급한다. 이전 배포판 0.17.1의 넓은 Read/Grep/Glob 사전 승인과 구분한다.
- v0.18.0의 `--bare`·`--setting-sources ""`는 자동 CLAUDE.md·메모리 읽기와 기본 설정 소스 로딩을 끈다. CLI 실행 파일·관리 정책·OS 계정은 계속 신뢰하며, 실제 제공자 인증 동작은 설치 전 별도로 확인해야 한다.
- Windows에서는 cmd.exe가 메시지를 다시 해석하는 `.cmd`·`.bat` 런처(npm 설치의 `claude.cmd`)를 실행하지 않으므로 네이티브 `claude.exe`가 필요하다.
- 처리 중 예외가 난 메시지는 offset을 전진시키지 않고 5초부터 간격을 늘려 다시 처리한다. 같은 메시지에서 처리 예외가 3회 연속 나면 본문 없이 로그를 남기고 소유자에게 알린 뒤 건너뛴다. 이 안내가 전달되지 않으면 건너뛰지 않고 계속 재시도한다. 실패한 브리핑·집사 보고는 간격을 늘려 가며(최대 1시간) 재시도한다. 정기 브리핑은 같은 회차가 8번 연속 실패하면 그 회차를 건너뛰고 소유자에게 한 번 알린다.
- 브리지는 claude CLI의 텍스트 출력과 종료 코드만 읽는다. 그래서 모델 제공자의 요청 거절과 장애·빈 응답을 구별하지 못하며, 실패 안내에도 원인을 적지 않는다. CLI가 거절을 0이 아닌 종료 코드나 빈 출력으로 끝내면 다른 실패와 같이 처리하고, 종료 코드 0의 텍스트로 끝내면 그 텍스트를 답으로 전달한다. 실제 거절이 어느 쪽으로 출력되는지는 확인하지 않았다.
- 메시지 기반 직접 쓰기는 `10-inbox/jarvis/` 캡처뿐이다.
- 인라인 봇을 거친 메시지는 사용자가 일부러 보낸 메모라도 `content_origin: via_bot`(비신뢰)으로 캡처된다. 이전 버전에서 받은 캡처는 다시 분류하지 않는다.
- 사진·파일·음성 등 텍스트가 아닌 메시지는 캡처·Q&A 대상이 아니며 응답·로그 없이 무시된다. 차트·스크린샷은 볼트의 `10-inbox/`에 파일로 넣고 `/vault-process-inbox`로 정제하라.
- 예약 집사는 설정된 `health_report`를 갱신하고 설정된 `mirror` 원격으로 push할 수 있다.
- 집사 보고는 healthcheck를 `--json-summary`로 실행해 치명 건수를 알리고, 읽지 못한 노트나 건너뛴 링크·정션이 있으면 `일부 미검사`와 그 건수를 붙인다. `치명 없음 ✅`는 빠짐없이 검사했고 치명이 0건일 때만 나온다(연결 작업 트리 사본과 근거 용량 한도로 빠진 노트는 미검사로 세지 않는다). 요약을 읽지 못하면 `결과 확인 불가`로 알린다.
- 실행 중인 브리지는 자신이 시작된 플러그인 폴더의 코드를 계속 쓴다. 이전 플러그인 폴더에서 등록한 브리지는 새 폴더에서 다시 시작할 때까지 예전 집사 메시지를 보낸다.
- 거부된 텍스트 메시지는 `미승인 또는 비공개 아닌 발신자 폐기`를 콘솔과 `~/.vault-jarvis/jarvis.log`에 기록하며 본문은 기록하지 않는다.
- 캡처 파일명에는 정제된 Telegram `update_id` 접미사가 붙는다.
- 캡처 정제는 `/vault-process-inbox`가 담당한다.
- deny zone·`.env`·`90-assets/`는 Q&A 탐색에서 금지된다.
- deny zone 제한은 호스트의 경로 정책이며 OS 수준 보안 경계가 아니다. 민감 자료에는 별도 파일 권한이나 샌드박스를 적용해야 한다.
