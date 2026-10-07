# Security policy

agentic-vault is a single-maintainer, standard-library-only plugin. This page says
which versions get fixes, how to report a vulnerability privately, what is in scope,
and which limits are known and documented rather than bugs.

## Supported versions

| Version | Security fixes |
|---|---|
| 0.19.0 (latest) | Local hardening source; public tag/Release not published. Fixes are verified against this source before local rollout. |
| 0.18.0 (public release) | Existing public release; review the local 0.19.0 changes before upgrading. |
| 0.17.1 and earlier | No. Upgrade to 0.18.0 after reviewing the Jarvis API-auth requirement. |

Releases 0.3.0 through 0.15.0, which ship the Jarvis bridge, are affected by the
Windows launcher issue described in [the v0.15.1 release notes](docs/releases/v0.15.1.md).
Exposure requires all of:
Windows, the optional Telegram bridge running, `claude` resolving to the npm
`claude.cmd` launcher, and a message from a whitelisted Telegram user.

## Reporting a vulnerability

Do not put vulnerability details in a public issue, pull request or discussion.

1. Use GitHub private vulnerability reporting: open the repository's **Security** tab
   and choose **Report a vulnerability**
   (<https://github.com/Technoetic/agentic-vault/security/advisories/new>).
   This form exists only if private vulnerability reporting is enabled for the
   repository.
2. If the form is not available, open a public issue titled "Security contact
   request" that contains no details, and wait for a private channel.

Please include the version or commit, the operating system, how the plugin was
installed (for Windows, whether `claude` resolves to `claude.exe` or `claude.cmd`),
the steps to reproduce and the impact you observed. Responses are best effort and
no response time is guaranteed.

> **Maintainer note:** private vulnerability reporting is a repository setting
> (Settings → Code security → Private vulnerability reporting). Its current state
> could not be verified from the source tree. Enable it so that step 1 works.

## Scope

In scope:

- **Jarvis Telegram bridge** (`skills/agentic-vault/scripts/jarvis_bridge.py`):
  sender filtering (private chat, `chat.id == from.id`, whitelisted numeric user
  ID), inbox capture writes, how the Claude CLI is resolved and launched (fixed
  argv, prompt on stdin, Windows batch-launcher refusal, tool restriction), and
  handling of the bot token and logs.
- **Hooks**: the SessionStart injection hook (`hooks/`) and the vault git hooks
  and staged checker (`assets/git-hooks/`, `vault_healthcheck.py --staged`),
  including path containment, deny zones and link or junction handling.
- **Optional Aside adapter** (`assets/adapters/aside/`, `vault_adapters.py`): the
  PreToolUse hook that a user may merge into a vault's Claude settings (by default
  `.claude/settings.local.json`; with Claude Code's `if` filter it runs Windows
  PowerShell before `aside` commands), the helper script it runs from
  `00-meta/scripts/`, how the hook command resolves the vault path, and the
  read-only adapter report.
- **External judgment API transmission** (`jev_client.py`, `jev_ask.py`,
  `vault_judge.py`): what can be sent to the Jev API, the approval flag
  (`run --allow-network`), the secret filter and API key handling.
- Backup, recall and other scripts that read or write vault paths
  (`vault_paths.py`, `backup_vault.py`, `vault_recall.py`).

Out of scope (report these upstream): vulnerabilities in Claude Code, Codex,
Telegram or the Jev service themselves.

## Known limitations

These are documented design limits, not vulnerabilities. A report that shows one of
them being bypassed in a way the documentation does not describe is still welcome.

- **Unattended generation uses a bounded, tool-free context.** Since v0.18.0
  Jarvis calls Claude with `--bare`, `--tools ""`, `--setting-sources ""`,
  `--disable-slash-commands`, `--no-session-persistence`, `--strict-mcp-config`
  and `--settings '{"disableAllHooks":true}'`. The host reads permitted Markdown
  through the existing path/stable-file policy, rejects hardlinks and passes
  bounded evidence on stdin. This replaces the 0.17.1 bridge's broad
  Read/Grep/Glob grants. Prompt injection can still corrupt an answer; none of
  these flags certifies semantic truth or turns the CLI into an OS sandbox.
