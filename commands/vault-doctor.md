---
description: 기억 주입의 설정·경로·예산 상태와 도구 어댑터 상태를 읽기 전용으로 진단
---

현재 볼트의 기억 주입 상태를 진단하라. 스크립트 경로는 실제 설치된 플러그인에서
구한다. Codex는 공유 스킬 `references/codex.md`의 플러그인 경로 규칙을 따른다.

```text
python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_doctor.py" --vault "<현재 볼트 절대 경로>" --format json
```

1. stdout의 JSON과 종료 코드를 함께 확인한다. 진단은 읽기 전용이며 설정 변경,
   건강 보고서 작성, 기억 원문 출력, 훅 신뢰 변경을 수행하지 않는다.
2. 전체 상태와 handoff/hot별 원인, 주입 예정량, 다음 조치를 간결히 설명한다.
   예산 0은 사용자가 비활성화한 상태다. 누락·빈 파일·읽기 실패·경로 차단·예산 부족을
   구별하고, 차단된 파일을 다른 도구로 열어 진단을 우회하지 않는다.
3. 한 섹션 오류로 실제 훅의 전체 출력이 중단되면 다른 섹션이 개별적으로 읽히더라도
   "기억 주입 정상"이라고 보고하지 않는다. 잘린 출력은 완전한 기억이 아니다.
   사유 `host_char_cap`은 두 섹션을 합친 훅 출력이 엔진 상한(`host_char_cap`, 9,500자 —
   Claude Code 한계 10,000자 아래)을 넘어 더 잘렸다는 뜻이다. 상한은 설정이 아니므로 예산을
   올리라고 안내하지 말고, 노트를 줄이는 것(`shorten_the_notes_below_the_host_cap`)을 안내한다.
   노트가 읽기 한도도 넘었으면 `source_byte_limit`이 먼저 보고된다.
4. 이 진단은 파일과 설정에서 예상되는 주입 결과다. Claude/Codex가 훅을 신뢰했는지,
   실제로 실행했는지, 모델이 기억을 올바르게 사용했는지는 증명하지 않는다.
5. 진단 요청만으로 수리를 시작하지 않는다. 사용자가 이미 수정을 요청했다면 해당
   범위에서 원인에 맞는 수정을 진행하고 같은 진단으로 결과를 확인한다.
6. 도구 어댑터 상태도 함께 본다:
   `python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_adapters.py" --vault "<현재 볼트 절대 경로>" --format json`.
   이 진단도 읽기 전용이다. `adapters.aside`의 `actions`는 제안으로만 보고하고 설치·교체는
   `/vault-upgrade`(Codex `$agentic-vault:agentic-vault upgrade`)로 안내한다. `cli_on_path`가 false이고
   설치된 것이 없으면 "도구 어댑터 없음" 한 줄로 끝낸다. 이 결과는 기억 주입 판정과 섞지 않는다.
7. 사용자가 Jev/Jarvis 연결 준비 상태도 요청하면 [단계별 연결 진단](../docs/tower-reliability.md)의
   `vault_connectors.py --vault PATH --format json`을 추가로 사용한다. 기본은 오프라인이며
   키 존재를 인증 성공으로 해석하지 않는다. live probe는 명시한 지원 연결과 기존 승인
   범위에서만 수행하며, 진단 요청만으로 Jarvis를 시작하거나 메시지를 보내지 않는다.
8. 전체 지침 분량도 요청하면 같은 Doctor에 `--instructions`를 추가한다.
   [지침 진단 안내](../docs/moai-diagnostics.md)에 따라 루트 지침·참조·중첩 규칙의 정적 분량과
   조건·누락을 보고한다. 본문을 출력하지 않으며 실제 호스트 로드나 제공자 비용으로 해석하지 않는다.
9. 사용자가 Claude Code 버전 확인을 요청할 때만 같은 Doctor에 `--host-version`을 추가한다.
   PATH의 `claude --version`을 고정 인자·셸 없이 한 번 실행하고(최대 약 5초) `host_version`의
   `status`(`ok`·`below_recommended`·`unavailable`·`unparsed`)와 권장 최소 버전 2.1.295를 보고한다.
   종료 코드는 바뀌지 않는다. PATH 실행 파일의 버전이므로 지금 훅을 실행하는 호스트와 같다고
   단정하지 않는다. 낮은 버전이면 갱신을 권하되 설치·갱신을 직접 실행하지 않는다.
   [권장 사유와 실측 절차](../docs/reliability.md#호스트-버전과-sessionstart-실측)
10. 사용자가 SessionStart 훅이 실제로 실행됐는지·잘렸는지·실패했는지 확인을 요청하고 Claude Code 세션 기록 파일
   하나를 지정하거나 승인했을 때만 같은 Doctor에 `--session-evidence "<세션 기록 .jsonl>"`을 추가한다. 기록은 보통
   `~/.claude/projects/<프로젝트 경로로 만든 폴더 이름>/<세션 ID>.jsonl`에 있다(폴더 이름은 전체 경로에서
   영문자·숫자가 아닌 글자를 `-`로 바꾼 것이다. 예: `C:\work\vault` → `C--work-vault`). 기록 파일을 다른 도구로
   열어 읽지 않는다(입력·답변·훅 출력이 모두 들어 있다). `session_evidence`의 `status`(`observed_ran`·
   `observed_truncated`·`observed_error`·`not_recorded`·`unsupported_host_version`·`unparsed`), 훅별
   `emitted_chars`·`injected_chars`·`truncated`, 실패한 훅의 `outcome`·`exit_code`, 판 범위를 보고한다.
   `observed_error`는 훅이 실행되지 않았다거나 기록이 없다는 뜻이 아니라 성공하지 못한 훅 기록이 있다는 뜻이므로
   그 훅을 확인하도록 안내한다. `not_recorded`를 "실행되지 않음"으로, 첫 응답 토큰을 훅의
   몫으로, 기록을 "모델이 기억을 사용함"으로 해석하지 않는다. 지침 파일 경로는 사용자가 요청할 때만
   `--session-instruction-paths`로 보인다. 종료 코드는 바뀌지 않는다.
   [읽는 값과 한계](../docs/reliability.md#호스트-버전과-sessionstart-실측)
