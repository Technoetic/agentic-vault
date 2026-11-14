"""Shared texts name Antigravity CLI next to Claude Code and Codex (v0.48.0, item 5).

The shared skill, `/vault-session-start`, `/vault-lint` and the AGENTS stub carry the exact replacements of the
v0.48.0 specification (section 3.6), and `skills/agentic-vault/references/antigravity.md` says how the generated
Antigravity package is installed and used and what was not checked. Reverting the published v0.48.0 replacements
gives back the published v0.47.0 skill and stub byte for byte. The current skill additionally permits the explicit
v0.49.0 task-workflow routing section; removing only that section must preserve the complete v0.48.0 skill.

No Python function in the repository builds a vault's AGENTS.md: the agent follows `/vault-upgrade` step 1-1 (and
`/vault-init` step 4-3). `build_vault_agents_md` below follows that text, and the size test bounds the result.
`/vault-upgrade` regenerates an AGENTS.md only because it carries the `agentic-vault:generated` comment; there is no
version stamp, so a stub change reaches every vault with a generated AGENTS.md on its next upgrade.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "agentic-vault" / "scripts"
SKILL = ROOT / "skills" / "agentic-vault" / "SKILL.md"
REFERENCE = ROOT / "skills" / "agentic-vault" / "references" / "antigravity.md"
SESSION_START = ROOT / "commands" / "vault-session-start.md"
LINT = ROOT / "commands" / "vault-lint.md"
UPGRADE = ROOT / "commands" / "vault-upgrade.md"
INIT = ROOT / "commands" / "vault-init.md"
TEMPLATES = ROOT / "assets" / "templates"
STUB = TEMPLATES / "AGENTS-vault-stub.md"

# SHA-256 of `git show v0.47.0:<path>` (CRLF folded to LF) for the two files whose v0.47.0 text must survive.
V0470_SKILL_SHA256 = "138723f0d44ea34ff7292488a5b1b0bc74b89c1768dc5a52f63e1b3c3126421d"
V0470_STUB_SHA256 = "50faf8a4888a15f8ec505ca70e7b284969bbb9a5a4204c429dce64e19a868552"

REFERENCE_LINK = "[references/antigravity.md](references/antigravity.md)"

# skills/agentic-vault/SKILL.md
DESCRIPTION_OLD = " with Claude Code or Codex,"
DESCRIPTION_NEW = " with Claude Code, Codex or Antigravity CLI,"
CODEX_BULLET = (
    "- **Codex:** 먼저 [references/codex.md](references/codex.md)를 읽고 `$agentic-vault:agentic-vault <작업> [인자]`를 "
    "공통 명령 문서에 연결하라(독립 스킬 설치는 `$agentic-vault <작업>`). 세션 시작·검색·종료·검사·백업과 기존 노트 작업을 "
    "같은 엔진으로 수행한다. Claude Code의 `/vault-*` 진입점은 그대로 사용한다."
)
ANTIGRAVITY_BULLET = (
    "- **Antigravity CLI:** 먼저 " + REFERENCE_LINK + "를 읽는다. Antigravity용 패키지는 저장소의 `antigravity/` 폴더이며 "
    "명령 15개가 같은 이름의 스킬(예: `vault-session-start`)로 들어 있다. 훅은 플러그인 설치 폴더에서 실행되고 볼트는 "
    "작업 공간에서 찾는다."
)
TIERS_OLD = "(Claude Code의 CLAUDE.md·rules, Codex의 AGENTS.md)"
TIERS_NEW = "(Claude Code의 CLAUDE.md·rules, Codex·Antigravity CLI의 AGENTS.md)"
NOTATION_START = "아래 `/vault-*` 표기는 Claude Code 명령이다."
NOTATION_OLD_END = "Jarvis는 Claude CLI를 사용하는 별도 연동이다."
NOTATION_APPENDIX = (
    " Antigravity CLI에서는 같은 이름의 스킬(`vault-session-start` 등)을 쓴다(" + REFERENCE_LINK + ")."
)
CAP_OLD = "(Claude Code 훅 출력 10,000자 — 엔진은 9,500자로 묶는다)"
CAP_NEW = (
    "(Claude Code 훅 출력 10,000자 — 엔진은 9,500자로 묶는다. Antigravity CLI에도 같은 9,500자 묶음을 "
    "`injectSteps` 하나로 넣으며 그쪽 상한은 확인하지 않았다)"
)
REFERENCE_BULLET = "- " + REFERENCE_LINK + " — Antigravity CLI 패키지·스킬 이름·훅 동작·자리표시자·한계"

# commands/vault-session-start.md and commands/vault-lint.md
SESSION_START_OLD = (
    "주입이 없으면 `${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py`를 Python으로 실행해 같은 경로 검증·예산 제한을 "
    "적용한 출력을 사용하라(`CLAUDE_PROJECT_DIR`은 현재 볼트 루트)."
)
SESSION_START_NEW = (
    "주입이 없으면 `${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py --vault <볼트 루트>`를 Python으로 실행해 같은 "
    "경로 검증·예산 제한을 적용한 출력을 사용하라."
)
LINT_RUN_OLD = 'python "${CLAUDE_PLUGIN_ROOT}/skills/agentic-vault/scripts/vault_healthcheck.py"'
LINT_RUN_NEW = LINT_RUN_OLD + ' --vault "<볼트 루트>"'
LINT_PATH_OLD = (
    "- 볼트 경로는 스크립트가 `CLAUDE_PROJECT_DIR`(없으면 cwd)에서 자동으로 읽는다. 다른 볼트를 검사하려면 "
    "`--vault <경로>`를 붙인다."
)
LINT_PATH_NEW = "- 볼트 경로는 `--vault`로 넘긴다. 생략하면 스크립트가 `CLAUDE_PROJECT_DIR`(Claude Code), 없으면 현재 폴더를 쓴다."

# assets/templates/AGENTS-vault-stub.md
STUB_FIRST_OLD = (
    "<!-- agentic-vault:generated — AGENTS-vault-stub.md와 .claude/rules/의 공통 규칙에서 생성. 직접 편집하지 말고 "
    "/vault-upgrade 또는 Codex의 $agentic-vault:agentic-vault upgrade로 재생성한다. -->"
)
STUB_FIRST_NEW = (
    "<!-- agentic-vault:generated — AGENTS-vault-stub.md와 .claude/rules/의 공통 규칙에서 생성. 직접 편집하지 말고 "
    "/vault-upgrade(Codex는 $agentic-vault:agentic-vault upgrade, Antigravity CLI는 vault-upgrade 스킬)로 재생성한다. -->"
)
STUB_READS_OLD = "Codex는 CLAUDE.md를"
STUB_READS_NEW = "Codex와 Antigravity CLI는 CLAUDE.md를"
STUB_CODEX_LAST_LINE = "  기존 공통 절차와 Python 엔진을 사용한다. 플러그인 경로는 설치된 SKILL.md에서 해석한다."
STUB_ANTIGRAVITY_LINES = (
    "- Antigravity CLI에서는 플러그인의 `agentic-vault` 스킬과 `references/antigravity.md`를 읽는다.",
    "  명령은 같은 이름의 스킬(`vault-session-start`·`vault-recall`·`vault-session-end`·`vault-lint` 등)로",
    "  쓴다. 플러그인 경로는 설치된 SKILL.md에서 해석한다.",
)
STUB_ANTIGRAVITY_BULLET = (
    "- Antigravity CLI에서는 플러그인의 `agentic-vault` 스킬과 `references/antigravity.md`를 읽는다. 명령은 같은 "
    "이름의 스킬(`vault-session-start`·`vault-recall`·`vault-session-end`·`vault-lint` 등)로 쓴다. 플러그인 경로는 "
    "설치된 SKILL.md에서 해석한다."
)
STUB_CAP_OLD = "Codex 표시 상한에 따른 생략"
STUB_CAP_NEW = "호스트 표시 상한에 따른 생략"

# The AGENTS.md /vault-upgrade builds: the procedure text the helper below follows.
UPGRADE_INPUT = (
    "입력은 `${CLAUDE_PLUGIN_ROOT}/assets/templates/AGENTS-vault-stub.md`의 `{{VAULT_NAME}}`을 현재 볼트명으로 "
    "치환한 내용 전체와 설치된 rules 6개 본문이다."
)
UPGRADE_ORDER = (
    "**architecture → linking → frontmatter → workflow → collab → browser** 순서로 빈 줄을 두고 연결하며, "
    "각 rule의 맨 앞 `agentic-vault:rule engine=` HTML 주석 블록만 제거한다."
)
UPGRADE_DECISION = "AGENTS.md가 없으면 생성하고, `agentic-vault:generated` 주석이 있으면 생성 산출물로 재생성하라."
RULE_ORDER = ("architecture", "linking", "frontmatter", "workflow", "collab", "browser")
RULE_COMMENT = re.compile(r"\A<!--[^\n]*agentic-vault:rule engine=.*?-->[ \t]*\n", re.S)
AGENTS_MAX_BYTES = 24_000

REFERENCE_MAX_BYTES = 9_000
REFERENCE_HEADINGS = ("## 설치", "## 스킬 이름", "## 자리표시자", "## 도구 대응", "## 훅", "## 하지 않는 것",
                      "## 데이터", "## 확인하지 않은 것")
TOOL_PAIRS = {("Read", "view_file"), ("Grep", "grep_search"), ("Glob", "find_by_name"),
              ("Bash", "run_command"), ("PowerShell", "run_command"), ("Edit", "replace_file_content"),
              ("Write", "write_to_file"), ("AskUserQuestion", "ask_question"), ("Agent", "invoke_subagent"),
              ("Task", "invoke_subagent")}
LINK = re.compile(r"\]\(([^)\s]+)\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _folded(text: str) -> str:
    return " ".join(text.split())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tagged_skill(tag: str) -> str:
    # Match the released-period differential tests: archive/shallow checkouts may lack Git history.
    # CI fetches tags (fetch-depth: 0); these historical comparisons never fetch or use network data.
    git = shutil.which("git")
    if git is None:
        raise unittest.SkipTest(f"cannot compare with {tag}: git is not installed")
    try:
        completed = subprocess.run(
            [git, "show", f"refs/tags/{tag}:skills/agentic-vault/SKILL.md"],
            cwd=ROOT, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise unittest.SkipTest(f"cannot compare with {tag}: git show failed ({type(exc).__name__})") from None
    if completed.returncode:
        raise unittest.SkipTest(f"cannot compare with {tag}: the tag is not in this checkout (a shallow clone has no tags)")
    return completed.stdout.decode("utf-8").replace("\r\n", "\n")


def _section(text: str, heading: str) -> str:
    """From `heading` to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = text.index(heading + "\n")
    following = re.compile(r"(?m)^#{1,%d} " % level)
    match = following.search(text, start + len(heading) + 1)
    return text[start:match.start() if match else len(text)]