- **CLI compatibility and authentication change.** `--bare` skips CLAUDE.md
  auto-discovery, automatic memory, plugin sync and keychain/OAuth reads according
  to the installed Claude CLI help. It requires Anthropic API authentication
  (`ANTHROPIC_API_KEY`, or a supported explicitly supplied helper) or the selected
  third-party provider's authentication. The bridge currently supplies no helper.
  Subscription-only login does not satisfy this mode. Unsupported flags fail
  closed; the bridge does not fall back to wider tool/settings access. Local
  verification checks argv and synthetic processes, not a live provider session.
- **The host is still trusted.** Other sessions or manual commands do not inherit
  the bridge's no-tools boundary. Plugin files, the CLI binary and host environment
  remain trusted inputs. Settings sources are omitted for this bridge; separately
  managed host policy still applies. File-system permissions and sandboxing remain
  necessary for stronger OS isolation. A compromised whitelisted account can ask
  about the approved context it is allowed to receive.
- **Consumption budgets have specific scopes.** Jarvis generation stdin is at most
  64 KiB including evidence. stdout and stderr are each collected up to 64 KiB;
  exceeding either rejects the result and terminates the direct child. Pipe readers
  are cancellable and close even when a descendant retains a pipe. The bridge does
  not claim to kill an arbitrary process tree or bound a compromised executable's
  CPU/disk usage at the OS level. Recall title metadata is capped at 512 characters;
  this is not a total recall JSON response byte cap.
- **Deny zones are policy, not permissions.** Deny zones are enforced by the plugin's
  own scripts, by prompts and by any Read deny rules you install in Claude settings.
  They do not change file-system permissions.
- **The Aside hook runs a file from the vault.** Once merged, the hook runs
  `00-meta/scripts/aside-up.ps1` with `-ExecutionPolicy Bypass` before `aside`
  commands, and Claude Code runs PreToolUse hooks before its own permission check
  for the command (documented host behavior, not measured here). Anyone or anything
  that can write that file or the vault's `.claude/` settings (another agent allowed
  to edit files, a sync partner, a shared folder) can therefore run code without a
  shell approval, and a change to the script does not show up as a settings change.
  Keep the hook in `.claude/settings.local.json`, and turn it on only where those
  paths are trusted. The command resolves the vault through
  `$env:CLAUDE_PROJECT_DIR` inside PowerShell, so it points at the same file under a
  Git Bash or a PowerShell hook shell; if the variable is missing it runs nothing.
  Output that an `aside repl` script prints goes back to the calling agent and its
  model provider.
- **Git hooks are local gates.** Anyone who controls the repository can bypass them
  with `--no-verify` or by changing `core.hooksPath`. Use server-side checks if you
  need enforcement that cannot be bypassed.
- **Telegram access equals account access.** Anyone who controls a whitelisted
  Telegram account (for example a stolen phone or web session) can capture to the
  inbox and ask read-only questions.
- **The outbound secret filter is pattern-based.** Before a Jev request it rejects
  text that matches a known credential shape. Text is checked as written and again
  after NFKC normalization with invisible format characters (such as zero-width
  spaces) removed, so full-width or zero-width disguises of the shapes below are
  caught too.
  - Caught: `sk-`/`sk_` keys, GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`,
    `github_pat_`), AWS access key IDs (`AKIA…`), Jev/TypeSafe keys, JWTs, PEM
    private-key headers, Telegram bot tokens, Slack tokens (`xoxb-` and the other
    `xox?-` forms, `xapp-`) and webhook URLs, Google API keys (`AIza…`),
    credentials in URLs (`scheme://user:password@host`), `Authorization: Bearer`
    or `Basic` values, and a value of four or more characters after a label such as
    `password`, `passwd`, `secret`, `api_key`, `access_token`, `refresh_token` or
    `client_secret`, or after the Korean labels `비밀번호`, `비번`, `패스워드`,
    `암호`, `토큰`, `인증키`, `액세스 키`, `시크릿` and `API 키` (Korean labels only
    when the value is ASCII).
  - Structured input additionally rejects nonempty scalar values under explicit
    credential keys such as password, client_secret and API_KEY. Generic `secret`
    and `token` labels can be legitimate question labels and are not treated as
    credentials by that key rule. Requests have independent node, depth, character,
    integer and serialized-byte budgets; cycles are rejected and shared objects
    remain supported. Such labelled values may require rewording a benign rubric.
  - Not caught: passwords or keys with no label and no known prefix, other
    vendors' formats, values after other labels or in other languages, secrets
    with arbitrarily split token values or encoded (base64, hex), and anything inside images or
    attachments.

  Send only the minimal, approved text.
