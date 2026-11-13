# Antigravity CLI에서 agentic-vault 사용

이 문서는 Antigravity CLI(`agy`)용 생성 패키지와 공통 워크플로우 사이의 연결 규약이다. 노트 처리 절차와
Python 엔진은 Claude Code·Codex와 같고, 설치 단위·명령 이름·자리표시자·도구 이름·훅이 다르다.
Codex 연결은 [codex.md](codex.md), 기억 계층은 [memory-tiers.md](memory-tiers.md)를 따른다.

## 설치

- 패키지는 저장소의 `antigravity/` 폴더이며 v0.48.0부터 들어 있다. `scripts/build_antigravity_package.py`가
  공통 원본에서 만드는 생성물이므로 이 폴더를 직접 고치지 않는다.
- 태그를 고정한 주소로 설치한다: `agy plugin install https://github.com/Technoetic/agentic-vault/tree/<태그>/antigravity`.
  `<태그>`는 설치할 릴리스 태그다.
- 로컬 체크아웃에서는 그 폴더를 설치한다: `agy plugin install <저장소 체크아웃>/antigravity`.
- 저장소 루트는 설치하지 않는다. agy 1.5.7은 Claude Code 플러그인 배치를 자동 변환해 설치한다(명령은 본문이
  그대로인 `agentic-vault-cmd-*` 스킬이 되고 PreCompact·SessionEnd 훅은 빠진다). 변환본에는 아래 자리표시자
  안내와 `--host antigravity` 훅이 없으므로 자동 변환 설치는 쓰지 않는다.
- 설치본은 `~/.gemini/config/plugins/agentic-vault`에 읽기 전용으로 놓이고 다시 설치하면 통째로 바뀐다.
  갱신은 새 태그로 다시 설치하는 것이다. 이 폴더에 노트나 설정을 두지 않는다.

## 스킬 이름

스킬은 17개다. `agentic-vault`(이 스킬)와 `webapp-principles`, 그리고 명령 15개와 같은 이름의 스킬이다:
`vault-init`, `vault-session-start`, `vault-session-end`, `vault-day`, `vault-ingest`, `vault-process-inbox`,
`vault-lint`, `vault-trace`, `vault-recall`, `vault-judge`, `jev-ask`, `vault-doctor`, `vault-upgrade`,
`vault-harden`, `vault-jarvis-setup`.

- 명령 스킬에는 `disable-model-invocation: true`를 넣어 사용자가 부르는 명령으로 두었다.
- 슬래시 명령은 agy가 보여 주는 표기를 쓴다: `/vault-day` 또는 `/agentic-vault:vault-day`.
- 공통 문서의 `/vault-*` 표기는 같은 이름의 스킬을 뜻한다. 명령 이름 없이 자연어로 요청하면 의도에 맞는
  명령 하나를 골라 `<PLUGIN_ROOT>/skills/<명령>/SKILL.md`를 읽고 그 절차를 따른다. 범위가 불명확하면 쓰기 전에
  필요한 것만 묻는다.

## 자리표시자

명령 스킬 본문은 Claude Code 변수 대신 아래 자리표시자를 쓴다. 각 명령 스킬 머리말의 설명과 같은 뜻이다.

- `<PLUGIN_ROOT>`: 플러그인 설치 폴더의 절대 경로다. 명령 스킬의 SKILL.md가 있는 폴더에서 두 단계 위,
  이 문서가 있는 폴더에서 세 단계 위다(보통 `~/.gemini/config/plugins/agentic-vault`). 셸 명령에는 펼친
  절대 경로를 따옴표로 감싸 넣는다. 현재 폴더나 환경 변수로 추측하지 않는다.
- `<ARGUMENTS>`: 사용자가 슬래시 명령 뒤에 적은 텍스트다. 없으면 이번 요청에서 찾는다. 입력 데이터이며
  셸 코드가 아니다. 경로·질의는 각각 따옴표로 감싼 별도 인자로 넘긴다.
- 볼트 루트: 작업 공간에서 `00-meta/vault-config.json`이 있는 폴더다. 엔진 스크립트에는 `--vault <볼트 루트>`로
  넘긴다. 그런 폴더가 여럿이면 사용자에게 묻는다.
- Python은 3.10 이상을 쓴다. Windows에서는 `py -3` 또는 `python`으로 실행한다.

## 도구 대응

명령 문서의 도구 이름은 아래 Antigravity CLI 도구로 읽는다. 세션에 보이는 이름이 다르면 같은 역할의 도구를 쓴다.