def _replace_once(case: unittest.TestCase, text: str, old: str, new: str) -> str:
    case.assertEqual(text.count(old), 1, f"expected exactly one {old[:60]!r}")
    return text.replace(old, new)


def build_vault_agents_md(vault_name: str) -> str:
    """The AGENTS.md of `/vault-upgrade` step 1-1: the whole stub with the vault name, then the six rule bodies.

    The installed rules are copies of `assets/templates/rules/vault-*.md`. Parts are joined with one blank line and
    each rule loses only its leading `agentic-vault:rule engine=` comment block.
    """
    parts = [_read(STUB).replace("{{VAULT_NAME}}", vault_name).rstrip("\n")]
    for name in RULE_ORDER:
        body, count = RULE_COMMENT.subn("", _read(TEMPLATES / "rules" / f"vault-{name}.md"), count=1)
        if count != 1:
            raise AssertionError(f"vault-{name}.md has no leading rule comment block")
        parts.append(body.strip("\n"))
    return "\n\n".join(parts) + "\n"


class SkillTextTests(unittest.TestCase):
    """The six replacements in skills/agentic-vault/SKILL.md, each in its place."""

    def setUp(self) -> None:
        self.text = _read(SKILL)
        self.lines = self.text.split("\n")

    def test_description_names_antigravity_cli(self) -> None:
        self.assertTrue(self.lines[2].startswith('description: "Use when working in an agentic-vault directory'
                                                 + DESCRIPTION_NEW))
        self.assertNotIn(DESCRIPTION_OLD, self.text)
        self.assertLessEqual(len(self.lines[2][len('description: "'):-1]), 1024)

    def test_antigravity_bullet_follows_the_codex_bullet(self) -> None:
        codex = self.lines.index(CODEX_BULLET)
        self.assertEqual(self.lines[codex + 1], ANTIGRAVITY_BULLET)
        self.assertLess(codex, self.lines.index("## 1. 이 볼트는 무엇인가 — 에이전틱 메모리의 4렌즈"))

    def test_memory_tiers_name_antigravity_agents_md(self) -> None:
        self.assertIn(TIERS_NEW, self.text)
        self.assertNotIn(TIERS_OLD, self.text)
        self.assertIn(TIERS_NEW, _section(self.text, "## 1. 이 볼트는 무엇인가 — 에이전틱 메모리의 4렌즈"))

    def test_command_notation_paragraph_names_the_skills(self) -> None:
        section = _section(self.text, "## 5. 명령 15종 — 언제 쓰는가")
        paragraph = next(line for line in section.split("\n") if line.startswith(NOTATION_START))
        self.assertTrue(paragraph.endswith(NOTATION_OLD_END + NOTATION_APPENDIX))
        self.assertEqual(self.text.count(NOTATION_APPENDIX), 1)

    def test_host_cap_sentence_names_inject_steps(self) -> None:
        section = _section(self.text, "## 6. 세션 인계 워크플로우")
        self.assertIn(CAP_NEW, section)
        self.assertNotIn(CAP_OLD, self.text)

    def test_reference_list_ends_with_antigravity(self) -> None:
        section = _section(self.text, "## 7. 참조 문서")
        bullets = [line for line in section.split("\n") if line.startswith("- ")]
        self.assertEqual(bullets[-1], REFERENCE_BULLET)
        self.assertTrue(bullets[0].startswith("- [references/codex.md](references/codex.md) — Codex"))
        self.assertEqual(self.text.rstrip("\n").split("\n")[-1], REFERENCE_BULLET)

    def test_reference_is_linked_three_times(self) -> None:
        self.assertEqual(self.text.count(REFERENCE_LINK), 3)

    def test_published_v048_additions_preserve_v047_skill_bytes(self) -> None:
        # The old whole-file hash proves the v0.48.0 migration, not that the shared skill can never gain a route.
        # Keep that exact guarantee against preserved release blobs rather than hashing today's later additions.
        historical = _tagged_skill("v0.47.0")
        self.assertEqual(_sha256(historical), V0470_SKILL_SHA256)
        text = _replace_once(self, _tagged_skill("v0.48.0"), DESCRIPTION_NEW, DESCRIPTION_OLD)
        text = _replace_once(self, text, ANTIGRAVITY_BULLET + "\n", "")
        text = _replace_once(self, text, TIERS_NEW, TIERS_OLD)
        text = _replace_once(self, text, NOTATION_APPENDIX, "")
        text = _replace_once(self, text, CAP_NEW, CAP_OLD)
        text = _replace_once(self, text, "\n" + REFERENCE_BULLET, "")
        self.assertEqual(text, historical)
        self.assertEqual(_sha256(text), V0470_SKILL_SHA256)

    def test_current_skill_only_adds_task_workflow_routing_to_v048(self) -> None:
        route = _section(self.text, "## 선택형 작업 명세와 검토")
        # v0.49.0 intentionally adds one opt-in route; every pre-existing byte, including host-specific text,
        # must still equal v0.48.0. Unknown removals or additions outside this section continue to fail.
        self.assertEqual(_replace_once(self, self.text, route, ""), _tagged_skill("v0.48.0"))

    def test_task_workflow_routes_are_opt_in_and_data_only(self) -> None:
        # Current instruction checks run even without release history, and target only the new routing section.
        heading = "## 선택형 작업 명세와 검토"
        self.assertEqual(self.text.count(heading + "\n"), 1)
        route = _section(self.text, heading)
        for literal in ("[작업 명세](../../docs/task-contracts.md)",
                        "`scripts/vault_task_contracts.py` capture / inspect / revise",
                        "[단계별 검증](../../docs/staged-evaluation.md)",
                        "`scripts/vault_staged_evaluation.py` evaluate",
                        "[개선 반복](../../docs/improvement-cycles.md)",
                        "`scripts/vault_improvement_cycles.py` audit",
                        "명시적으로 기록할 때", "준비 상태는 실행 승인이 아니다",
                        "자동 실행·모델 호출·게시·권한 부여를 하지 않는다",
                        "이미 확정된 단순 작업에 새 인터뷰를 강제하지 않는다"):
            with self.subTest(route_literal=literal):
                self.assertIn(literal, route)

    def test_claude_code_and_codex_texts_are_kept(self) -> None:
        for literal in (CODEX_BULLET, "Claude Code의 `/vault-*` 진입점은 그대로 사용한다",
                        "Codex는 `$agentic-vault:agentic-vault session-start`",
                        "Claude Code 훅 출력 10,000자 — 엔진은 9,500자로 묶는다",
                        "- [references/codex.md](references/codex.md) — Codex 진입점·설치 경로 해석·공통 명령 연결·백업 CLI"):
            with self.subTest(literal=literal):
                self.assertIn(literal, self.text)