- **Windows requires a native `claude.exe` for Jarvis.** Since 0.15.1 the bridge
  refuses `.cmd` and `.bat` launchers because `cmd.exe` re-parses their arguments.

## 한국어 요약

- 공개 릴리스는 0.18.0이며 로컬 개선 소스는 미게시 0.19.0이다. 0.17.1 이하는 Jarvis의 API 인증 요건을 확인한 뒤 지원 소스로 갱신한다.
- 취약점은 공개 이슈에 쓰지 말고 GitHub **Security → Report a vulnerability**로 비공개 제보한다.
  이 기능은 저장소 설정에서 켜져 있어야 하며, 없으면 내용 없이 연락 요청 이슈만 연다.
- 범위: Jarvis 브리지, SessionStart·git 훅, 선택형 Aside 어댑터 훅·도우미, 외부 판단 API(Jev) 전송, 볼트 경로 처리.
- Aside 어댑터 훅은 사용자가 승인해 병합한 경우에만 존재하며(기본 `.claude/settings.local.json`), aside 명령 전에 `00-meta/scripts/aside-up.ps1`을 `-ExecutionPolicy Bypass`로 실행한다. Claude Code는 PreToolUse 훅을 명령 권한 확인보다 먼저 실행한다(문서 기준, 실측 아님). 그래서 그 파일이나 볼트의 `.claude/`를 쓸 수 있는 누구든(편집이 허용된 다른 에이전트, 동기화 상대, 공유 폴더) 셸 승인 없이 코드를 실행시킬 수 있고, 스크립트 내용 변경은 설정 변경으로 드러나지 않는다. 신뢰하는 경로에서만 켠다. 훅 명령은 PowerShell 안에서 `$env:CLAUDE_PROJECT_DIR`로 볼트를 찾으므로 Git Bash·PowerShell 훅 셸 모두 같은 파일을 가리키고, 변수가 없으면 아무것도 실행하지 않는다.
- `aside repl` 스크립트가 출력한 내용은 호출한 에이전트와 그 모델 제공자에게 간다. 외부 전송 금지 자료는 원문을 출력하지 않는다.
- 0.18.0의 Jarvis는 호스트가 허용 경로에서 선택한 제한된 근거만 보내며 모델 도구는 없다.
  `--bare`로 자동 CLAUDE.md·자동 기억·OAuth/키체인 읽기를 끄고, 설정·스킬·MCP·훅·세션 저장도 제한한다.
  이 모드는 API 인증이 필요하고 구독 로그인만으로는 실행되지 않는다. 0.17.1 이하의 넓은 읽기 권한을 대체한다.
  프롬프트 주입에 의한 오답이나 OS 수준 권한 문제를 모두 해결했다는 뜻은 아니다.
- 입력64KiB·stdout/stderr 각64KiB를 검사하고, 타임아웃/초과 뒤 reader와파이프를 정리한다.
  임의의 자식 프로세스 트리를 종료하는 OS 샌드박스는 아니다. 라이브 모델 인증·응답은 검증하지 않았다.
- Jev로 보내기 전 비밀 필터는 패턴 기반이다. 잡는 형식과 못 잡는 형식은 위 영어 목록에 있다.
  알려진 라벨 뒤의 줄바꿈 비밀번호·키는 전체 원문 검사로 차단한다. 라벨·알려진 접두사가 없는 비밀, 임의로 쪼갠 토큰 값이나 인코딩된 비밀은 놓칠 수 있다.

## OWASP provided-document traceability

See [the 2026 provided-document hardening record](docs/security/owasp-2026-hardening.md) for all ten risk IDs, code and regression tests, non-applicable training/vector components and residual limits. The supplied PDF retains publication placeholders; this is neither official-version certification nor an OWASP compliance claim.
