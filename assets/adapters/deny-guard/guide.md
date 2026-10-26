# deny zone 가드 (선택, Claude Code 전용)

이 폴더의 `deny-guard.py`는 Claude Code의 PreToolUse 훅으로 도는 선택형 가드다. 도구 호출이 볼트의 deny zone
(`00-meta/vault-config.json`의 `deny_zones`)을 가리키면 `deny`로 막거나 `ask`로 사용자 확인을 받는다. 플러그인은 이 훅을
켜지 않는다(`hooks/hooks.json`에 없다). 사용자가 아래 「켜는 법」대로 직접 병합해야만 동작한다.

가드이지 샌드박스가 아니다. 파일 시스템 권한을 바꾸지 않고, 「한계」에 적은 경로는 보지 못한다. 기존 Read deny 규칙
(`assets/templates/settings-permissions.json`을 병합한 것)을 대신하지 않고 그 위에 더하는 보조 장치다.

## 판정표

| 도구 | 보는 입력 | deny | ask | 결정 없음 |
|---|---|---|---|---|
| Read·Edit·Write·NotebookEdit | 키가 `path`로 끝나는 모든 문자열(`file_path`·`notebook_path`) | 경로가 구역 안(정션·링크를 푼 실제 경로 포함) | 경로 필드가 없는 입력. Edit·Write·NotebookEdit으로 가드 제어 파일을 바꿈 | 그 밖 |
| Glob | `path`, `pattern` | `path`가 구역 안. 패턴의 고정 앞부분이 구역 안. 와일드카드 뒤의 한 단계 구역 이름(`**/90-assets/**`) | 와일드카드 옆의 구역 이름(`**/_processed/*`) | 구역 이름 없이 볼트 전체를 도는 검색(`**/*.md`) |
| Grep | `path`, `glob`(`pattern`은 찾을 글자라 보지 않는다) | `path`나 `glob`이 구역 안 | `glob` 와일드카드 옆의 구역 이름 | `!90-assets/**` 같은 제외 glob, 경로 없는 검색 |
| Bash | `command` | 글자 그대로의 단어(리디렉션 대상 포함)가 구역 안 | 구역 이름이나 제어 파일 이름이 보이는 명령 가운데 실행 중에 경로·명령을 만듦(`$VAR`·`$(...)`·백틱·`eval`·`source`·`sh -c`·`python -c` 같은 인라인 코드), `cd` 뒤의 상대 경로, 와일드카드 옆의 구역 이름, 따옴표가 맞지 않음, 주석처럼 경로로 풀 수 없는 구역 이름, 가드 제어 파일을 가리킴. 그리고 셸 작업 폴더가 구역 안 | 구역 이름도 제어 파일 이름도 없는 명령 |
| PowerShell | `command` | — | 구역 이름이나 제어 파일 이름이 보임(아직 구문 분석하지 않는다), 작업 폴더가 구역 안 | 그 밖 |

- **Bash 예외(구역을 열지 않는 단어):** `mv`·`git mv`의 목적지와 `mkdir` 폴더(볼트 규칙의 격리 이동 — `/vault-process-inbox`·`/vault-lint`가
  처리한 원본을 셸 move로 구역에 옮긴다), 표준 출력을 파일로 보내는 `echo`·`printf`의 글자(`echo '90-assets/' >> .gitignore`),
  `git commit`·`git tag`의 `-m` 메시지, 제외 옵션 값(`--exclude-dir=90-assets`, `rg -g '!90-assets/**'`). 파이프나 `$(...)`로 넘어가는
  `echo` 출력은 경로가 될 수 있어 예외가 아니다. 파일을 읽는 옵션(`--exclude-from`·`--ignore-file`)과 값이 없는 옵션
  (`--exclude-standard`·`--ignore-case`·`--no-ignore`)은 제외 옵션으로 보지 않는다. `mv`의 `-t`가 다른 짧은 옵션과 붙은 형태(`-vt`)나
  `git -C`처럼 하위 명령 앞에 옵션 값이 오는 형태는 예외로 보지 않고 그대로 검사한다.