class CommandTextTests(unittest.TestCase):
    """`/vault-session-start` and `/vault-lint` pass the vault with `--vault`."""

    def test_session_start_runs_the_hook_with_the_vault(self) -> None:
        text = _read(SESSION_START)
        self.assertIn(SESSION_START_NEW, _section(text, "## 1. 상태 파일 로드"))
        self.assertNotIn(SESSION_START_OLD, text)
        self.assertNotIn("CLAUDE_PROJECT_DIR", text)
        hook = _read(ROOT / "hooks" / "session_start.py")
        self.assertRegex(hook, r"add_argument\(\s*\"--vault\"")

    def test_lint_runs_the_check_with_the_vault(self) -> None:
        text = _read(LINT)
        step = _section(text, "## 1. 검증 엔진 실행")
        lines = step.split("\n")
        self.assertIn(LINT_RUN_NEW, lines)
        self.assertNotIn(LINT_RUN_OLD, lines)
        self.assertIn(LINT_PATH_NEW, lines)
        self.assertNotIn(LINT_PATH_OLD, text)
        fence = lines.index("```")
        self.assertEqual(lines[fence + 1], LINT_RUN_NEW)
        self.assertEqual(lines[fence + 2], "```")


class AgentsStubTests(unittest.TestCase):
    """The AGENTS stub names Antigravity CLI; the AGENTS.md built from it stays within 24,000 UTF-8 bytes."""

    def setUp(self) -> None:
        self.text = _read(STUB)
        self.lines = self.text.split("\n")

    def test_first_line_names_the_three_upgrade_paths(self) -> None:
        self.assertEqual(self.lines[0], STUB_FIRST_NEW)

    def test_claude_md_sentence_names_antigravity(self) -> None:
        self.assertIn(STUB_READS_NEW, self.text)
        self.assertNotIn(STUB_READS_OLD, self.text)

    def test_antigravity_bullet_follows_the_codex_bullet_wrapped_alike(self) -> None:
        codex_end = self.lines.index(STUB_CODEX_LAST_LINE)
        self.assertEqual(tuple(self.lines[codex_end + 1:codex_end + 4]), STUB_ANTIGRAVITY_LINES)
        self.assertTrue(self.lines[codex_end + 4].startswith("- 세션에 handoff/hot이 이미 주입됐으면"))
        self.assertEqual(_folded("\n".join(STUB_ANTIGRAVITY_LINES)), STUB_ANTIGRAVITY_BULLET)
        width = max(len(line) for line in self.lines[codex_end - 2:codex_end + 1])
        for line in STUB_ANTIGRAVITY_LINES:
            with self.subTest(line=line[:30]):
                self.assertLessEqual(len(line), width + 10)

    def test_display_cap_is_the_host_cap(self) -> None:
        self.assertIn(STUB_CAP_NEW, self.text)
        self.assertNotIn(STUB_CAP_OLD, self.text)

    def test_reverting_the_replacements_gives_back_v0470(self) -> None:
        text = _replace_once(self, self.text, STUB_FIRST_NEW, STUB_FIRST_OLD)
        text = _replace_once(self, text, STUB_READS_NEW, STUB_READS_OLD)
        text = _replace_once(self, text, "\n".join(STUB_ANTIGRAVITY_LINES) + "\n", "")
        text = _replace_once(self, text, STUB_CAP_NEW, STUB_CAP_OLD)
        self.assertEqual(_sha256(text), V0470_STUB_SHA256)

    def test_upgrade_text_matches_the_builder(self) -> None:
        # The helper follows these sentences; if the procedure changes, the helper has to change with it.
        upgrade = _folded(_read(UPGRADE))
        for sentence in (UPGRADE_DECISION, UPGRADE_INPUT, UPGRADE_ORDER):
            with self.subTest(sentence=sentence[:40]):
                self.assertIn(sentence, upgrade)
        self.assertIn("**architecture → linking → frontmatter → workflow → collab → browser** 순서로 빈 줄을 사이에 두고 "
                      "이어 붙인다", _folded(_read(INIT)))

    def test_generated_agents_md_stays_within_24000_bytes(self) -> None:
        for name in ("Demo Vault", "가" * 100):
            with self.subTest(vault_name=name[:10]):
                text = build_vault_agents_md(name)
                self.assertNotIn("{{", text)
                self.assertTrue(text.startswith(STUB_FIRST_NEW + "\n"))
                self.assertIn(STUB_ANTIGRAVITY_BULLET, _folded(text))
                self.assertNotIn("agentic-vault:rule engine=", text)
                headings = [text.index("\n# " + heading + "\n") for heading in (
                    "볼트 아키텍처 맵 (디렉토리별 접근 규칙)", "Hard Rules: 마크다운 & 위키링크",
                    "Hard Rules: YAML 프런트매터 (위반 시 메타데이터 계층 붕괴)", "핵심 워크플로우", "다중 에이전트 협업",
                    "브라우저 자동화 경계")]
                self.assertEqual(headings, sorted(headings))
                self.assertLessEqual(len(text.encode("utf-8")), AGENTS_MAX_BYTES)

    def test_generated_marker_still_marks_the_file_engine_owned(self) -> None:
        # /vault-upgrade regenerates on this comment; vault_proposals refuses edits to files that carry it.
        self.assertIn("agentic-vault:generated", self.lines[0])
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import vault_proposals
        self.assertIsNotNone(vault_proposals.ENGINE_RE.search(self.lines[0]))