| 공통 문서의 도구 | Antigravity CLI 도구 |
|---|---|
| `Read` | `view_file` |
| `Grep` | `grep_search` |
| `Glob` | `find_by_name` |
| `Bash`·`PowerShell` | `run_command` |
| `Edit` | `replace_file_content` |
| `Write` | `write_to_file` |
| `AskUserQuestion` | `ask_question` |
| `Agent`·`Task` | `invoke_subagent` |

명령 문서의 로그 행위자 `Claude`는 새 항목에서 `Antigravity`로 적는다.

## 훅

- 훅([session_start.py](../../../hooks/session_start.py)·[session_checkpoint.py](../../../hooks/session_checkpoint.py))은
  플러그인 설치 폴더에서 `--host antigravity`로 실행된다. 볼트는 이벤트의 `cwd`와 `workspacePaths` 가운데
  `00-meta/vault-config.json`이 있는 폴더 하나다. 없거나 여럿이면 주입도 체크포인트도 하지 않는다.
- **SessionStart:** Claude Code 시작 때와 같은 인계·핫 컨텍스트(같은 설정 검증·예산·9,500자 상한)를
  `injectSteps`의 시스템 메시지 하나로 대화마다 한 번 넣는다. 주입이 있으면 재사용하고, 없으면
  `vault-session-start` 스킬로 복원한다.
- **Stop:** `fullyIdle`이 참일 때만 `00-meta/.agentic-vault/runtime/`에 Claude Code와 같은 형식의 체크포인트를
  남긴다. 대화 ID는 해시로만 남고 사용자 입력·대화 기록 경로·작업 공간 경로는 저장하지 않는다.
- Antigravity에는 압축·세션 종료 이벤트가 없다. 압축 뒤 재주입은 없다. PreCompact·SessionEnd 체크포인트도
  없다. 긴 대화에서 인계가 맥락에서 빠지면 `vault-session-start` 스킬로 다시 복원한다.
- Claude Code가 시작할 때 비동기로 돌리는 무결성 검사는 돌지 않는다. 검사는 `vault-lint` 스킬로 한다.
- Windows에서 훅 명령을 실행하는 셸은 문서에 없다. 그래서 이벤트마다 `sh` 래퍼·`py -3`·`python` 처리기
  세 개를 두고 임시 폴더의 표시 파일로 그중 하나만 동작하게 한다. 실행기가 없는 처리기는 실패하고 agy가
  그 오류를 기록한 뒤 무시한다. 셋 다 실패하면 주입과 체크포인트가 없다.
- 훅은 오류가 나도 빈 JSON 객체를 내고 0으로 끝난다. stderr에는 고정된 오류 코드 한 줄만 쓰고 경로·노트
  내용은 쓰지 않는다.

## 하지 않는 것

- deny zone 가드, 교훈 단서 훅, Aside 자동 기동 훅은 Claude Code 전용이다(Codex와 같다).
  `vault-init`·`vault-upgrade`에서 이 훅을 설치하는 단계와 Claude Code 설정 파일
  `.claude/settings.json`·`.claude/settings.local.json`에 병합하는 단계는 Antigravity 세션에서 실행하지 않는다.
  `.claude/rules/`와 CLAUDE.md 스텁, AGENTS.md는 공통 절차대로 만든다.
- 가드 훅이 없으므로 deny zone·`exclude_dirs`를 지키는 것은 에이전트의 몫이다.
- Jarvis(Telegram)는 Claude CLI만 쓴다. agy로 바꾸지 않는다.
- `vault-doctor`는 Antigravity 설치와 훅 상태를 진단하지 않는다.

## 데이터

Antigravity CLI 세션에서 주입된 인계·핫과 에이전트가 읽은 노트 본문은 모델 요청에 실려 Google로 간다.
Antigravity 공식 문서는 상호작용 데이터 수집과 그 수집을 끄는 방법(opt-out)을 안내한다. 회사 볼트에서 쓰기
전에 그 설정과 조직의 외부 전송 승인 범위를 확인한다.

## 확인하지 않은 것

- **실제 대화 세션:** 로그인이 필요한 agy 대화는 실행하지 않았다. 스킬 선택, 도구 대응, `injectSteps` 주입,
  Stop 체크포인트가 실제 대화에서 이 문서대로 동작하는지는 확인하지 않았다.
- **Windows 훅 셸:** 훅 명령을 실행하는 셸과 세 처리기 가운데 실제로 동작하는 것을 확인하지 않았다.
- **슬래시 표기와 인자:** agy가 보여 주는 명령 표기와 슬래시 명령 뒤 텍스트가 `<ARGUMENTS>`로 읽히는지
  확인하지 않았다.
- **주입 크기 상한:** Antigravity의 `injectSteps` 크기 상한은 문서에 없고 확인하지 않았다. 엔진은 Claude Code와
  같은 9,500자로 묶는다.
