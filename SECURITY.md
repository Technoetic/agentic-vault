# Security policy

agentic-vault is a single-maintainer, standard-library-only plugin. This page says
which versions get fixes, how to report a vulnerability privately, what is in scope,
and which limits are known and documented rather than bugs.

## Supported versions

| Version | Security fixes |
|---|---|
| 0.45.1 (latest) | Yes. Security fixes target the latest source version. |
| 0.45.0 and earlier | No. Upgrade to 0.45.1 after reviewing the configured revocation egress, Jarvis API-auth, `via_bot` capture-origin and warning-certificate requirements in the release notes. The unpublished v0.40.0, v0.39.2, v0.39.1, v0.39.0, v0.38.0, v0.36.0 and v0.35.0 candidates are superseded. The v0.34.0-to-v0.35.1 transition alone preserves certificate-bound bytes. |

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
- **Optional deny-zone guard** (`assets/adapters/deny-guard/`, `vault_adapters.py`):
  the PreToolUse hook template that a user may merge into a vault's
  `.claude/settings.local.json` (never by default: `/vault-init` asks for it
  separately and installs nothing without a yes), the standalone `deny-guard.py`
  and its PowerShell parser `ps-parse.ps1` that the hook runs from
  `00-meta/scripts/`, the decisions it returns (only deny or ask, never allow)
  and the read-only adapter report (`adapters.deny_guard`). A way to make the
  guard answer allow, or to reach a deny zone through a path that its guide does
  not list as a limit, is in scope.
- **External judgment API transmission** (`jev_client.py`, `jev_ask.py`,
  `vault_judge.py`): what can be sent to the Jev API, the approval flag
  (`run --allow-network`), the secret filter and API key handling.
- **Opt-in session-transcript reader** (`vault_doctor.py --session-evidence`,
  `vault_session_evidence.py`): it reads one Claude Code transcript that the user
  names and, to recognise this plugin's own SessionStart status labels, the
  plugin's `hooks/hooks.json` (read-only, up to 64 KiB). A transcript holds every prompt, answer, tool result and hook output, and
  the reader must print only counts, lengths, numbers, booleans and fixed status
  words and, with `--session-instruction-paths`, instruction file paths. Output
  that carries transcript content, commands, identifiers or the transcript path is
  in scope.
- Backup, recall and other scripts that read or write vault paths
  (`vault_paths.py`, `backup_vault.py`, `vault_recall.py`).
- **Optional ontology contracts** (`vault_ontology.py`, `vault_ontology_query.py`,
  `vault_ontology_cli.py`): strict bounded JSON declarations and registry digests,
  exact source-span and current declaration/config bindings, current path and
  revocation policy, reviewed dependency closure, exact lookup ambiguity and
  withholding usable context after failed input/source rechecks. These helpers
  only read explicit inputs; the CLI is not an MCP server.

- **Optional recoverable bundles and review planner** (`vault_bundle.py`,
  `vault_review_plan.py`): bounded strict input, policy and source rechecks,
  explicit plan-digest authority, cooperating leases, journal CAS and
  forward recovery without clobbering unknown bytes; read-only bounded
  declared-graph propagation. A journal is not a signature or rollback log.
- **Opt-in generated-memory experiment**: strict authored action grammar,
  fresh synthetic-vault workers, bounded local transport, sanitized worker
  environments and source closure checks. Workers are not an OS sandbox;
  arbitrary hostile local code or a malicious same-user process is outside
  the experiment threat model. No company-vault input is accepted.

- **Optional handoff folding** (`vault_handoff_fold.py`): `analyze` only reads.
  `apply` is a write tool. It needs the current handoff hash and a caller-chosen
  threshold and protected-section count, creates new archive notes and never
  rewrites an existing one, replaces the handoff, and refuses before writing when
  its own output does not rebuild the original. Only cooperating writers observe its
  lock. The archive notes and the handoff are separate atomic replaces, not one
  transaction. A failed run keeps the notes unless the handoff provably holds the
  original bytes, and a killed run can leave unreferenced archive notes and a guard
  that needs manual removal. No hook or session command calls it.