- **가드 제어 파일(자기 보호):** `00-meta/vault-config.json`, `00-meta/scripts/deny-guard.py`, `.claude/settings*.json`과 `.claude` 폴더
  (볼트 밖 사용자 설정 `~/.claude/settings.json` 포함). 이것을 바꾸면 가드를 끌 수 있으므로 `ask`로 확인한다. Read로 읽는 것은 그대로 둔다.
- **구역 비교:** 플러그인 `vault_paths.zone_matches`를 그대로 복사해 쓴다. 한 단계 구역(`90-assets`)은 어느 깊이의 같은 이름 폴더든,
  여러 단계 구역(`10-inbox/_processed`)은 볼트 루트에서 시작할 때만 맞고, 대소문자는 무시한다. 경로는 글자 그대로의 경로와
  `realpath`로 푼 경로를 둘 다 볼트 루트(주어진 경로와 `realpath`로 푼 경로)에 대고 `normcase`로 비교한다. 그래서 구역 밖 폴더가
  정션으로 구역을 가리켜도, 구역 자체가 볼트 밖을 가리키는 정션이어도 잡힌다. Windows에서는 MSYS 경로(`/d/vault/...`)를
  `D:/vault/...`로 바꾸고, `\\?\` 접두사와 끝의 점·공백, `:` 뒤의 스트림 이름을 Windows가 여는 방식대로 접는다.

## 출력

- 결정이 없으면 아무것도 출력하지 않고 종료 코드 0으로 끝난다. Claude Code의 일반 권한 흐름(Read deny 규칙·승인 창)이 그대로 진행된다.
- 결정이 있으면 JSON 한 줄을 출력하고 0으로 끝난다:
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "deny-guard: ..."}}`
  (`permissionDecision`은 `deny` 또는 `ask`). 이유는 고정 문구에 구역 이름만 넣는다. `deny` 이유는 Claude에게, `ask` 이유는 승인 창에서
  사용자에게 보인다.
- `allow`는 내지 않는다. 그래서 이 가드는 권한을 넓히지 못하고 좁히기만 한다. 여러 훅의 결정은 deny > defer > ask > allow 순서로
  합쳐지고, 설정 파일의 deny·ask 규칙은 훅 결과와 상관없이 평가된다.
- `CLAUDE_PROJECT_DIR`가 없거나 폴더가 아니면 stderr에 한 줄을 쓰고 결정하지 않는다. `00-meta/vault-config.json`을 읽지 못하거나
  `deny_zones`가 목록이 아니거나 상대 경로가 아닌 항목이 있으면 템플릿 기본 구역 다섯 개로 판정하고 stderr에 한 줄을 쓴다(`deny_zones`
  키가 없으면 healthcheck처럼 기본 구역을 조용히 쓴다). 입력 JSON을 읽지 못하거나 가드 안에서 예외가 나면 `ask`를 낸다.

## 켜는 법 (수동 병합)

1. Claude Code가 보는 PATH의 `python`이 Python 3.10 이상인지 확인한다: `python -c "import sys; print(sys.version)"`. Windows 스토어 별칭만
   있거나 `py` 런처만 있으면 아래 `command`를 python.exe 절대 경로로 바꾸거나 `"command": "py"`로 두고 `args` 맨 앞에 `"-3"`을 넣는다.
2. 이 폴더의 `deny-guard.py`를 볼트의 `00-meta/scripts/deny-guard.py`로 바이트 그대로 복사한다(첫 줄의
   `agentic-vault:adapter deny-guard engine=` 스탬프를 지우지 않는다).
