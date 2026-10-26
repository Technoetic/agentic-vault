# deny zone 가드 (선택, Claude Code 전용)

이 폴더의 `deny-guard.py`는 Claude Code의 PreToolUse 훅으로 도는 선택형 가드다. 도구 호출이 볼트의 deny zone
(`00-meta/vault-config.json`의 `deny_zones`)을 가리키면 `deny`로 막거나 `ask`로 사용자 확인을 받는다. PowerShell 명령은 같은
폴더의 `ps-parse.ps1`로 구문 분석한다. 플러그인은 이 훅을 켜지 않는다(`hooks/hooks.json`에 없다). 사용자가 아래 「켜는 법」대로
직접 병합해야만 동작한다.

가드이지 샌드박스가 아니다. 파일 시스템 권한을 바꾸지 않고, 「한계」에 적은 경로는 보지 못한다. 기존 Read deny 규칙
(`assets/templates/settings-permissions.json`을 병합한 것)을 대신하지 않고 그 위에 더하는 보조 장치다.

## 판정표

| 도구 | 보는 입력 | deny | ask | 결정 없음 |
|---|---|---|---|---|
| Read·Edit·Write·NotebookEdit | 키가 `path`로 끝나는 모든 문자열(`file_path`·`notebook_path`) | 경로가 구역 안(정션·링크를 푼 실제 경로 포함) | 경로 필드가 없는 입력. Edit·Write·NotebookEdit으로 가드 제어 파일을 바꿈 | 그 밖 |
| Glob | `path`, `pattern` | `path`가 구역 안. 패턴의 고정 앞부분이 구역 안. 와일드카드 뒤의 한 단계 구역 이름(`**/90-assets/**`) | 와일드카드 옆의 구역 이름(`**/_processed/*`) | 구역 이름 없이 볼트 전체를 도는 검색(`**/*.md`) |
| Grep | `path`, `glob`(`pattern`은 찾을 글자라 보지 않는다) | `path`나 `glob`이 구역 안 | `glob` 와일드카드 옆의 구역 이름 | `!90-assets/**` 같은 제외 glob, 경로 없는 검색 |
| Bash·Monitor | `command`(Monitor는 이 명령을 Bash 도구의 셸에서 실행한다) | 글자 그대로의 단어(리디렉션 대상 포함)가 구역 안. 단어의 `:` 뒤 글자(git `REV:경로`·`:경로`·`:N:경로`, `:/경로` 같은 pathspec, `host:경로`)도 작업 폴더·볼트 루트·작업 폴더 위의 저장소 루트에서 경로로 푼다. Windows 8.3 짧은 이름(`90-ASS~1`)은 실제로 있으면 긴 이름으로 풀어 본다 | 구역 이름이나 제어 파일 이름이 보이는 명령 가운데 실행 중에 경로·명령을 만듦(`$VAR`·`$(...)`·백틱·`eval`·`source`·`sh -c`·`python -c` 같은 인라인 코드), `$(...)`·백틱 안이나 here-document(`<<`) 뒤에 보이는 구역 이름(예외 단어 안이어도), `cd` 뒤의 상대 경로, 와일드카드 옆의 구역 이름, 기존 파일을 덮어쓸 수 있는 이동(`-n` 없는 `mv`, `git mv -f`)의 구역 목적지, 따옴표가 맞지 않음, 주석처럼 경로로 풀 수 없는 구역 이름, 가드 제어 파일을 가리킴. 그리고 셸 작업 폴더가 구역 안 | 구역 이름도 제어 파일 이름도(그것을 대신할 수 있는 짧은 이름도) 없는 명령, `command` 없이 `ws`만 받은 Monitor(WebSocket 감시는 명령을 실행하지 않는다) |
| PowerShell | `command`(구역 이름이나 제어 파일 이름이 보일 때만 `ps-parse.ps1`로 구문 분석) | 글자 그대로의 문자열(인자, 리디렉션 대상, 파이프 입력, 변수에 넣는 값, 확장 문자열의 `$` 앞 고정 폴더)이 구역 안. Bash 예외가 없어 `Move-Item`의 목적지와 `-Exclude` 값도 포함 | 구문 오류, 파서 실패·5초 초과, 실행 중에 정해지는 부분(`Invoke-Expression`·`iex`, `Start-Process`, `Invoke-Command`, `&`·`.`로 부르는 스크립트 블록, dot-source, `-EncodedCommand`, FileSystem 밖 공급자 경로, 변수·연산자·메서드 호출, 확장 문자열, 다른 셸·인터프리터), 같은 명령 안 `Set-Location`·`cd`와 상대 경로, 문자열로 보이지 않는 구역 이름(주석 등), 가드 제어 파일. 그리고 작업 폴더가 구역 안 | 구역 이름도 제어 파일 이름도 없는 명령(파서를 띄우지 않는다), 구역 이름을 글자로만 품은 문자열(`Write-Output 'see 90-assets'`) |