class ReferenceTests(unittest.TestCase):
    """skills/agentic-vault/references/antigravity.md: the list of section 3.6, in Korean, at most 9,000 bytes."""

    def setUp(self) -> None:
        # A Windows checkout with core.autocrlf=true has CRLF; the committed bytes are LF.
        self.raw = REFERENCE.read_bytes().replace(b"\r\n", b"\n")
        self.text = self.raw.decode("utf-8")

    def section(self, heading: str) -> str:
        return _folded(_section(self.text, heading))

    def test_size_encoding_and_whitespace(self) -> None:
        self.assertLessEqual(len(self.raw), REFERENCE_MAX_BYTES)
        self.assertFalse(self.raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", self.raw)
        self.assertNotIn(b"\t", self.raw)
        self.assertTrue(self.raw.endswith(b"\n") and not self.raw.endswith(b"\n\n"))
        for number, line in enumerate(self.text.split("\n"), 1):
            with self.subTest(line=number):
                self.assertEqual(line, line.rstrip())

    def test_headings_in_order(self) -> None:
        self.assertTrue(self.text.startswith("# Antigravity CLI에서 agentic-vault 사용\n"))
        positions = []
        for heading in REFERENCE_HEADINGS:
            with self.subTest(heading=heading):
                self.assertEqual(self.text.count(heading + "\n"), 1)
                positions.append(self.text.index(heading + "\n"))
        self.assertEqual(positions, sorted(positions))

    def test_install_pins_a_tag_or_a_local_folder_and_skips_the_conversion(self) -> None:
        section = self.section("## 설치")
        for literal in ("`agy plugin install https://github.com/Technoetic/agentic-vault/tree/<태그>/antigravity`",
                        "`agy plugin install <저장소 체크아웃>/antigravity`", "v0.48.0부터",
                        "자동 변환 설치는 쓰지 않는다", "`agentic-vault-cmd-*`",
                        "`~/.gemini/config/plugins/agentic-vault`"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_skill_names_cover_every_command(self) -> None:
        section = self.section("## 스킬 이름")
        commands = sorted(path.stem for path in (ROOT / "commands").glob("*.md"))
        self.assertEqual(len(commands), 15)
        for name in ("agentic-vault", "webapp-principles", *commands):
            with self.subTest(skill=name):
                self.assertIn(f"`{name}`", section)
        for literal in ("17개", "`/vault-day`", "`/agentic-vault:vault-day`", "agy가 보여 주는 표기",
                        "`disable-model-invocation: true`"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_placeholders(self) -> None:
        section = self.section("## 자리표시자")
        for literal in ("`<PLUGIN_ROOT>`", "`<ARGUMENTS>`", "`00-meta/vault-config.json`", "`--vault <볼트 루트>`",
                        "`~/.gemini/config/plugins/agentic-vault`", "셸 코드가 아니다"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_tool_table(self) -> None:
        section = _section(self.text, "## 도구 대응")
        pairs = set()
        for line in section.split("\n"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not line.startswith("| `") or len(cells) != 2:
                continue
            for left in re.findall(r"`([^`]+)`", cells[0]):
                for right in re.findall(r"`([^`]+)`", cells[1]):
                    pairs.add((left, right))
        self.assertEqual(pairs, TOOL_PAIRS)
        self.assertIn("새 항목에서 `Antigravity`로 적는다", _folded(section))

    def test_hooks(self) -> None:
        section = self.section("## 훅")
        for literal in ("SessionStart", "`injectSteps`", "9,500자", "Stop", "`fullyIdle`", "압축 뒤 재주입은 없다",
                        "`sh` 래퍼·`py -3`·`python`", "agy가 그 오류를 기록", "`vault-session-start` 스킬",
                        "`vault-lint` 스킬", "`workspacePaths`"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_what_is_not_done(self) -> None:
        section = self.section("## 하지 않는 것")
        for literal in ("deny zone 가드", "교훈 단서 훅", "Aside 자동 기동 훅", "Claude Code 전용", "Jarvis",
                        "Claude CLI"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_data_goes_to_google(self) -> None:
        section = self.section("## 데이터")
        for literal in ("Google", "상호작용 데이터", "opt-out"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_unchecked_items(self) -> None:
        section = self.section("## 확인하지 않은 것")
        for literal in ("**실제 대화 세션:**", "**Windows 훅 셸:**", "**슬래시 표기와 인자:**", "**주입 크기 상한:**"):
            with self.subTest(literal=literal):
                self.assertIn(literal, section)

    def test_no_claude_variables_left_for_the_package(self) -> None:
        # The generated package fails its build when these remain under skills/.
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", self.text)
        self.assertNotIn("$ARGUMENTS", self.text)

    def test_links_are_relative_and_resolve(self) -> None:
        targets = LINK.findall(self.text)
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target):
                self.assertNotRegex(target, r"^[A-Za-z][A-Za-z0-9+.-]*:")
                self.assertNotIn("docs/antigravity.md", target)
                path = target.split("#", 1)[0]
                self.assertTrue((REFERENCE.parent / path).exists(), target)


if __name__ == "__main__":
    unittest.main()