3. `settings-hooks.json`의 `PreToolUse` 그룹을 볼트 `.claude/settings.local.json`의 `hooks.PreToolUse` 배열에 추가한다. 기존 키와 훅은 모두
   남긴다. 기기마다 다른 설정이므로 공유 `settings.json`이 아니라 `settings.local.json`이 기본이다. 폴더 동기화를 쓰는 볼트는
   local 파일도 다른 기기로 퍼진다.
4. Claude Code의 `/hooks`에서 훅이 보이는지 확인하고, 볼트 루트의 Git Bash에서 시험 입력으로 판정을 본다. `"permissionDecision": "deny"`가
   든 JSON 한 줄이 나와야 한다.

   ```bash
   echo '{"tool_name":"Read","tool_input":{"file_path":"90-assets/x.png"}}' | CLAUDE_PROJECT_DIR="$PWD" python -X utf8 -I 00-meta/scripts/deny-guard.py
   ```

훅 항목의 뜻:

- `matcher`는 정확한 도구 이름 목록이다(글자·`|`만 있으면 정규식으로 보지 않는다).
- `if` 필터를 쓰지 않는다. 문서상 `if`는 최선 노력(best-effort) 필터라 강제 수단으로 쓰지 말라고 되어 있으므로, 대상 도구마다 실행하고 스크립트 안에서 거른다.
- `command`+`args`는 셸을 거치지 않는 exec 형식이다. `${CLAUDE_PROJECT_DIR}`가 Git Bash·PowerShell 어느 훅 셸에서도 같은 볼트 경로로 바뀐다.
- `-I`는 볼트 폴더에 놓인 다른 `.py`나 `PYTHON*` 환경 변수를 불러오지 않게 하고, `-X utf8`은 입출력을 UTF-8로 고정한다.
- `timeout`은 10초다.
- `"onFailure": "block"`은 가드가 시작되지 못하거나(경로·python 오류), 0·2가 아닌 코드로 끝나거나, 시간을 넘기면 도구 호출을 막는다(Claude Code v2.1.295 이상).

**끄기(되돌리기):** `.claude/settings.local.json`에서 `deny-guard.py`를 부르는 그룹을 지운다. 스크립트 파일은 남아 있어도 실행되지 않는다.

## 지연 (엔진 개발 기기 실측)

Windows 11, Python 3.12.10, 보안 제품의 실시간 검사가 켜진 엔진 개발 기기에서 2026-10-27에 입력마다 41회 잰 값이다.

| 입력 | 중앙값 | 90백분위 |
|---|---|---|
| `python -X utf8 -I -c pass`(Python 시작만) | 0.37초 | 0.43초 |
| Read, 구역 밖(결정 없음) | 0.38초 | 0.45초 |
| Read, 구역 안(deny) | 0.35초 | 0.44초 |
| Bash, 구역 이름 없음(결정 없음) | 0.41초 | 0.47초 |
| Bash, 구역 경로(deny) | 0.40초 | 0.46초 |
| PowerShell, 구역 이름(ask) | 0.41초 | 0.46초 |

훅 한 번은 거의 전부 Python 프로세스 시작 시간이다. 판정 자체는 같은 기기에서 0.3ms 미만이었다. 같은 날 다른 작업이 겹친 측정에서는
90백분위가 0.9초까지 늘었다. 대상 도구를 부를 때마다 이만큼 늦어지고, 병렬 도구 호출은 그 수만큼 프로세스를 띄운다. 이 볼트의 기기에서
다시 재서 판단한다.

## 한계 (가드이지 샌드박스가 아님)

- 파일 시스템 권한을 바꾸지 않는다. 우회할 수 없는 경계가 필요하면 OS 권한(ACL)을 쓴다.
- 실행된 프로그램이 스스로 여는 파일(스크립트 안의 경로, `python -c` 문자열 안의 경로 — 인라인 코드는 ask로만 잡는다)과, 구역 이름을
  글자 그대로 쓰지 않고 실행 중에 조립하는 경로(`9*-assets`, 문자열 결합, 앞서 정의한 별칭·함수)는 보지 못한다. `90-{assets,x}` 같은
  중괄호 확장과 주석 속 구역 이름은 ask로만 잡는다.