- **Bash 예외(구역을 열지 않는 단어):** 기존 파일을 덮어쓰지 않는 이동(`mv -n`·`mv --no-clobber`, `-f` 없는 `git mv`)의 목적지와
  `mkdir` 폴더(볼트 규칙의 격리 이동 — `/vault-process-inbox`·`/vault-lint`가 처리한 원본을 셸 move로 구역에 옮긴다), 표준 출력을 파일로
  보내는 `echo`·`printf`의 글자(`echo '90-assets/' >> .gitignore`), `git commit`·`git tag`의 `-m` 메시지, 제외 옵션 `--exclude`·`--exclude-dir`·
  `--ignore-dir`의 값(`--exclude-dir=90-assets`, `--exclude-dir 90-assets`)과 `--ignore=값`, `ls`·`ag`의 `--ignore 값`, 그리고 `!`로 시작하는
  단어(`rg -g '!90-assets/**'`). 파이프나 `$(...)`로 넘어가는
  `echo` 출력, 파일이 아닌 스트림(`/dev/stdout`·`/dev/fd/1`·`/proc/self/fd/1`·`>&2`)이나 실행 중에 정해지는 대상(`> "$f"`)으로 보내는
  출력은 경로가 될 수 있어 예외가 아니다(`/dev/null`은 버리는 곳이라 예외다). 표준 출력 리디렉션이 여럿이면 셸처럼 마지막 것을 본다.
  다른 이름의 제외·무시 옵션은 예외가 아니다: 값이 없는 옵션(`--exclude-vcs`·`--ignore-backups`·`--delete-excluded`·`--exclude-standard`,
  `--no-ignore`를 끄는 ripgrep의 `--ignore`) 뒤의 단어는 피연산자이고, 파일을 읽는 옵션(`--exclude-from`·`--ignore-file`·`--ignore-path`)의
  값은 그 파일을 연다. `--` 뒤의 단어는 옵션으로 보지 않는다. `mv`의 `-t`가 다른 짧은 옵션과 붙은 형태(`-vt`)나 `git -C`처럼 하위 명령
  앞에 옵션 값이 오는 형태는 예외로 보지 않고 그대로 검사한다.
- **덮어쓸 수 있는 이동은 ask:** `mv`는 `-n`(`--no-clobber`, `--update=none`)이 없거나 그 뒤에 `-f`·`-i`가 오면(GNU·BSD `mv`는 셋 가운데
  마지막 것을 따른다) 목적지에 같은 이름의 파일이 있을 때 그 파일을 바꿔 쓴다. 그래서 목적지가 구역 안이면 `deny`가 아니라 `ask`이고,
  이유 문구가 `mv -n`이나 `git mv`를 안내한다. `git mv -f`·`git mv --force`와 `mv --exchange`도 같다.
- **리디렉션 번호:** `2>&1`·`2>/dev/null`·`1>f`처럼 숫자를 `<`·`>`에 붙여 쓰면 셸은 그 숫자를 스트림 번호로 읽으므로 가드도 이동의
  피연산자로 보지 않는다(`mv -n a.md 10-inbox/_processed/2026-10/ 2>&1`의 목적지는 그대로 예외다). 띄어 쓴 `2 > f`의 `2`는 셸에서도
  피연산자다. 명령에 U+0001 문자가 있으면 가드가 번호를 표시하는 데 쓰는 문자이므로 예외를 적용하지 않는다. 구역 이름이 보이면
  `deny`, 제어 파일 이름만 보이면 `ask`다(이 문자로 `deny`를 `ask`로 낮출 수 없다).
- **코드는 예외가 아니다:** 셸이 명령으로 실행할 수 있는 글자, 곧 `$(...)`·백틱 안(따옴표 안이든 밖이든)과 here-document(`<<`) 뒤에 구역
  이름이나 제어 파일 이름이 보이면 예외 단어 안이어도 `ask`다(`echo "$(cat 90-assets/x)" > out.txt`,
  `git commit -m "$(cat 10-inbox/_processed/a.md)"`, `mkdir "$(cat 90-assets/x)"`). 그 바깥의 고정 글자는 예외 그대로다
  (`mkdir -p "10-inbox/_processed/$(date +%Y-%m)"`). 작은따옴표·`$'...'` 안의 `$(`·백틱과 `\$(`는 글자다. 글자로 보는 형식은 Claude Code의
  커밋 형식 하나다: `$(cat <<'EOF'` 다음 줄부터 `EOF` 줄까지를, 첫 줄에 다른 글자가 없고 `EOF` 줄 바로 뒤에 `)`가 오며 본문에 `$(`·백틱이
  없을 때만 글자로 본다. `$(`의 끝은 괄호 짝으로 찾되, 안에 `case`·`${`·`#`·`<<`가 있으면 자기 `)`를 품을 수 있어 명령 끝까지를 코드로 본다.
  셸 단어 분리(`shlex`)는 이런 형태를 모르므로(`"$(...)"`를 한 단어로 두고, 백틱 안을 여러 단어로 나누고, here-document 본문의 따옴표를
  따옴표로 읽는다) 이 검사는 단어와 따로 한다.
- **가드 제어 파일(자기 보호):** `00-meta/vault-config.json`, `00-meta/scripts/deny-guard.py`, `00-meta/scripts/ps-parse.ps1`,
  `.claude/settings*.json`과 `.claude` 폴더(볼트 밖 사용자 설정 `~/.claude/settings.json` 포함). 이것을 바꾸면 가드를 끌 수 있으므로
  `ask`로 확인한다. Read로 읽는 것은 그대로 둔다. 셸 명령의 글자에서는 `.claude`를 폴더 자체(`rm -rf .claude`), 바로 안의 와일드카드
  이름(`.claude/*.json`), `.`이나 `..`로 끝나는 경로(`.claude/rules/..`)일 때만 제어 파일 이름으로 본다. 그래서 플러그인 설치
  경로(`~/.claude/plugins/...`)나 `.claude/rules/` 아래 파일을 쓰는 명령은 그 이름 때문에 묻지 않는다.