- **Optional declared-path commits** (`vault_commit.py`): writes the shared Git index
  and history for the paths named. Scope includes the sha256 check per path, refusal
  of deny zones, `.git`, folders, links, Windows short names, shared notes, every path
  inside a linked working tree and unresolved conflict entries, the in-progress state
  check, compare-and-swap `update-ref` in `--own-lines`, and fixed-code output that
  carries no paths or note text unless `--show-git-output` copies failing Git output
  to stderr. It never deletes a lock and offers no way to skip hooks. It clears
  `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*` and
  `GIT_CONFIG_PARAMETERS`, which could replace `core.hooksPath`, and runs git from an
  absolute path, never an implicit current folder. `GIT_CONFIG_GLOBAL`,
  `GIT_CONFIG_SYSTEM` and repository settings can still change `core.hooksPath`, and
  this tool does not block that path. `--own-lines` uses `commit-tree`, so only
  `pre-commit` runs: `commit-msg`, `post-commit` and `commit.gpgsign` do not apply. It
  is not a lock and does not stop another session's plain `git commit`.
- **Optional Doctor reads and host execution** (`vault_doctor.py --capacity`,
  `--plugin-scope`, `--plugin-scope-host`; `vault_capacity.py`,
  `vault_plugin_scope.py`): `--capacity` reads the configured notes' sizes and
  runs bounded local `git` reads. `--plugin-scope` reads `.claude/settings.local.json`
  and `.claude/settings.json` in the vault, the user `settings.json`
  (`CLAUDE_CONFIG_DIR` when set, otherwise `~/.claude`), `00-meta/vault-config.json`
  and one health-check copy at `00-meta/scripts/vault_healthcheck.py`. It counts
  SessionStart hook entries and must not read or print a hook command. Output is
  counts and closed words. `--plugin-scope-host` also executes the `claude` program
  found the same way as `--host-version`: `claude plugin list --json` once in the
  vault, with fixed arguments, no shell and a time bound. The plugin makes no promise
  about what the host writes in its own folders.
- **Checkpoints** (`hooks/session_checkpoint.py`, `vault_state.py`): a checkpoint hashes
  each source under a 64 MiB bound and stores no note bytes. The hook prints only a
  fixed code on stderr and exits 0, so a failed checkpoint stops nothing.
- **Optional lesson cues** (`vault_lesson_cues.py`, `assets/adapters/lesson-cues/`):
  `build --write` replaces `00-meta/lesson-cues.json` and refuses a list over 256 KiB
  with `lesson_cues_too_large`. The hook is a template a user
  may merge into `.claude/settings.local.json`, never by default. It answers only a
  hint or a once-per-session `deny`, and a way to make it answer `allow` or `ask` is
  in scope. Cues are literals and globs, not regular expressions. It keeps a session
  record in the system temporary folder. It sets no `onFailure`, so a failed hook lets
  the call run: it is advisory, not a control. The compiler takes only rows whose whole
  status cell is a known in-force form, outside comment blocks and the rejected-drafts
  section. Cue values cannot hold format marks, Unicode line or paragraph separators or
  C1 control characters. The hook checks each
  compiled cue again and drops one the compiler could not have written, but it does not
  prove that a well-formed cue came from the ledger. The compiled file is not a
  deny-guard control. `vault_lesson_clusters.py` only reads.