- 볼트 루트에서 경로 없이 도는 Grep·Glob과 Bash의 `rg`·`find .`는 구역을 지나가도 막지 않는다(막으면 모든 검색이 막힌다). Read deny
  규칙이 파일 읽기를 따로 막는다.
- 확인한 뒤 실행 전에 정션·링크를 바꿔치면 보지 못한다. 구역 이름이 들어 있지 않은 Bash 명령은 정션을 거쳐 구역에 닿아도 보지 않는다
  (경로 도구는 `realpath`로 잡는다). UNC 경로(`\\server\share`)는 네트워크 조회를 피하려고 실제 경로로 풀지 않고, 8.3 짧은 이름은 실제로
  있는 경로만 풀린다.
- Bash에서 구역 경로와 같은 글자는 경로가 아닌 인자(검색어 `grep -rn 90-assets notes`, 화면에 찍는 `echo 90-assets`)여도 deny될 수
  있다. 노트 검색은 Grep 도구로, 노트 편집은 Edit·Write 도구로 한다. `cd` 뒤의 상대 경로는 ask로 넘기고, `git -C`·`make -C` 같은 작업
  폴더 옵션은 따라가지 않는다.
- PowerShell은 아직 구문 분석하지 않는다. 구역 이름이 보이면 `Move-Item`으로 하는 격리 이동도 ask가 된다.
- 자기 보호는 ask일 뿐이다. `00-meta/vault-config.json`·`.claude/`·이 스크립트를 쓸 수 있는 누구나(편집이 허용된 다른 에이전트,
  Codex 세션, 동기화 상대, 다른 수집 경로) 가드를 끄거나 구역을 바꿀 수 있고, Claude Code 밖에서 바꾼 것은 가드가 보지 못한다.
  `00-meta/`처럼 제어 파일을 담은 상위 폴더를 통째로 지우거나 옮기는 명령도 따로 묻지 않는다(셸 권한 확인에 맡긴다).
- Claude Code v2.1.295 미만은 `onFailure`를 모른다. 그런 호스트에서는 가드가 충돌하거나, 시간을 넘기거나, python이 없으면 도구 호출이
  그대로 진행된다(fail-open). v2.1.295 이상에서는 반대로 python 설치가 깨지면 대상 도구 호출이 모두 막히므로 위 「끄기」로 되돌린다.
- Claude Code 전용이다. Codex는 훅 모델이 달라 이 템플릿을 쓰지 않는다.
- `ask`가 잦으면 내용을 보지 않고 승인하는 습관이 생긴다. 구역마다 마지막 단계 이름으로 미리 거르므로, 구역 이름을 짧은 일반 단어로
  정하면 ask가 늘어난다.

## 출처

- Claude Code hooks 문서(`code.claude.com/docs/en/hooks`): PreToolUse 입력(`tool_name`·`tool_input`·`cwd`), `hookSpecificOutput`의
  `permissionDecision`(allow·deny·ask·defer)과 `permissionDecisionReason`, 여러 훅의 우선순위, exec 형식(`command`+`args`)의 경로 치환,
  `if` 필터가 best-effort라는 설명, `onFailure: "block"`(v2.1.295 이상)과 실패로 치는 경우.
- Python 표준 라이브러리 `shlex.shlex(posix=True, punctuation_chars=...)`와 `whitespace_split`: 셸 연산자를 따로 떼는 단어 분리. 이 가드는
  줄바꿈도 연산자로 다뤄 다음 줄을 새 명령으로 본다.
- 구역 비교 규칙: 플러그인 `skills/agentic-vault/scripts/vault_paths.py`의 `zone_matches`를 표시(`SHARED ZONE RULE`) 사이에 그대로 복사했고,
  `tests/test_rule_sources.py`가 두 사본이 같은지 확인한다.