- **구역 비교:** 플러그인 `vault_paths.zone_matches`를 그대로 복사해 쓴다. 한 단계 구역(`90-assets`)은 어느 깊이의 같은 이름 폴더든,
  여러 단계 구역(`10-inbox/_processed`)은 볼트 루트에서 시작할 때만 맞고, 대소문자는 무시한다. 경로는 글자 그대로의 경로와
  `realpath`로 푼 경로를 둘 다 볼트 루트(주어진 경로와 `realpath`로 푼 경로)에 대고 `normcase`로 비교한다. 그래서 구역 밖 폴더가
  정션으로 구역을 가리켜도, 구역 자체가 볼트 밖을 가리키는 정션이어도 잡힌다. Windows에서는 MSYS 경로(`/d/vault/...`)를
  `D:/vault/...`로 바꾸고(`CLAUDE_PROJECT_DIR`가 MSYS 형식이어도 같은 `vault-config.json`을 읽는다), `\\?\` 접두사와 끝의 점·공백,
  `:` 뒤의 스트림 이름을 Windows가 여는 방식대로 접는다. macOS 볼륨은 기본으로 대소문자를 가리지 않는데 `normcase`는 그곳에서
  대소문자를 그대로 두므로, macOS에서는 비교 전에 `casefold`로도 접는다(`/Users/me/VAULT/90-assets`도 볼트 안이다).
- **8.3 짧은 이름(Windows):** Windows는 `90-assets`에 `90-ASS~1` 같은 짧은 이름을 붙일 수 있다. 공백과 맨 앞의 점, 확장자를 가르는
  것 말고의 점을 빼고 대문자로 바꾼 이름의 앞 여섯 글자에 `~숫자`를 붙이고, 그런 이름이 이미 넷 있으면 앞 두 글자, 16진수 네 자리,
  `~숫자`를 쓴다. 셸 명령의 한 단계 이름이 여섯 글자 이하와 `~숫자`(뒤에 확장자가 올 수 있다)로 되어 있고 그 앞부분이 구역이나 제어
  파일 이름에서 이렇게 나올 수 있으면(`90-ASS~1`, `_PROCE~1`, `OBSIDI~1`, `CLAUDE~1`, `SETTIN~1.JSO`) 그 이름이 보이는 것처럼 다룬다.
  경로는 `realpath`가 실제로 있는 짧은 이름만 긴 이름으로 풀므로 그것으로 판정한다. ASCII 밖 글자가 든 이름은 코드 페이지에 따라 짧은
  이름이 달라지므로 그 꼴의 이름을 모두 대상으로 본다. `HEAD~1` 같은 git 리비전은 앞부분이 맞지 않아 걸리지 않는다. Windows가 아닌
  곳에서는 짧은 이름을 보지 않는다.

## PowerShell 구문 분석 (`ps-parse.ps1`)

- **언제 띄우나:** Bash와 같은 미리 거르기(구역의 마지막 단계 이름이나 제어 파일 이름, 그것을 대신할 수 있는 8.3 짧은 이름이 명령
  글자에 보이는가)가 걸릴 때만 띄운다.
  PowerShell의 이스케이프 문자인 백틱은 미리 거를 때 지운다(``90-ass`ets``도 걸린다). 걸리지 않는 PowerShell 호출은 Python 시작
  시간만 든다.
- **어떻게 띄우나:** 가드 파일과 같은 폴더의 `ps-parse.ps1`을 Windows 폴더의 Windows PowerShell 5.1
  (`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`, PATH나 작업 폴더에서는 찾지 않는다)로
  `-NoProfile -NonInteractive -ExecutionPolicy Bypass -File`로 실행하고, 명령 글자는 표준 입력에 UTF-8로 넘긴다. 콘솔 창은 띄우지
  않고 제한 시간은 5초(`PARSE_TIMEOUT`)다. `[System.Management.Automation.Language.Parser]::ParseInput`으로 구문 트리만 만들어
  `FindAll`로 훑을 뿐 명령을 실행하거나 경로를 열지 않는다.
- **출력:** JSON 한 줄과 종료 코드 0. 인쇄 가능한 ASCII 밖의 글자는 모두 `\uXXXX`로 내보내 콘솔 코드 페이지와 무관하다. 종료 코드 0과
  이 JSON이 아니면 가드는 `ask`로 처리한다.

  | 키 | 뜻 |
  |---|---|
  | `errors` | 구문 오류 수 |
  | `commands` | 명령 이름(소스 순서). `& { ... }`·`& $x`처럼 이름이 상수가 아니면 `null` |
  | `strings` | 모든 상수 문자열 값(맨 단어, 따옴표 문자열, here-string, 명령 이름 포함). 백틱 이스케이프와 따옴표는 풀린 값이다 |
  | `expandable` | 확장 문자열(`"...$x..."`) 수 |
  | `expandable_strings` | 그 글자(변수와 `$(...)`는 쓴 그대로) |
  | `flags` | 실행 중에 정해지는 구성의 이름(정렬): `invoke_expression`(`Invoke-Expression`·`iex`), `start_process`(`Start-Process`·`start`·`saps`), `invoke_command`(`Invoke-Command`·`icm`), `script_block_call`(`&`·`.`로 스크립트 블록이나 상수가 아닌 값을 실행), `dot_source`(`.`), `encoded_command`(`-EncodedCommand`·`-ec`와 `-enc`·`-e` 같은 앞부분), `provider_path`(`Registry::`·`Env:`·`HKLM:`처럼 FileSystem 밖 공급자 경로, 한 글자보다 긴 드라이브 이름, `${C:\x}`·`$env:X` 같은 드라이브 지정 변수), `expression`(그 밖의 계산: `$true`·`$false`·`$null` 아닌 변수, 연산자, 메서드 호출과 속성, 하위 식, 스크립트 블록, 대입, 반복문, 정의) |
