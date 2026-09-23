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
  settings allow and put it in the reply to the whitelisted user. Claude Code hooks
  that you configured yourself are not covered by this restriction. Protect
  sensitive files with file permissions or a sandbox.
- **Deny zones are policy, not permissions.** Deny zones are enforced by the plugin's
  own scripts, by prompts and by any Read deny rules you install in Claude settings.
  They do not change file-system permissions.
- **Git hooks are local gates.** Anyone who controls the repository can bypass them
  with `--no-verify` or by changing `core.hooksPath`. Use server-side checks if you
  need enforcement that cannot be bypassed.
- **Telegram access equals account access.** Anyone who controls a whitelisted
  Telegram account (for example a stolen phone or web session) can capture to the
  inbox and ask read-only questions.
- **The outbound secret filter is pattern-based.** It blocks common credential
  formats before a Jev request, but it cannot recognize every secret. Send only the
  minimal, approved text.
- **Windows requires a native `claude.exe` for Jarvis.** Since 0.15.1 the bridge
  refuses `.cmd` and `.bat` launchers because `cmd.exe` re-parses their arguments.

## 한국어 요약

- 보안 수정은 최신 릴리스(현재 0.15.1)에 다음 0.15.x 패치로 낸다. 0.15.0 이하는 0.15.1로 올린다.
- 취약점은 공개 이슈에 쓰지 말고 GitHub **Security → Report a vulnerability**로 비공개 제보한다.
  이 기능은 저장소 설정에서 켜져 있어야 하며, 없으면 내용 없이 연락 요청 이슈만 연다.
- 범위: Jarvis 브리지, SessionStart·git 훅, 외부 판단 API(Jev) 전송, 볼트 경로 처리.
- Jarvis의 읽기 전용은 Claude CLI 도구 제한과 프롬프트 정책이며 OS 경계가 아니다.
  프롬프트 인젝션은 완화할 뿐 막지 못한다.
