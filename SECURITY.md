# Security policy

agentic-vault is a single-maintainer, standard-library-only plugin. This page says
which versions get fixes, how to report a vulnerability privately, what is in scope,
and which limits are known and documented rather than bugs.

## Supported versions

| Version | Security fixes |
|---|---|
| 0.15.1 (latest) | Yes. Fixes ship as the next 0.15.x patch. |
| 0.15.0 and earlier | No. Upgrade to 0.15.1. |

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

- **Prompt injection is mitigated, not prevented.** Jarvis Q&A and briefing sessions
  read vault notes, which can contain untrusted text such as web clips or captures.
  The sessions get only the Read, Grep and Glob tools (`--tools`), load no MCP
  servers (`--strict-mcp-config`) and receive a prompt policy that forbids deny
  zones and `.env`. This is a Claude CLI tool restriction, not an operating-system
  boundary. A steered session can still read what those tools and your Claude
  settings allow and put it in the reply to the whitelisted user. Hooks are not
  tools, so the tool restriction does not stop them; the sessions therefore also
  pass `--settings '{"disableAllHooks":true}'`, which turns off user, plugin and
  vault hooks. With Claude Code 2.1.252 a user-scope plugin Stop hook wrote five
  files into the session directory without this setting and none with it. Protect
  sensitive files with file permissions or a sandbox.
- **Read, Grep and Glob are not confined to the vault.** The bridge pre-approves
  the three tools with `--allowedTools Read,Grep,Glob`, which has no path
  condition. A steered session can therefore read any file that the bridge's
  operating-system user can read, such as `~/.ssh`, `~/.vault-jarvis` or other
  projects, unless a Read deny rule in your Claude settings blocks it. The deny
  zones in the prompt are policy only. If that matters, run the bridge as an
  operating-system user that cannot read files outside the vault, or in a sandbox.
  This follows from the Claude Code permission model; it was not confirmed by
  running the real CLI.
- **The vault's Claude settings load without a trust prompt.** Sessions
  start in the vault directory, and `claude -p` skips the workspace trust dialog.
  The vault's project and local settings (`.claude/settings.json`,
  `.claude/settings.local.json`) therefore load on every Q&A and every scheduled
  briefing, with no one present to approve them. Hooks in them do not run because
  of `disableAllHooks`, but other settings still apply, so anyone who can write to
  the vault's `.claude/` folder (another agent, a sync partner or a shared-folder
  member, not only you) can still change how these sessions behave. Treat that
  write access as sensitive.
  The bridge does not pass `--setting-sources` or `--restricted`, because both
  would also drop the deny-zone Read rules that `/vault-init` offers to merge
  into the vault's `.claude/settings.json`.
- **Deny zones are policy, not permissions.** Deny zones are enforced by the plugin's
  own scripts, by prompts and by any Read deny rules you install in Claude settings.
  They do not change file-system permissions.
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
  - Not caught: passwords or keys with no label and no known prefix, other
    vendors' formats, values after other labels or in other languages, secrets
    split across lines or encoded (base64, hex), and anything inside images or
    attachments.

  Send only the minimal, approved text.
- **Windows requires a native `claude.exe` for Jarvis.** Since 0.15.1 the bridge
  refuses `.cmd` and `.bat` launchers because `cmd.exe` re-parses their arguments.

## 한국어 요약

- 보안 수정은 최신 릴리스(현재 0.15.1)에 다음 0.15.x 패치로 낸다. 0.15.0 이하는 0.15.1로 올린다.
- 취약점은 공개 이슈에 쓰지 말고 GitHub **Security → Report a vulnerability**로 비공개 제보한다.
  이 기능은 저장소 설정에서 켜져 있어야 하며, 없으면 내용 없이 연락 요청 이슈만 연다.
- 범위: Jarvis 브리지, SessionStart·git 훅, 외부 판단 API(Jev) 전송, 볼트 경로 처리.
- Jarvis의 읽기 전용은 Claude CLI 도구 제한과 프롬프트 정책이며 OS 경계가 아니다.
  프롬프트 인젝션은 완화할 뿐 막지 못한다.
- Read·Grep·Glob은 볼트로 한정되지 않는다. 경로 조건 없이 사전 승인되므로 브리지를 실행한
  OS 사용자가 읽을 수 있는 파일(`~/.ssh`, `~/.vault-jarvis` 등)은 Claude 설정의 Read 거부
  규칙이 없으면 읽힐 수 있다.
- `claude -p`는 작업 공간 신뢰 확인을 건너뛴다. 볼트의 project·local Claude 설정과 훅이
  Q&A·예약 브리핑마다 사람 없이 로드되므로, 볼트를 쓸 수 있는 누구든(다른 에이전트·동기화
  상대 포함) 넣은 훅이 브리지 사용자 권한으로 실행된다.
- Jev로 보내기 전 비밀 필터는 패턴 기반이다. 잡는 형식과 못 잡는 형식은 위 영어 목록에 있다.
  라벨·알려진 접두사가 없는 비밀, 여러 줄로 나뉘거나 인코딩된 비밀은 통과한다.