- **판정:** `strings`와 `expandable_strings`의 값을 Bash 단어처럼 판정한다. `FileSystem::` 접두사는 떼고, FileSystem 밖 공급자 경로는
  파일 경로로 풀지 않고 `ask`, 확장 문자열은 `$` 앞의 고정 폴더가 구역이면 `deny`, 와일드카드는 Glob처럼 본다. `:` 뒤의 글자도 Bash처럼
  경로로 보지만(`git show :90-assets/x`는 `deny`), `$` 뒤에 오는 `:`는 `$env:X`처럼 변수의 일부일 수 있어 보지 않는다.
  `HEAD:90-assets/x`처럼 두 글자 이상 뒤에 `:`가 오는 문자열은 공급자 경로 꼴이라 `ask`다. 같은 명령에
  `Set-Location`(`cd`·`sl`·`chdir`·`Push-Location`·`Pop-Location`과 별칭)이 있으면 실행 순서를 알 수 없어 상대 경로는 `ask`다
  (드라이브 문자 없이 `\`로 시작하는 경로와 `~`도 현재 드라이브·공급자를 따르므로 같다).
  `deny`가 하나라도 있으면 `deny`이고, 없으면 구문 오류·`flags`·확장 문자열·다른 셸이나 인터프리터 이름(`cmd`·`powershell`·`pwsh`·
  `bash`·`python` 등)·문자열로 보이지 않는 구역 이름 가운데 하나라도 있으면 `ask`다.
- **deny 이유:** PowerShell에는 Bash 예외가 없다고 밝히고, 구역으로 옮기는 격리 이동은 Bash 도구의 `mv -n`·`git mv`로, 노트 검색은
  Grep 도구로 하라고 안내한다.

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

새 볼트는 Claude Code의 `/vault-init`이 Aside 단계 뒤에 따로 물어(4-2단계) 아래 1–3을 대신 한다. 답이 없으면 설치하지 않는다.
기존 볼트는 아래처럼 직접 병합한다. `/vault-doctor`와 `/vault-upgrade`는 가드를 제안하지 않는다.

1. Claude Code가 보는 PATH의 `python`이 Python 3.10 이상인지 확인한다: `python -c "import sys; print(sys.version)"`. Windows 스토어 별칭만
   있거나 `py` 런처만 있으면 아래 `command`를 python.exe 절대 경로로 바꾸거나 `"command": "py"`로 두고 `args` 맨 앞에 `"-3"`을 넣는다.
2. 이 폴더의 `deny-guard.py`와 `ps-parse.ps1`을 볼트의 `00-meta/scripts/deny-guard.py`, `00-meta/scripts/ps-parse.ps1`로 바이트
   그대로 복사한다(첫 줄의 `agentic-vault:adapter deny-guard engine=`·`agentic-vault:adapter ps-parse engine=` 스탬프를 지우지
   않는다). 가드는 자기와 같은 폴더의 `ps-parse.ps1`을 쓴다. 이 파일이 없으면 구역 이름이 보이는 PowerShell 명령은 모두 `ask`가 된다.
3. `settings-hooks.json`의 `PreToolUse` 그룹을 볼트 `.claude/settings.local.json`의 `hooks.PreToolUse` 배열에 추가한다. 기존 키와 훅은 모두
   남긴다. 기기마다 다른 설정이므로 공유 `settings.json`이 아니라 `settings.local.json`이 기본이다. 폴더 동기화를 쓰는 볼트는
   local 파일도 다른 기기로 퍼진다.
4. Claude Code의 `/hooks`에서 훅이 보이는지 확인하고, 볼트 루트의 Git Bash에서 시험 입력으로 판정을 본다. `"permissionDecision": "deny"`가
   든 JSON 한 줄이 나와야 한다.

   ```bash
   echo '{"tool_name":"Read","tool_input":{"file_path":"90-assets/x.png"}}' | CLAUDE_PROJECT_DIR="$PWD" python -X utf8 -I 00-meta/scripts/deny-guard.py
   echo '{"tool_name":"PowerShell","tool_input":{"command":"Get-Content 90-assets/x.png"}}' | CLAUDE_PROJECT_DIR="$PWD" python -X utf8 -I 00-meta/scripts/deny-guard.py
   ```

   두 번째 줄은 `ps-parse.ps1`까지 거친 `deny`가 나와야 한다.

훅 항목의 뜻:

- `matcher`는 정확한 도구 이름 목록이다(글자·`|`만 있으면 정규식으로 보지 않는다). Monitor는 받은 명령을 Bash 도구의 셸에서 백그라운드로
  실행하고 Bash 권한 규칙을 따르므로(Claude Code 도구 참조) 함께 넣는다. `command` 대신 `ws`를 받는 WebSocket 감시는 명령을 실행하지 않아
  가드가 결정하지 않는다.
- `if` 필터를 쓰지 않는다. 문서상 `if`는 최선 노력(best-effort) 필터라 강제 수단으로 쓰지 말라고 되어 있으므로, 대상 도구마다 실행하고 스크립트 안에서 거른다.
- `command`+`args`는 셸을 거치지 않는 exec 형식이다. `${CLAUDE_PROJECT_DIR}`가 Git Bash·PowerShell 어느 훅 셸에서도 같은 볼트 경로로 바뀐다.
- `-I`는 볼트 폴더에 놓인 다른 `.py`나 `PYTHON*` 환경 변수를 불러오지 않게 하고, `-X utf8`은 입출력을 UTF-8로 고정한다.
- `timeout`은 10초다. PowerShell 파서의 제한 시간 5초가 그 안에 들어간다.
- `"onFailure": "block"`은 가드가 시작되지 못하거나(경로·python 오류), 0·2가 아닌 코드로 끝나거나, 시간을 넘기면 도구 호출을 막는다(Claude Code v2.1.295 이상).

**끄기(되돌리기):** `.claude/settings.local.json`에서 `deny-guard.py`를 부르는 그룹을 지운다. 스크립트 파일은 남아 있어도 실행되지 않는다.
스크립트를 지우거나 옮기려면 그룹을 먼저 지운다. 훅이 남은 채 `deny-guard.py`가 없으면 python이 종료 코드 2로 끝나고, Claude Code는
종료 코드 2를 차단으로 읽으므로 `onFailure`와 호스트 버전에 상관없이 대상 도구 호출이 모두 막힌다.

## 상태 보고 (`vault_adapters.py`)

플러그인의 읽기 전용 어댑터 진단이 가드의 설치 상태를 `adapters.deny_guard`로 보고한다. `/vault-doctor`가 이 보고를 보여 주고,
`/vault-upgrade`(Claude Code)가 그 `actions`를 처리한다. 진단은 가드를 실행하거나 파일·설정을 바꾸지 않는다.

```text
python "<플러그인 루트>/skills/agentic-vault/scripts/vault_adapters.py" --vault <볼트> --format json [--offer-deny-guard]
```

| 키 | 뜻 |
|---|---|
| `state` | `absent`(스크립트도 가드 훅도 없음), `installed`(두 스크립트와 문제 없는 훅), `incomplete`(일부만 있음: 스크립트만, 훅만, `ps-parse.ps1` 없이), `needs_review`(훅 문제, 스크립트는 있는데 설정 파일을 읽지 못함, 읽을 수 없거나 링크·정션 뒤에 있는 스크립트) |
| `guard`·`parser` | `00-meta/scripts/deny-guard.py`·`00-meta/scripts/ps-parse.ps1`의 `path`, `installed_stamp`, `bundled_stamp`, `state`(`missing`·`current`·`outdated`·`newer`·`modified`·`unstamped`·`unreadable`·`unsafe_path`). Aside 도우미와 같은 규칙으로 첫 줄 `engine=` 스탬프를 비교하고, 스탬프가 같으면 줄바꿈과 BOM을 접은 내용이 번들과 같은지로 로컬 수정을 가린다 |
| `hook` | `.claude/settings.json`·`.claude/settings.local.json`에서 `command`나 `args`로 `deny-guard.py`를 부르는 PreToolUse 핸들러: `state`(`absent`·`configured`·`needs_review`·`unreadable`), `configured_in`, `unreadable`(읽지 못했거나 링크·정션을 거친 설정 파일), `problems` |
| `actions` | `offer_guard_update`(설치본 스탬프가 번들보다 낮음), `review_guard`(로컬 수정·스탬프 없음·더 높음·읽기 실패·링크, 또는 일부만 설치), `review_settings`(훅 문제나 읽지 못한 설정, 가드가 `absent`가 아닐 때만), `offer_deny_guard`(아래) |

- **`problems`가 잡는 것:** 가드를 소리 없이 약하게 만드는 설정 네 가지다. `"onFailure": "block"`이 없음(가드가 실패하면 호출이
  그대로 진행된다), `if` 필터가 있음(best-effort라 가드를 건너뛸 수 있다), 중괄호 없는 `$CLAUDE_PROJECT_DIR`(훅 셸에 따라 달라지고
  PowerShell에서는 빈 값이다), 정확한 도구 이름 목록인 `matcher`가 대상 도구 아홉 개 가운데 하나라도 빠뜨림(`*`·빈 값·생략은
  모든 도구에 맞는다. 정규식 matcher는 판정하지 않는다). 실행기(`python`·`py -3`·절대 경로)와 `timeout`은 보지 않는다.
- **제안은 `/vault-init`만:** `offer_deny_guard`는 `--offer-deny-guard`를 줄 때만, 그리고 `state`가 `absent`이고
  `.claude/settings.local.json`을 읽을 수 있을(또는 아직 없을) 때만 나온다. 이 플래그는 `/vault-init` 4-2만 쓰고, 다른 보고 내용은
  바꾸지 않는다. 볼트로 확인된 보고(`status: ok`)에는 플래그 없이도 `adapters.deny_guard`가 들어 있고(가드가 없으면
  `state: absent`), `adapters.aside`와 종료 코드는 그대로다.
- **한계:** 파일과 설정만 읽는다. Claude Code가 훅을 실제로 불러왔는지, 이 기기의 `python`이 3.10 이상인지, 가드가 판정을 제대로
  내는지는 보지 않는다(위 4의 시험 입력으로 확인한다). `.claude/` 밖의 사용자 설정(`~/.claude/settings.json`)과 Codex 설정도 보지 않는다.
- **출처:** Aside 어댑터 보고의 스탬프 상태와 훅 명령 검사를 두 스크립트와 가드 훅으로 넓혔다. 훅 검사 항목은 Claude Code hooks
  문서의 `onFailure: "block"`(v2.1.295 이상), `if` 필터가 best-effort라는 설명, PowerShell 훅에서 중괄호 없는 `$CLAUDE_PROJECT_DIR`가
  빈 값이 된다는 주의와 matcher 규칙(글자·숫자·`_`·`-`·공백·`,`·`|`만 있으면 정확한 이름 목록)에서 왔다.

## 지연 (엔진 개발 기기 실측)

Windows 11(10.0.26300), Python 3.12.10, Windows PowerShell 5.1.26100, 보안 제품의 실시간 검사가 켜진 엔진 개발 기기에서
2026-10-27에 쟀다. 임시 볼트의 `00-meta/scripts/`에 설치한 사본을 훅과 같은 명령(`python -X utf8 -I .../deny-guard.py`, 입력은 표준
입력)으로 부르고, 입력마다 41회씩 번갈아 돌렸다. 이때 다른 작업(테스트 실행)이 함께 돌아 CPU 사용률이 30–60%였다.

| 입력 | 중앙값 | 90백분위 |
|---|---|---|
| `python -X utf8 -I -c pass`(Python 시작만) | 0.51초 | 1.00초 |
| Read, 구역 밖(결정 없음) | 0.52초 | 1.08초 |
| Read, 구역 안(deny) | 0.52초 | 1.09초 |
| Bash, 구역 이름 없음(결정 없음) | 0.51초 | 1.05초 |
| Bash, 구역 경로(deny) | 0.52초 | 1.12초 |
| PowerShell, 구역 이름 없음(결정 없음, 파서를 띄우지 않음) | 0.51초 | 1.18초 |
| PowerShell, 구역 경로(deny, 파서를 띄움) | 1.02초 | 1.70초 |
| PowerShell, 실행 중에 정해지는 구성(ask, 파서를 띄움) | 1.05초 | 1.65초 |
| `ps-parse.ps1` 자식 프로세스만 | 0.45초 | 0.51초 |

훅 한 번은 거의 전부 프로세스 시작 시간이다. 판정 자체(설정 파일 읽기 포함)는 같은 때 1ms 미만(0.25–0.4ms)이었다. 같은 날 검토 지적을
고친 뒤 프로세스 안에서 판정만 입력마다 400회씩 다시 재면 중앙값 0.2–0.65ms였고, 가장 느린 것은 `:` 뒤 경로를 저장소 루트에서도 푸는
`git show HEAD:...`였다. 구역 이름이나
제어 파일 이름이 보이는 PowerShell 명령만 Windows PowerShell을 하나 더 띄워 약 0.5초가 더 든다. 자식 프로세스만 잰 값은 부하 속에서도
0.42–0.56초로 고르게 나왔다. 약 15분 뒤 같은 조건의 두 번째 측정도 중앙값 차이가 0.15초 이내였다. 다른 작업이 없던 앞선 측정에서는
Python 시작만이 중앙값 0.37초, 90백분위 0.43초였으므로 부하에 따라 두 배까지 늘어난다. 대상 도구를 부를 때마다 이만큼 늦어지고,
병렬 도구 호출은 그 수만큼 프로세스를 띄운다. 이 볼트의 기기에서 다시 재서 판단한다.

## 한계 (가드이지 샌드박스가 아님)

- 파일 시스템 권한을 바꾸지 않는다. 우회할 수 없는 경계가 필요하면 OS 권한(ACL)을 쓴다.
- 실행된 프로그램이 스스로 여는 파일(스크립트 안의 경로, `python -c` 문자열 안의 경로 — 인라인 코드는 ask로만 잡는다)과, 구역 이름을
  글자 그대로 쓰지 않고 실행 중에 조립하는 경로는 보지 못한다. 와일드카드(`9*-assets`·`9?-assets`·`90-asset[s]`), Bash의 `$'...'` 이스케이프
  (`$'90-ass\x65ts'`), 문자열 결합, 앞서 정의한 변수·별칭·함수가 그렇다. `echo`·`printf`로 파일에 쓴 구역 경로나 커밋 메시지를 같은 명령의
  뒤 단계나 다음 명령이 읽어 경로로 쓰는 경우(`echo 90-assets/x > list; xargs cat < list`)도 뒤 단계가 구역 이름을 쓰지 않으므로 보지
  못한다. `90-{assets,x}` 같은 중괄호 확장과 주석 속 구역 이름은 ask로만 잡는다.
- 볼트 루트에서 경로 없이 도는 Grep·Glob과 Bash의 `rg`·`find .`는 구역을 지나가도 막지 않는다(막으면 모든 검색이 막힌다). Read deny
  규칙이 파일 읽기를 따로 막는다.
- 확인한 뒤 실행 전에 정션·링크를 바꿔치면 보지 못한다. 구역 이름(이나 그것을 대신할 수 있는 8.3 짧은 이름)이 들어 있지 않은 Bash
  명령은 정션을 거쳐 구역에 닿아도 보지 않는다(경로 도구는 `realpath`로 잡는다). UNC 경로(`\\server\share`)는 네트워크 조회를 피하려고
  실제 경로로 풀지 않고, 8.3 짧은 이름은 실제로 있는 경로만 풀린다. 셸 명령에서는 앞부분이 구역이나 제어 파일 이름과 맞는 짧은 이름만
  보므로, 짧은 이름을 실행 중에 조립하는 경로는 보지 못한다. macOS에서는 경로를 `casefold`로 비교하므로, 대소문자를 가리도록 포맷한
  볼륨에서는 대소문자만 다른 볼트 밖 폴더를 볼트로 볼 수 있다(더 막는 쪽이다). Linux에서는 대소문자를 가린다.
- Bash에서 구역 경로와 같은 글자는 경로가 아닌 인자(검색어 `grep -rn 90-assets notes`, 화면에 찍는 `echo 90-assets`)여도 deny될 수
  있다. 단어의 `:` 뒤 글자도 경로로 보므로 원격 경로(`scp host:90-assets/x .`)나 경로가 아닌 글자(`echo note:90-assets`)도 `:` 뒤가
  볼트의 구역 경로와 같으면 deny된다. here-document 본문도 셸에서는 글자이지만 가드는 본문 줄을 명령처럼 나눠 보므로 그 안의 구역
  경로가 deny될 수 있다(`cat > note.md <<'EOF'`). 노트 검색은 Grep 도구로, 노트 편집은 Edit·Write 도구로 한다. `cd` 뒤의 상대 경로는 ask로 넘기고,
  `git -C`·`make -C` 같은 작업 폴더 옵션은 따라가지 않는다. git `REV:경로`의 경로는 작업 폴더, 볼트 루트, 작업 폴더에서 위로 처음 만나는
  `.git`이 있는 폴더에서만 풀어 보므로, `git -C`나 `GIT_DIR`·`GIT_WORK_TREE`로 다른 저장소를 가리키면 그 저장소의 루트는 따라가지 않는다.
- 엔진 작업 흐름과 부딪치는 곳이 있다. `/vault-process-inbox`의 격리 이동과 `/vault-lint`의 노화 문서 아카이브 이동에서 Windows에 쓰는
  `Move-Item`은 막히므로 Bash 도구의 `mv -n`을 쓴다(`-n` 없는 `mv`는 구역 안 파일을 덮어쓸 수 있어 `ask`다). 구역으로 옮긴 파일을
  이름으로 스테이징하는 명령(`/vault-session-end` 7단계의 `git add <변경파일>`에 든 `10-inbox/_processed/2026-10/a.md`, `/vault-init`
  6단계에서 구역 폴더의 `.gitkeep`을 스테이징하는 명령)은 `deny`다. `git mv`로 옮기면 이동이 함께 스테이징되고, 이미 옮긴 파일은 구역 밖 상위 폴더로 스테이징한다(`git add 10-inbox`). `git add`의 `deny` 이유 문구도 이 두 방법을
  알려 준다. 상위 폴더를 스테이징하면 그 폴더의 다른 변경도 함께 올라가므로 커밋 전에 `git status`로 확인한다.
- PowerShell은 미리 거르기에 걸린 명령만 구문 분석한다. `Get-Content (Join-Path 90 -assets)`처럼 구역 이름을 글자로 쓰지 않는 명령은
  파서를 띄우지 않고 통과한다(실측). 문자열 결합(`'90-ass' + 'ets'`), `-f` 서식, 와일드카드(`9?-assets`), `-EncodedCommand`의 Base64,
  앞서 정의한 별칭·함수·`New-PSDrive` 드라이브도 마찬가지다.
- 구문 분석은 Windows PowerShell 5.1 파서로 한다. PowerShell 7 전용 문법(`&&`·`||`, 삼항 연산자, `??`)은 구문 오류라 `ask`가 된다
  (글자 그대로의 구역 경로가 트리에 남으면 `deny`). Windows가 아니면 파서를 띄우지 않으므로 구역 이름이 보이는 PowerShell 명령은
  모두 `ask`다.
- PowerShell에는 Bash 예외가 없다. `Move-Item`으로 구역에 넣는 격리 이동, `-Exclude 90-assets` 같은 제외 값, 구역 경로와 같은 검색어
  (`Select-String -Pattern 90-assets`)도 `deny`된다. 격리 이동은 Bash 도구의 `mv -n`·`git mv`로 한다. 반대로 구역 이름을 글자로만 품고
  경로로 풀리지 않는 문자열(`Write-Output 'see 90-assets'`, 커밋 메시지)은 결정하지 않으므로, 다른 프로그램이 그런 문자열을 명령으로
  실행하는 경우(`schtasks /tr`, WMI의 프로세스 생성)는 보지 못한다.
- 그룹 정책의 실행 정책, AppLocker·WDAC, 제한 언어 모드(Constrained Language)가 `ps-parse.ps1`을 막거나, 보안 제품 검사로 5초를
  넘기면 그 PowerShell 명령은 `ask`가 된다.
- 자기 보호는 ask일 뿐이다. `00-meta/vault-config.json`·`.claude/`·가드 스크립트 두 개를 쓸 수 있는 누구나(편집이 허용된 다른 에이전트,
  Codex 세션, 동기화 상대, 다른 수집 경로) 가드를 끄거나 구역을 바꿀 수 있고, Claude Code 밖에서 바꾼 것은 가드가 보지 못한다. 훅은
  볼트의 두 스크립트를 실행하므로(`ps-parse.ps1`은 `-ExecutionPolicy Bypass`로), 그 파일을 바꾼 사람은 훅이 실행할 코드도 바꾼다.
  `00-meta/`처럼 제어 파일을 담은 상위 폴더를 통째로 지우거나 옮기는 명령도 따로 묻지 않는다(셸 권한 확인에 맡긴다).
- Claude Code v2.1.295 미만은 `onFailure`를 모른다. 그런 호스트에서는 가드가 충돌하거나, 시간을 넘기거나, python이 없으면 도구 호출이
  그대로 진행된다(fail-open). v2.1.295 이상에서는 반대로 python 설치가 깨지면 대상 도구 호출이 모두 막히므로 위 「끄기」로 되돌린다.
  `deny-guard.py`가 없을 때는 버전과 상관없이 막힌다: python이 종료 코드 2로 끝나고 Claude Code는 종료 코드 2를 차단으로 읽는다.
  그래서 스크립트를 지우거나 옮기기 전에 훅 그룹부터 지운다.
- Claude Code 전용이다. Codex는 훅 모델이 달라 이 템플릿을 쓰지 않는다.
- 가드는 `matcher`에 든 아홉 도구의 호출만 본다. MCP 도구처럼 목록 밖의 도구와, 도구 호출 없이 실행되는 명령(다른 훅, 플러그인이 선언해
  저절로 시작하는 monitor)은 보지 않는다.
- `ask`가 잦으면 내용을 보지 않고 승인하는 습관이 생긴다. 구역마다 마지막 단계 이름으로 미리 거르므로, 구역 이름을 짧은 일반 단어로
  정하면 ask가 늘어난다.

## 출처

- Claude Code hooks 문서(`code.claude.com/docs/en/hooks`): PreToolUse 입력(`tool_name`·`tool_input`·`cwd`), `hookSpecificOutput`의
  `permissionDecision`(allow·deny·ask·defer)과 `permissionDecisionReason`, 여러 훅의 우선순위, exec 형식(`command`+`args`)의 경로 치환,
  `if` 필터가 best-effort라는 설명, `onFailure: "block"`(v2.1.295 이상)과 실패로 치는 경우.
- Claude Code 도구 참조(`code.claude.com/docs/en/tools-reference`): 도구 이름은 훅 matcher에 쓰는 글자이고, `Monitor`는 명령을 백그라운드에서
  실행하며 Bash와 같은 권한 규칙을 쓰고(Windows에서는 Git Bash가 있어야 한다), WebSocket 감시는 `command` 대신 `ws`를 받는다. Claude Code
  세션의 Monitor 도구 스키마(`command`·`ws`·`description`·`timeout_ms`, "The script runs in the same shell environment as Bash.")와도 맞춰
  보았다.
- git 문서 `gitrevisions`(`<rev>:<path>`, `:[<n>:]<path>`: 경로는 작업 트리 루트에서 읽고 `./`·`../`로 시작할 때만 작업 폴더에서 읽는다)와
  `gitglossary`의 pathspec magic(`top`의 표시 `/`, `exclude`의 표시 `!`·`^`). ripgrep 14.1.1 `rg --help`의 `--no-ignore` 설명("This flag can
  be disabled with --ignore."): `--ignore`는 값을 받지 않는다.
- Windows 8.3 짧은 이름의 꼴: 위키백과 「8.3 filename」의 VFAT·NT 생성 규칙(앞의 점과 공백을 빼고 대문자로 바꿔 앞 여섯 글자에 `~숫자`,
  같은 꼴이 넷 이상이면 앞 두 글자·해시 16진수 네 자리·`~숫자`)과, 엔진 개발 기기 임시 폴더에서 `GetShortPathNameW`로 본 이름
  (`90-ASS~1`, `_PROCE~1`, `OBSIDI~1`, `CLAUDE~1`, `SETTIN~1.JSO`).
- Python 표준 라이브러리 `shlex.shlex(posix=True, punctuation_chars=...)`와 `whitespace_split`: 셸 연산자를 따로 떼는 단어 분리. 이 가드는
  줄바꿈도 연산자로 다뤄 다음 줄을 새 명령으로 본다. `shlex`는 명령 치환과 here-document를 모르므로 그 부분은 가드가 따로 훑는다.
- POSIX Shell Command Language(XCU 2장) 2.6.3 Command Substitution과 2.7.4 Here-Document: `$( )` 안은 따옴표가 새로 시작하는 명령이고,
  here-document 구분자의 일부라도 따옴표로 감싸면 본문을 확장하지 않는다. Claude Code 커밋 형식(`$(cat <<'EOF' ... EOF)`)을 글자로 보는
  근거다. 작은따옴표 안과 Bash의 `$'...'`(GNU Bash 매뉴얼 ANSI-C Quoting)은 확장하지 않는 글자로 본다.
- 구역 비교 규칙: 플러그인 `skills/agentic-vault/scripts/vault_paths.py`의 `zone_matches`를 표시(`SHARED ZONE RULE`) 사이에 그대로 복사했고,
  `tests/test_rule_sources.py`가 두 사본이 같은지 확인한다.
- Microsoft Learn `System.Management.Automation.Language.Parser.ParseInput`
  (`learn.microsoft.com/dotnet/api/system.management.automation.language.parser.parseinput`): 파일이 아닌 입력을 구문 분석해 토큰,
  `ParseError[]`, `ScriptBlockAst`를 돌려준다("Parse input that does not come from a file."). Windows PowerShell 5.1 참조 어셈블리와
  PowerShell 7.4–7.6에 있다. `ps-parse.ps1`은 이 트리를 `FindAll`로 훑는다.
