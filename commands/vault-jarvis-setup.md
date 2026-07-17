---
description: Vault Jarvis 설정 — Telegram 봇 연동으로 브리핑·캡처·읽기전용 Q&A·집사 활성화
---

# /vault-jarvis-setup — 자비스 계층 설정

이 볼트에 Telegram 자비스(브리핑·캡처·읽기전용 Q&A·집사)를 설정하라. 브리지 스크립트: `${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/jarvis_bridge.py`

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

`python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/jarvis_bridge.py" --self-test`를 실행하라. FAIL이 있으면 원인을 해결한 뒤 진행한다. (`env: JARVIS_TELEGRAM_TOKEN` WARN은 1단계 완료 전이면 정상)

## 3. 본인 숫자 ID 확인 → 화이트리스트

1. 브리지를 임시 실행: `python .../jarvis_bridge.py --vault .` — 화이트리스트가 비어 있으면 모든 메시지를 폐기하며 **발신자 숫자 ID를 콘솔에만 표시**한다.
2. 사용자가 자신의 봇에게 아무 메시지나 1통 전송 → 콘솔의 `미등재 발신자 폐기: from=<숫자>`에서 ID 확보.
3. `00-meta/vault-config.json`의 `jarvis` 블록을 Edit하라: `"enabled": true`, `"telegram_user_ids": [<확보한 숫자>]`. 블록이 없으면 템플릿(`${CLAUDE_PLUGIN_ROOT}/assets/templates/vault-config.json`의 `jarvis` 키)을 참고해 추가하라.
4. 브리지 재시작.

## 4. 상시 실행 등록 (Windows 권장 경로)

작업 스케줄러에 로그온 시 자동 시작으로 등록하도록 안내하라 (사용자 확인 후 실행):

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

## 보안 경계 (사용자에게 요약 고지)

- 화이트리스트 숫자 ID 외 발신자는 무응답 폐기된다.
- Q&A·브리핑 세션은 읽기 전용(Read·Grep·Glob) — 볼트 변경·명령 실행 불가.
- 자비스의 볼트 쓰기는 `10-inbox/jarvis/` 한 곳뿐이며 정제는 `/vault-process-inbox`가 담당.
- deny zone·`.env`·`90-assets/`는 Q&A 탐색에서 금지된다.