- **Linked worktree handling** (`vault_worktrees.py` and its callers, `deny-guard.py`):
  a zone in a copy is closed by reading the folder's `.git` file, so a wrong file only
  closes more. A walk or a backup leaves a folder out only when git's admin folder also
  names that folder back, so hiding notes that way takes write access to the common git
  directory. A tool that takes a named path applies the same zone check, and
  `vault_commit.py` refuses every path inside a linked tree. A copy whose admin entry is
  gone returns to recall and snapshots until it is removed or repaired, with its zones
  still closed. Copies of the deny guard and the healthcheck installed in a vault keep
  the old behavior until they are updated. Keyed and ephemeral `evaluate_recall.py`
  reports hash `skipped_worktrees`; plain and keyless `--redact` reports show the folder
  names. The exceptions are listed as limits in
  [the hardening guide](docs/hardening.md#linked-worktree-copies-and-deny-zones).

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
- **Ontology contracts bind annotations, not truth or authority.** The explicit
  JSON sidecar is host-authored. `reviewed` labels do not authenticate a reviewer
  or approval, and hashes prove byte binding rather than semantic correctness.
  Exact matching does not resolve real-world identity or infer synonyms. A
  `ready_for_evidence` structured plan has reviewed definitions and valid literal
  slots; it does not establish that answer data exists, validate an answer or
  authorize SQL, commands or any external action. The helpers add no NLP, model,
  network or database calls. See [the guide](docs/ontology-contracts.md).
- **Consumption budgets have specific scopes.** Jarvis generation stdin is at most
  64 KiB including evidence. stdout and stderr are each collected up to 64 KiB;
  exceeding either rejects the result and terminates the direct child. Pipe readers
  are cancellable and close even when a descendant retains a pipe. The bridge does
  not claim to kill an arbitrary process tree or bound a compromised executable's
  CPU/disk usage at the OS level. Recall title metadata is capped at 512 characters;
  this is not a total recall JSON response byte cap.
- **Deny zones are policy, not permissions.** Deny zones are enforced by the plugin's
  own scripts, by prompts, by any Read deny rules you install in Claude settings
  and, if you merge it, by the optional deny-zone guard hook. They do not change
  file-system permissions.
- **The deny-zone guard is a guard, not a sandbox.** It can only narrow what the
  permission system lets through. It does not see files that a spawned process or
  a script opens by itself, paths assembled at run time that never spell a zone
  name (for example `Get-Content (Join-Path 90 -assets)`, string concatenation,
  aliases or variables defined earlier), Grep or Glob calls without a path that
  start at the vault root and walk through a zone, a junction swapped after
  the check, tools outside its hook matcher (MCP tools, for example) or commands
  that run without a tool call (other hooks, monitors that a plugin starts by
  itself). Claude Code older than v2.1.295 does not know
  `"onFailure": "block"`: there a guard that crashes, times out or finds no
  `python` lets the tool call run (fail-open). On newer hosts such a failure
  blocks the call instead, so a broken `python` blocks every matched call until
  the hook group is removed. Self-protection only asks. The full list is in
  [the guard's guide](assets/adapters/deny-guard/guide.md).
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
- **The deny-zone guard lives in the vault.** Once merged, every Read, Grep, Glob,
  Edit, Write, NotebookEdit, Bash, PowerShell and Monitor call runs
  `00-meta/scripts/deny-guard.py` with the `python` found on PATH, a PowerShell
  command that names a zone or a guard control also starts
  `00-meta/scripts/ps-parse.ps1` with `-ExecutionPolicy Bypass`, and the guard
  takes its zones from `00-meta/vault-config.json`. Anyone or anything that can
  write those files or the vault's `.claude/` settings (another agent allowed to
  edit files, a Codex session, a sync partner, another capture path) can
  therefore disable the guard, change its zones or run code before every matched
  call. Claude Code edits and shell commands that name these controls are asked
  about, not denied; commands that reach them without naming them (through a
  parent folder or a script) and changes made outside Claude Code are not seen.
  Keep the hook in `.claude/settings.local.json` and turn it on only where those
  paths are trusted.
- **Git hooks are local gates.** Anyone who controls the repository can bypass them
  with `--no-verify` or by changing `core.hooksPath`. Use server-side checks if you
  need enforcement that cannot be bypassed.
- **Telegram access equals account access.** Anyone who controls a whitelisted
  Telegram account (for example a stolen phone or web session) can capture to the
  inbox and ask read-only questions.
- **Inline-bot captures are untrusted.** A captured message that carries Telegram's
  `via_bot` field is recorded as `content_origin: via_bot`, because a third-party
  bot produced the text even though the sender is whitelisted. This also applies to
  a memo the user deliberately sends through an inline bot. Forward metadata still
  wins (`forwarded`). A privileged note that consumes such a capture gets
  `provenance-unresolved`. The bridge requests only `message` updates, so
  `guest_message` and other update kinds are not processed. Captures stored before
  this change are not relabelled: an inline-bot memo captured by an older bridge
  stays `own` (or `url`), so review older `10-inbox/jarvis/` captures by hand if
  inline bots were used. Engines older than v0.26.0 report `via_bot` captures as
  `provenance-invalid`, so update every installation that reads the vault together.
  Moving Jarvis to an authenticated channel is still open.
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

- 보안 수정 대상은 최신 0.45.1 소스다. 0.45.0 이하는 릴리스 노트의 Jarvis API 인증·`via_bot` 수집 출처·경고 승격 인증서 요건을 확인한 뒤 0.45.1으로 갱신한다. 공개 전 CI에서 실패해 대체한 후보의 기록은 보존한다. 이번 전환은 인증서가 묶는 일곱 파일·LF corpus를 유지하며 실제 설치 바이트 확인과 harmless replay는 최종 게이트다.
- 취약점은 공개 이슈에 쓰지 말고 GitHub **Security → Report a vulnerability**로 비공개 제보한다.
  이 기능은 저장소 설정에서 켜져 있어야 하며, 없으면 내용 없이 연락 요청 이슈만 연다.
- 범위: Jarvis 브리지, SessionStart·git 훅, 선택형 Aside 어댑터 훅·도우미, 선택형 deny zone 가드 훅·스크립트, 외부 판단 API(Jev) 전송, 선택형 세션 기록 읽기(`--session-evidence`, 내용을 출력하지 않아야 한다), 볼트 경로 처리.
- 연결 작업 트리 안의 금지 구역은 경로를 받는 모든 도구에서 본 구역과 같은 방식으로 거부한다. `vault_commit.py`는 그 안의 경로를 모두 `denied_path`로, Windows 짧은 이름을 `unsafe_path`로, 미해결 충돌을 `unmerged_paths`로 거부하고, 같은 패치의 재실행은 `already_applied`로 거부한다. `GIT_CONFIG_COUNT`·`GIT_CONFIG_KEY_*`·`GIT_CONFIG_VALUE_*`·`GIT_CONFIG_PARAMETERS`는 비우지만 `GIT_CONFIG_GLOBAL`·`GIT_CONFIG_SYSTEM`·저장소 설정으로 `core.hooksPath`를 바꾸는 길은 막지 못한다. 폴더를 걷기에서 빼는 일은 git 관리 폴더가 그 폴더를 되가리킬 때만 하므로, 관리 기록이 사라진 사본은 지우거나 복구할 때까지 검색과 스냅숏에 돌아온다. 볼트에 설치된 healthcheck와 deny zone 가드 사본은 갱신하기 전까지 옛 동작이다. 키 모드 `evaluate_recall.py` 보고서는 `skipped_worktrees`를 해시로 가린다.
- 체크포인트는 64 MiB 상한 안에서 원본 해시만 남기고 노트 내용을 저장하지 않는다. 훅은 고정 코드만 표준 오류에 내고 종료 코드 0을 유지한다. 교훈 단서 컴파일은 상태 칸 전체가 유효한 형태인 줄만 넣고 주석 블록과 `## 기각 대장` 구역을 뺀다. 접기 실행이 실패하면 handoff가 원본임을 확인하지 못하는 한 보관 노트를 지우지 않고, 강제 종료는 사람이 지워야 하는 guard를 남길 수 있다.
- 새 쓰기 도구는 `vault_handoff_fold.py apply`와 `vault_commit.py`이며 둘 다 직접 부를 때만 돈다. `vault_doctor.py --plugin-scope`는 설정 파일 세 개·vault-config·healthcheck 사본 하나를 읽고, `--plugin-scope-host`만 `claude plugin list --json`을 고정 인자·셸 없이 실행한다. 교훈 단서 훅은 사용자가 병합할 때만 존재하며 힌트나 세션당 1회 `deny`만 내고 실패하면 호출이 그대로 지나간다.
- Aside 어댑터 훅은 사용자가 승인해 병합한 경우에만 존재하며(기본 `.claude/settings.local.json`), aside 명령 전에 `00-meta/scripts/aside-up.ps1`을 `-ExecutionPolicy Bypass`로 실행한다. Claude Code는 PreToolUse 훅을 명령 권한 확인보다 먼저 실행한다(문서 기준, 실측 아님). 그래서 그 파일이나 볼트의 `.claude/`를 쓸 수 있는 누구든(편집이 허용된 다른 에이전트, 동기화 상대, 공유 폴더) 셸 승인 없이 코드를 실행시킬 수 있고, 스크립트 내용 변경은 설정 변경으로 드러나지 않는다. 신뢰하는 경로에서만 켠다. 훅 명령은 PowerShell 안에서 `$env:CLAUDE_PROJECT_DIR`로 볼트를 찾으므로 Git Bash·PowerShell 훅 셸 모두 같은 파일을 가리키고, 변수가 없으면 아무것도 실행하지 않는다.
- `aside repl` 스크립트가 출력한 내용은 호출한 에이전트와 그 모델 제공자에게 간다. 외부 전송 금지 자료는 원문을 출력하지 않는다.
- deny zone 가드 훅은 사용자가 따로 승인해 병합한 경우에만 존재한다(기본 `.claude/settings.local.json`, `/vault-init`은 답이 없으면 설치하지 않는다). 병합하면 Read·Grep·Glob·Edit·Write·NotebookEdit·Bash·PowerShell·Monitor 호출마다 `00-meta/scripts/deny-guard.py`를 PATH의 `python`으로 실행하고, 구역 이름이나 가드 제어 파일 이름이 보이는 PowerShell 명령에는 `00-meta/scripts/ps-parse.ps1`도 `-ExecutionPolicy Bypass`로 실행한다. `deny`·`ask`만 내고 `allow`는 내지 않는다.
- 가드이지 샌드박스가 아니다. 실행된 프로그램·스크립트가 스스로 여는 파일, 구역 이름을 쓰지 않고 실행 중에 조립한 경로(`Join-Path 90 -assets` 등), 볼트 루트에서 경로 없이 도는 Grep·Glob, 확인 뒤의 정션 바꿔치기, 훅 matcher 밖의 도구(MCP 도구 등)와 도구 호출 없이 실행되는 명령(다른 훅, 플러그인이 저절로 시작하는 monitor)은 보지 못한다. Claude Code v2.1.295 미만에서는 가드가 충돌하거나 시간을 넘기거나 python이 없으면 호출이 그대로 진행된다(fail-open). v2.1.295 이상에서는 반대로 호출이 막히므로, python이 깨지면 그 훅 그룹을 지워 되돌린다. 가드 스크립트·`00-meta/vault-config.json`·볼트의 `.claude/`를 쓸 수 있는 누구든(편집이 허용된 다른 에이전트, Codex 세션, 동기화 상대) 가드를 끄거나 구역을 바꾸거나 매 호출 전에 실행될 코드를 바꿀 수 있다. Claude Code에서 이 파일들을 이름으로 가리키는 편집·명령은 확인(ask)만 받고, 이름을 쓰지 않고 닿는 명령(상위 폴더, 스크립트)과 Claude Code 밖의 변경은 보지 못한다.
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
