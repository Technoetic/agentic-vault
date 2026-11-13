# Antigravity CLI에서 쓰기

agentic-vault v0.48.0부터 저장소의 `antigravity/` 폴더에 Google Antigravity CLI(`agy`)용 플러그인 패키지가 들어
있다. 이 폴더는 저장소의 명령·스킬·훅·엔진 스크립트로 만든 생성물이며, 저장소 루트는 지금처럼 Claude Code·Codex
플러그인이다. 같은 볼트를 세 호스트(Claude Code·Codex·Antigravity CLI)에서 쓰도록 만든 패키지다. 이 패키지는 실제
agy 대화 세션으로는 확인하지 않았다(아래 [확인하지 않은 것](#확인하지-않은-것)). 변경 범위는
[v0.48.0 릴리스 노트](https://github.com/Technoetic/agentic-vault/blob/v0.48.0/docs/releases/v0.48.0.md)에 있다.

## 설치

터미널에서 릴리스 태그와 패키지 폴더를 고정한 주소로 설치한다.

```text
agy plugin install https://github.com/Technoetic/agentic-vault/tree/v0.48.0/antigravity
```

Release ZIP을 풀었거나 저장소를 clone했다면 그 안의 `antigravity` 폴더를 지정해도 된다. 경로는 실제 위치로 바꾼다.

```text
agy plugin install "C:/path/to/agentic-vault/antigravity"
```

설치한 뒤 `agy plugin list --json`에서 `agentic-vault` 항목의 `source`가 `"antigravity"`, `version`이 `0.48.0`이고
스킬 17개와 훅 묶음 `agentic-vault`가 보이는지 확인한다.

저장소 루트 주소(태그만 고정한 주소 포함)로는 설치하지 않는다. agy는 루트를 Claude Code 플러그인으로 보고 자동
변환해 설치한다. 이때 명령은 `agentic-vault-cmd-*` 스킬이 되고 본문의 `${CLAUDE_PLUGIN_ROOT}`·`$ARGUMENTS`가 그대로
남으며, compact SessionStart·PreCompact·SessionEnd 훅은 버려진다. 변환된 시작 훅은 플러그인 폴더에서 실행되어
볼트를 찾지 못하므로 인계가 주입되지 않는 것으로 본다(재현 실행에서 출력 0바이트, 실제 agy 세션에서는 재지 않았다).

## 갱신

새 판으로 바꿀 때는 주소의 태그만 바꿔 같은 설치 명령을 다시 실행한다. agy는 설치 폴더
(`~/.gemini/config/plugins/agentic-vault`)를 새 내용으로 바꾼다. 이 폴더는 읽기 전용 사본이므로 직접 고치지 않는다.
볼트에 설치된 규칙·healthcheck 사본·생성 `AGENTS.md`는 플러그인 갱신과 별도로 `vault-upgrade` 스킬로 갱신한다.

## 무엇이 들어 있나

| 구성 | 내용 |
|---|---|
| `plugin.json` | 이름 `agentic-vault`, 판, 호스트 중립 설명, 제안 프롬프트 셋 |
| 스킬 17개 | 공용 스킬 `agentic-vault`·`webapp-principles`와, 명령 15개를 같은 이름으로 바꾼 스킬(`vault-init`, `vault-upgrade`, `vault-session-start`, `vault-session-end`, `vault-recall`, `vault-lint`, `vault-day`, `vault-ingest`, `vault-process-inbox`, `vault-trace`, `vault-judge`, `jev-ask`, `vault-doctor`, `vault-harden`, `vault-jarvis-setup`). 명령 스킬에는 `disable-model-invocation: true`가 붙는다 |
| 훅 둘 | `SessionStart`(인계 주입)와 `Stop`(체크포인트). agy 목록에는 훅 묶음 `agentic-vault` 하나로 보인다 |
| 엔진과 자료 | `skills/agentic-vault/scripts/`의 엔진 스크립트, `hooks/`의 훅 파일 넷, `assets/` 전체, 스킬이 가리키는 `docs/` 문서의 사본 |

명령 스킬 본문 앞의 머리말은 두 자리표시자를 설명한다. `<PLUGIN_ROOT>`는 이 플러그인 설치 폴더의 절대 경로(그
SKILL.md가 있는 폴더의 두 단계 위, 보통 `~/.gemini/config/plugins/agentic-vault`)이고, `<ARGUMENTS>`는 사용자가
슬래시 명령 뒤에 적은 텍스트다(없으면 이번 요청에서 찾는다). 볼트 루트는 작업 공간에서 `00-meta/vault-config.json`이
있는 폴더이며, 볼트를 다루는 엔진 스크립트에는 `--vault`로 넘긴다. 본문에 적힌 명령의 인자는 그대로 쓴다. 본문의
`/vault-*` 표기는 같은 이름의 스킬을 뜻한다.

agy 검사기는 24,000바이트를 넘는 스킬 본문에 경고하므로 `agentic-vault`·`vault-init`·`vault-upgrade` 스킬은 일부
절을 그 스킬의 `references/` 파일로 옮겼다. 저장소의 원본은 나누지 않는다. 패키지에 없는 문서로 가는 링크는 같은
태그의 GitHub 주소로 바뀌어 있다.

## 훅 동작

agy는 플러그인 훅을 플러그인 설치 폴더에서 실행하고 작업 공간 정보를 표준 입력의 JSON으로 준다. 두 훅은
`--host antigravity` 플래그로 이 형식을 읽는다. 볼트는 입력의 `cwd`, 그다음 `workspacePaths`의 각 항목 가운데
`00-meta/vault-config.json`이 일반 파일로 있는 절대 경로로 정한다. 정확히 하나일 때만 동작하고, 없거나 둘
이상이면 아무것도 하지 않는다(SessionStart 훅은 둘 이상일 때 표준 오류에
`agentic-vault: several vault candidates; nothing injected` 한 줄을 남긴다). `CLAUDE_PROJECT_DIR`와 훅 프로세스의
현재 폴더(플러그인 폴더)는 쓰지 않는다. 입력은 16 MiB까지만 읽는다.

- **SessionStart:** Claude Code에서 세션을 새로 시작할 때(`startup`)와 같은 설정 검증·예산·9,500자 상한으로
  handoff·hot을 묶어 `injectSteps`의 시스템 메시지 하나로 넣는다. 오류가 나면 고정 코드 한 줄만 표준 오류에 내고
  아무것도 넣지 않는다. 종료 코드는 언제나 0이다.
- **Stop:** 에이전트가 완전히 멈췄을 때(`fullyIdle`가 참)만, 볼트의 `00-meta/.agentic-vault/runtime/`에 사건
  이름 `Stop`의 체크포인트를 남긴다. 문서는 Stop이 실행 루프가 끝날 때 온다고 적는다. 같은 대화 ID와 실행
  번호(`executionNum`)의 Stop이 120초 안에 다시 오면 기록하지 않는다(아래 처리기 셋의 표지). 체크포인트에는
  노트의 볼트 기준 상대 경로·크기·해시, 설정 파일 해시, 사건 이름과 시각이 들어가고 노트 내용은 들어가지 않는다.
  대화 ID는 해시로만 남으며, 훅 입력의 `lastUserInput`(사용자가 마지막에 입력한 글)·`transcriptPath`(대화 기록
  경로)·작업 공간 경로는 저장하지 않는다. 언제나 `{}`를 내고 0으로 끝난다.
- **처리기 셋:** Windows에서 훅 명령을 실행하는 셸이 문서에 없어, 사건마다 같은 일을 하는 처리기 셋을 이 순서로
  둔다.

```text
sh ./hooks/run_python_hook.sh ./hooks/session_start.py --host antigravity
py -3 ./hooks/session_start.py --host antigravity
python ./hooks/session_start.py --host antigravity
sh ./hooks/run_python_hook.sh ./hooks/session_checkpoint.py --host antigravity
py -3 ./hooks/session_checkpoint.py --host antigravity
python ./hooks/session_checkpoint.py --host antigravity
```

먼저 동작한 처리기가 시스템 임시 폴더 안의 표지 폴더(이름이 `agentic-vault-hook-claims`로 시작한다)에 표지
파일을 만든다. 표지는 120초 동안만 유효하다. 같은 키(SessionStart는 대화 ID, Stop은 대화 ID와 실행 번호)의
사건이 120초 안에 다시 오면 처리하지 않고, 그보다 오래된 표지는 낡은 것으로 보아 다시 처리한다. 그래서 같은
사건에 처리기 셋이 함께 와도 하나만 동작하고, 같은 대화를 다시 열거나 같은 실행 번호가 다시 쓰여도 2분이 지나면
다시 처리한다. `executionNum`의 범위(대화 전체에서 이어지는지, 턴마다 다시 매기는지)와 대화를 다시 열 때
SessionStart가 다시 오는지는 문서에 없고 확인하지 않았다. 실행기(`sh`, `py`, `python`)가 없는 처리기는
실패하며, agy는 0이 아닌 종료 코드를 오류로 기록한 뒤 무시한다. 그래서 훅 오류 기록이 남아도 셋 가운데 하나가
동작했으면 정상이다. 표지 파일을 만들 수 없으면 처리기가 각자 동작해 같은 사건을 두 번 이상 처리할 수 있다.
이틀이 지난 표지 파일은 다음 훅이 지운다(한 번에 최대 200개를 살핀다).

Antigravity CLI의 훅 사건에는 압축과 세션 종료가 없다. 압축 뒤에는 인계를 다시 넣지 않으므로, 필요하면
`vault-session-start` 스킬로 다시 복원한다. Claude Code의 PreCompact·SessionEnd 체크포인트 대신 위의 Stop
체크포인트가 남으며, healthcheck의 선택형 checkpoint 이후 변경 경고는 이 기록도 읽는다([hardening.md](hardening.md#checkpoint-이후-바뀐-엔진-노트)).

## 볼트에서 읽는 규칙

Antigravity CLI는 시작 폴더부터 저장소 루트까지의 `AGENTS.md`·`GEMINI.md`·`.agents/rules/*.md`와 전역·플러그인
규칙을 읽는다. 볼트의 `CLAUDE.md`는 적재 위치에 없다. 그래서 볼트 규칙은 `/vault-init`이 만들고 `vault-upgrade`가
다시 만드는 생성 `AGENTS.md`(엔진 규칙 여섯과 스텁)로 전달되고, `CLAUDE.md`의 관리 블록 밖 사용자 규칙은 그
스텁의 안내에 따라 읽는다. Antigravity CLI는 `@` 포함을 펼친 뒤 24,000바이트를 넘는 규칙 파일을 자르며, 생성
`AGENTS.md`가 그 안에 드는지는 시험이 지킨다. 볼트에 생성 `AGENTS.md`가 없으면 먼저 `vault-upgrade`로 만든다.

## 직접 해 보는 확인

이 절차는 사용자가 자기 터미널에서 직접 한다. 에이전트가 agy를 감싸 실행하지 않는다(아래 [약관 메모](#약관-메모)).
대화는 모델을 호출하므로 크레딧을 쓰고 입력과 볼트 내용이 Google로 간다.

1. 개인 자료가 없는 시험용 볼트를 만든다(Antigravity CLI의 `vault-init` 스킬이나 Claude Code의 `/vault-init`).
   handoff 노트에 알아보기 쉬운 문장 하나를 적는다.
2. 그 볼트 폴더에서 agy를 직접 연다.
3. `/hooks`에서 훅 묶음 `agentic-vault`와 `SessionStart`·`Stop`이 보이는지, `/skills`에서 `agentic-vault`와
   `vault-session-start` 등 17개 스킬이 보이는지 확인한다.
4. 첫 메시지로 "세션 시작 때 주입된 인계 내용을 그대로 알려 줘"처럼 묻는다. 에이전트가 답하기 전에
   도구(`view_file`·`run_command`·`grep_search` 등)를 하나도 부르지 않았고 1에서 적은 문장이 나오면 SessionStart
   주입이 동작한 것으로 본다. 도구를 불렀다면 에이전트가 handoff를 직접 읽었을 수 있으므로(주입이 없으면 볼트의
   `AGENTS.md`와 `vault-session-start` 스킬이 직접 복원하라고 한다) 문장이 나와도 주입은 실패한 것으로 보고 훅
   오류를 확인한다. 문장이 나오지 않을 때도 같다. 문서는 agy가 0이 아닌 종료 코드로 끝난 훅을 오류로 기록한다고
   적지만, 그 기록을 보는 곳은 적지 않는다. `/hooks`(불러온 훅 목록)와, agy를 `--log-file <경로>`로 열었다면 그
   로그 파일에서 찾아본다. 두 곳에 훅 오류가 실제로 남는지는 확인하지 않았다. 주입이 되지 않았으면
   `vault-session-start` 스킬로 복원한다.
5. 답을 받은 뒤 볼트의 `00-meta/.agentic-vault/runtime/`에 `checkpoint-`로 시작하는 JSON 파일이 생겼는지 본다.

결과를 제보할 때는 볼트 내용 없이 agy 판, 운영체제, 어느 단계가 어떻게 됐는지만 적는다.

## 하지 않는 것

- Jarvis(Telegram) 브리지는 Claude CLI(`claude`)로만 응답을 만든다. agy만 설치한 환경에서 Jarvis 진단은 Claude
  CLI가 없다고 보고한다(`jarvis_claude_cli_unavailable`).
- deny zone 가드, 교훈 단서 훅, Aside 자동 기동 훅은 Claude Code의 PreToolUse 훅이라 패키지에 넣지 않았다.
  Antigravity 세션에서는 이 훅들이 돌지 않는다. `vault-init`의 가드·단서 설치 절은 그 스킬의
  `references/claude-code-hooks.md`로 옮겨 두었고, 같은 볼트를 Claude Code에서도 쓰며 사용자가 따로 승인했을 때만
  따른다.
- `vault-doctor`(`vault_doctor.py`)는 agy 설치와 Antigravity 플러그인 상태를 진단하지 않는다. `--plugin-scope`는
  Claude Code 설정만, `--session-evidence`는 Claude Code 세션 기록만 읽는다.
- Claude Code 전용 단계(PreToolUse 훅 설치, 설정 파일 `.claude/settings.json`·`.claude/settings.local.json` 병합)는
  Antigravity 세션에서 실행하지 않는다. `.claude/rules/`와 CLAUDE.md 스텁, `AGENTS.md`는 공통 절차대로 만든다.

## 권한

볼트의 Claude Code 권한 템플릿(`settings-permissions.json`의 deny zone 읽기 차단)은 Antigravity CLI로 옮겨지지 않는다.
Antigravity CLI의 권한 규칙은 `/permissions`의 Global·Shared·Project 범위를 합쳐 적용되고, Project 범위가 저장되는
파일 위치는 문서에 없어 플러그인이 볼트에 넣어 줄 수 없다. 필요하면 사용자가 `/permissions`의 Project 범위에 볼트의
절대 경로로 규칙을 넣는다.

- `.env`·`.obsidian/`처럼 에이전트가 읽지도 쓰지도 않아야 할 곳에는 `read_file` deny를 권한다. `read_file` deny는
  같은 경로의 쓰기도 막고, 모든 모드와 `--dangerously-skip-permissions`에서도 유지된다. Windows에서는 드라이브
  문자를 떼고 `/`로 바꾼 경로로 맞춘다.
- 볼트 절차가 파일을 옮겨 넣는 구역(`10-inbox/_processed/`, `90-assets/`, 아카이브 폴더 등)에는 deny를 걸지
  않는다. 쓰기 도구(`write_file`)까지 막혀 `vault-process-inbox`의 원본 격리 같은 절차가 실패할 수 있다(셸 명령으로
  옮기는 경우도 막히는지는 확인하지 않았다). 이런 구역은 엔진 스크립트의 검사와 규칙 문구로만 지켜지며, 파일
  시스템 권한이 아니다.
- agy 1.5.7부터 저장된 모드가 없는 세션은 Auto를 쓸 수 있는 곳에서 Auto 모드로 시작한다(CHANGELOG). 쓰기를 하는
  명령 스킬을 쓰기 전에 모드를 확인한다.

## 데이터

Antigravity CLI는 개인 Google 계정에서 상호작용 데이터 수집에 동의한 것으로 보고 시작하며, 설정에서 끌 수
있다(opt-out). 이 설정은 계정에 저장되고, 비즈니스 계정에서는 수집이 늘 꺼져 있다. 훅이 주입한 handoff·hot과
에이전트가 읽은 볼트 노트는 대화의 일부로 Google로 전송된다. 밖으로 보내면 안 되는 자료가 있는 볼트에서는 쓰기
전에 그 범위를 정하고, 그 자료가 있는 폴더를 위의 `read_file` deny로 막는다.

## 약관 메모

Antigravity FAQ는 소비자 요금제에서 agy를 제3자 코딩 에이전트·앱으로 감싸 쓰는 것을 지원하지 않으며, 제3자
소프트웨어로 Antigravity 로그인에 접근하면 약관 위반이라고 적는다. 제3자 에이전트에는 Gemini Enterprise나 AI Studio
API 키를 권한다. 소비자 요금제에서는 agy를 다른 앱·에이전트(Claude Code, Codex, Jarvis 브리지 등) 안에서 실행하지
않는다. 이 플러그인의 스킬·훅·Jarvis 브리지는 agy를 실행하지 않는다. 유지관리용
`scripts/verify_antigravity_plugin.py`는 격리한 가짜 홈 폴더에서 `agy --version`과 로그인이 필요 없는 `agy plugin`
명령(검사·설치·목록)만 실행하며, 대화와 `-p` 실행은 하지 않는다.

## 확인하지 않은 것

실제 agy 대화 세션이 필요해 확인하지 않았다.

- `injectSteps`의 시스템 메시지가 실제로 대화에 들어가는지, 압축·재개 뒤에도 남는지, 크기 상한이 얼마인지
- Windows에서 훅 명령을 실행하는 셸과 `sh`가 필요한지(그래서 처리기를 셋 둔다)
- 플러그인 스킬의 슬래시 표기와 인자: 공식 문서는 스킬마다 `/<스킬 이름>` 슬래시 명령이 생긴다고
  적는다(`/vault-day`). 플러그인 스킬에 이름공간이 붙는지와, 뒤에 적은 텍스트가 스킬에 어떻게 전달되는지는 문서에
  없고 확인하지 않았다. agy가 보여 주는 표기를 쓴다.
- 에이전트가 `<PLUGIN_ROOT>`를 설치 폴더의 절대 경로로 맞게 푸는지
- `executionNum`의 범위와 대화를 다시 열 때 SessionStart가 다시 오는지(그래서 표지는 120초 동안만 유효하다)
- agy가 훅 오류를 `/hooks`나 `--log-file`로 정한 로그에 남기는지

패키지 검사와 격리 설치는 Windows의 agy 1.5.7에서만 했고, macOS·Linux의 agy는 실행하지 않았다. agy가 설치할 때
패키지 파일의 실행 권한을 보존하는지도 확인하지 않았다. macOS·Linux에서 이 패키지의 `vault-init`·`vault-upgrade`
스킬로 볼트에 넣은 git 훅(`00-meta/scripts/git-hooks/pre-commit`·`pre-push`)이 커밋과 push 때 돌지 않으면, git은
실행 권한이 없는 훅을 건너뛰므로 두 파일에 `chmod +x`로 실행 권한을 준다.

## agy 판

agy 1.5.6 이상을 권장한다. 패키지는 agy 1.5.7의 검사기(`agy plugin validate --strict`)와 격리 설치로 확인했고,
그보다 낮은 판은 시험하지 않았다. 훅 입력의 `cwd`·`hookEventName`은 1.5.4부터 들어오며, 그 전 판에서는
`workspacePaths`로만 볼트를 찾는다.

참고한 공식 자료: [Build plugins](https://antigravity.google/docs/build-plugins),
[Hooks](https://antigravity.google/docs/hooks),
[Customization discovery](https://antigravity.google/docs/customization-discovery),
[Plugins](https://antigravity.google/docs/plugins), [Rules](https://antigravity.google/docs/rules),
[Permissions](https://antigravity.google/docs/permissions),
[`/permissions` 명령](https://antigravity.google/docs/cli/commands/permissions),
[Settings](https://antigravity.google/docs/settings), [CLI reference](https://antigravity.google/docs/cli/reference),
[Skills](https://antigravity.google/docs/skills), [FAQ](https://antigravity.google/docs/faq),
[antigravity-cli README·CHANGELOG](https://github.com/google-antigravity/antigravity-cli)(2026-11-13에 읽음). 보안
범위와 알려진 한계는 [SECURITY.md](https://github.com/Technoetic/agentic-vault/blob/v0.48.0/SECURITY.md), Claude Code·Codex 설치는 [README](https://github.com/Technoetic/agentic-vault/blob/v0.48.0/README.md#-설치)와
[Codex 안내](codex.md)에 있다.
