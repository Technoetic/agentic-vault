"""The generated Antigravity CLI package (`antigravity/`) and its generator.

`scripts/build_antigravity_package.py` turns the repository sources into a
separate plugin folder for Antigravity CLI: a native manifest, `hooks.json`,
the fifteen command documents as skills, the shared skills, copies of the
engine scripts, hooks and assets, and the documents those files link to. The
repository root stays the Claude Code/Codex plugin.

These tests build the package from a temporary copy of the repository, so the
checkout is never written. While `hooks/antigravity_hook.py` does not exist yet
in the checkout, the copy gets a stand-in file marked "P2 test fixture"; the
generator itself refuses to build without that module. The sync test compares a
committed `antigravity/` folder with a fresh build once the folder exists.
"""
from __future__ import annotations

import importlib.util
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

try:
    from _git_env import git_environment
except ImportError:  # pragma: no cover - imported as tests.test_antigravity_package
    from tests._git_env import git_environment


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_antigravity_package.py"
CLAUDE_MANIFEST = ROOT / ".claude-plugin" / "plugin.json"
HOOK_MODULE = "hooks/antigravity_hook.py"
FIXTURE_MARKER = "P2 test fixture"
REPO_URL = "https://github.com/Technoetic/agentic-vault"
SCHEMA_URL = "https://antigravity.google/schemas/v1/plugin.json"
# Keys the Antigravity plugin manifest schema allows (additionalProperties is false).
SCHEMA_KEYS = {"$schema", "name", "displayName", "description", "version", "disabled", "logo",
               "suggestedPrompts", "skills", "author", "homepage", "repository", "license", "keywords",
               "extensions"}
MANIFEST_KEYS = ("$schema", "name", "displayName", "version", "description", "suggestedPrompts",
                 "author", "homepage", "repository", "license", "keywords")
COPIED_MANIFEST_KEYS = ("author", "homepage", "repository", "license")
HOOK_FILES = {"antigravity_hook.py", "run_python_hook.sh", "session_checkpoint.py", "session_start.py"}
TOP_LEVEL = {"plugin.json", "README.md", "hooks.json", "hooks", "skills", "assets", "docs"}
SHARED_SKILLS = ("agentic-vault", "webapp-principles")
MAX_BODY_BYTES = 23_500
MAX_DESCRIPTION_CHARS = 1_024
EXCLUDED_DOC_FOLDERS = ("releases", "verification", "superpowers", "plans", "screenshots")
SKIPPED_NAMES = {"__pycache__", ".git", "antigravity", ".worktrees", ".superpowers", ".venv", "venv"}

HEADER = (
    "> **Antigravity CLI용 생성 문서.** 원본은 저장소의 `commands/{name}.md`이고 "
    "`scripts/build_antigravity_package.py`가 만든다. 이 파일을 직접 고치지 않는다.\n"
    "> - `<PLUGIN_ROOT>`: 이 플러그인 설치 폴더의 절대 경로다. 이 SKILL.md가 있는 폴더의 두 단계 위 폴더다"
    "(보통 `~/.gemini/config/plugins/agentic-vault`). 셸 명령에는 펼친 절대 경로를 따옴표로 감싸 넣는다.\n"
    "> - `<ARGUMENTS>`: 사용자가 슬래시 명령 뒤에 적은 텍스트다. 없으면 이번 요청에서 찾는다.\n"
    "> - 볼트 루트는 작업 공간에서 `00-meta/vault-config.json`이 있는 폴더다. 볼트를 다루는 엔진 스크립트에 "
    "볼트를 넘길 때는 `--vault <볼트 루트>`를 쓴다. 본문 명령의 인자는 적힌 대로 쓴다.\n"
    "> - 본문의 `/vault-*` 표기는 이 플러그인의 같은 이름 스킬을 뜻한다. Claude Code 전용 단계"
    "(`.claude/settings.json`·`.claude/settings.local.json` 병합, PreToolUse 훅 설치)는 Antigravity 세션에서 "
    "실행하지 않는다. `.claude/rules/`와 AGENTS.md는 본문대로 만든다."
)
SKILL_REPLACEMENT = (
    "> 선택형 도구 안내(교훈 검증·묶음 복구·큰 볼트 운영·변경 영향·기억 형성·기억 철회·기록 보존·기억 신뢰 경계)는 "
    "[references/optional-helpers.md](references/optional-helpers.md)로 옮겼다(Antigravity 스킬 본문 한도). "
    "해당 도구를 쓸 때 그 파일을 읽는다."
)
INIT_REPLACEMENT = (
    "> 4-2(deny zone 가드)와 4-3(교훈 단서 훅)은 Claude Code 전용 선택 설치라 "
    "[references/claude-code-hooks.md](references/claude-code-hooks.md)로 옮겼다. Antigravity 세션은 이 훅을 "
    "쓰지 않는다. 같은 볼트를 Claude Code에서도 쓰고 사용자가 따로 승인했을 때만 그 파일의 절차를 따른다."
)
UPGRADE_REPLACEMENT = (
    "> 보강 helper bundle과 선택 이행 절차, 그리고 그 뒤에 이어지던 `0.9.0` 안내 목록 4개(Codex 진입점·세션 주입 "
    "예산·`/vault-recall` 파일 셋·백업 스냅샷)는 [references/helper-bundle.md](references/helper-bundle.md)로 "
    "옮겼다(Antigravity 스킬 본문 한도). 그 파일을 읽고 따른 뒤 아래 두 줄로 검증한다."
)
# The replacement when only the helper bundle paragraphs move (the v0.48.0 draft): the 0.9.0 list stays in
# the skill body whenever the body then fits the limit.
UPGRADE_HELPER_ONLY_REPLACEMENT = (
    "> 보강 helper bundle과 선택 이행 절차는 [references/helper-bundle.md](references/helper-bundle.md)로 "
    "옮겼다(Antigravity 스킬 본문 한도). 해당 단계에서 그 파일을 읽는다."
)
# commands/vault-upgrade.md, step 2: the first line of the 0.9.0 list and the first verification line,
# which ends the moved section.
UPGRADE_LIST_START = "- `0.9.0`은 Codex에서도 같은 엔진을 사용한다."
UPGRADE_VERIFY_START = "- 볼트가 git 저장소이고 훅을 설치했으면"
UPGRADE_HEALTHCHECK = 'vault_healthcheck.py" --vault .'
# (source, heading predicate, package file the sections move to, replacement line)
SPLITS = (
    ("skills/agentic-vault/SKILL.md", lambda line: line.startswith("### 선택형"),
     "skills/agentic-vault/references/optional-helpers.md", SKILL_REPLACEMENT),
    ("commands/vault-init.md", lambda line: line.startswith(("## 4-2.", "## 4-3.")),
     "skills/vault-init/references/claude-code-hooks.md", INIT_REPLACEMENT),
    ("commands/vault-upgrade.md", lambda line: line == "### 보강 helper bundle과 선택 이행",
     "skills/vault-upgrade/references/helper-bundle.md", UPGRADE_REPLACEMENT),
)
GITATTRIBUTES_LINES = (
    "# Generated Antigravity package: LF in every text file (binary files untouched) so the committed copy "
    "equals the generator output on every OS.",
    "antigravity/** text=auto eol=lf",
)
# Copied as they are, never normalised: the generator's list of known binary file types (a NUL byte also
# marks a file as binary).
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf", ".zip")
# The copied trees whose git mode (100644 or 100755) the package copy keeps.
COPIED_PREFIXES = ("assets/", "skills/agentic-vault/scripts/", "hooks/")
README_UNCONFIRMED = ("실제 agy 대화 세션으로는 확인하지 않았다", "not yet confirmed in a real agy conversation")

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^(#{1,6})(?:[ \t]|$)")
# An inline link or image; one level of nested brackets in the text, balanced parentheses in the target.
LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")
LINK_TARGET = re.compile(r"\]\([^()\s]*(?:\([^()\s]*\)[^()\s]*)*(?:\s+\"[^\"]*\")?\)")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
PLUGIN_ROOT_PATH = re.compile(r"<PLUGIN_ROOT>/([A-Za-z0-9_./-]*)")


def _is_binary(data: bytes, relative: str) -> bool:
    return b"\0" in data or relative.lower().endswith(BINARY_SUFFIXES)


def _lf(data: bytes, relative: str) -> bytes:
    return data if _is_binary(data, relative) else data.replace(b"\r\n", b"\n")


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _ignore_root_extras(root: Path):
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name in {"__pycache__"} or name.endswith((".pyc", ".pyo"))}
        if Path(directory).resolve() == root.resolve():
            skipped |= {name for name in names if name in SKIPPED_NAMES}
        return skipped
    return ignore


def _copy_repo(dest: Path, *, minimal: bool = False) -> Path:
    """Copy the checkout (no .git, no generated package) and supply the hook module stand-in if needed."""
    if minimal:
        dest.mkdir(parents=True)
        for name in (".claude-plugin", "commands", "skills", "assets", "hooks"):
            shutil.copytree(ROOT / name, dest / name, ignore=_ignore_root_extras(ROOT / name))
        docs = dest / "docs"
        (docs / "security").mkdir(parents=True)
        for path in (ROOT / "docs").glob("*.md"):
            shutil.copy2(path, docs / path.name)
        for path in (ROOT / "docs" / "security").glob("*.md"):
            shutil.copy2(path, docs / "security" / path.name)
    else:
        shutil.copytree(ROOT, dest, ignore=_ignore_root_extras(ROOT))
    hook = dest / HOOK_MODULE
    if not hook.exists():
        hook.write_bytes(
            f'"""{FIXTURE_MARKER}: stands in for {HOOK_MODULE} until that module exists."""\n'.encode("utf-8"))
    return dest


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ if env is None else env, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=600)


def _git_modes(repo: Path, environment: dict) -> dict[str, str] | None:
    """Path -> mode (`100644`, `100755`) of the git index of repo; None unless repo is the top of a work tree."""
    def git(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=environment,
                                  stdin=subprocess.DEVNULL, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None

    top = git("rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or \
            Path(top.stdout.decode("utf-8", errors="replace").strip()).resolve() != repo.resolve():
        return None
    result = git("ls-files", "-s", "-z")
    if result is None or result.returncode != 0:
        return None
    modes = {}
    for record in result.stdout.split(b"\0"):
        if record:
            info, _, path = record.partition(b"\t")
            modes[path.decode("utf-8")] = info.split(b" ", 1)[0].decode("ascii")
    return modes


def _tree(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for directory, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(directory) / name
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _frontmatter(text: str) -> tuple[list[tuple[str, str]], str]:
    """Return the `key: value` pairs of a leading frontmatter block and the text after it."""
    lines = text.split("\n")
    if lines[0] != "---":
        raise AssertionError("no frontmatter")
    end = lines.index("---", 1)
    pairs = []
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep:
            raise AssertionError(f"frontmatter line without a key: {line!r}")
        pairs.append((key.strip(), value.strip()))
    return pairs, "\n".join(lines[end + 1:])


def _scalar(value: str) -> str:
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _body_bytes(text: str) -> int:
    return len(_frontmatter(text)[1].strip("\n").encode("utf-8"))


def _without_code_spans(line: str) -> str:
    out, index = [], 0
    while index < len(line):
        if line[index] != "`":
            out.append(line[index])
            index += 1
            continue
        run_end = index
        while run_end < len(line) and line[run_end] == "`":
            run_end += 1
        width, scan, close = run_end - index, run_end, -1
        while scan < len(line):
            if line[scan] != "`":
                scan += 1
                continue
            other = scan
            while other < len(line) and line[other] == "`":
                other += 1
            if other - scan == width:
                close = other
                break
            scan = other
        if close < 0:
            out.append(line[index:run_end])
            index = run_end
        else:
            out.append(" " * (close - index))
            index = close
    return "".join(out)


def _prose_lines(text: str):
    """Yield the lines outside fenced code blocks with inline code blanked out."""
    fence = None
    for line in text.split("\n"):
        match = FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                continue
            yield _without_code_spans(line)
        elif match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                and line.strip() == match.group(1):
            fence = None


def _link_targets(text: str) -> list[str]:
    return [match.group(1) for line in _prose_lines(text) for match in LINK.finditer(line)]


def _is_relative(target: str) -> bool:
    return bool(target) and not target.startswith(("#", "/", "\\")) and not SCHEME.match(target)


def _headings(text: str) -> list[str]:
    fence, found = None, []
    for line in text.split("\n"):
        match = FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
            elif HEADING.match(line):
                found.append(line)
        elif match and match.group(1)[0] == fence[0] and line.strip() == match.group(1):
            fence = None
    return found


def _masked(text: str) -> str:
    return LINK_TARGET.sub("]()", text)


def _placeholders(text: str) -> str:
    return text.replace("${CLAUDE_PLUGIN_ROOT}", "<PLUGIN_ROOT>").replace("$ARGUMENTS", "<ARGUMENTS>")


def _content_lines(text: str) -> Counter:
    return Counter(line for line in _masked(text).split("\n") if line.strip())


def _claude_manifest() -> dict:
    return json.loads(CLAUDE_MANIFEST.read_text(encoding="utf-8-sig"))


def _commands() -> list[str]:
    return sorted(path.stem for path in (ROOT / "commands").glob("*.md"))


class PackageBuildTests(unittest.TestCase):
    """One full build from a copy of the checkout, built twice into different folders."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="av-agy-package-"))
        cls.repo = _copy_repo(cls.temp / "repo")
        cls.out = cls.temp / "first" / "antigravity"
        cls.second = cls.temp / "second" / "antigravity"
        cls.runs = [_run("build", "--repo", str(cls.repo), "--out", str(out)) for out in (cls.out, cls.second)]
        cls.files = _tree(cls.out) if cls.out.is_dir() else {}
        cls.version = _claude_manifest()["version"]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def setUp(self) -> None:
        for run in self.runs:
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def text(self, relative: str) -> str:
        self.assertIn(relative, self.files)
        return self.files[relative].decode("utf-8")

    def markdown(self) -> dict[str, str]:
        return {path: data.decode("utf-8") for path, data in self.files.items() if path.endswith(".md")}

    def test_two_builds_are_identical(self) -> None:
        second = _tree(self.second)
        self.assertEqual(sorted(second), sorted(self.files))
        for relative, data in self.files.items():
            with self.subTest(path=relative):
                self.assertEqual(second[relative], data)

    def test_module_loads_unregistered_and_matches_the_folder(self) -> None:
        # Tests here load scripts through importlib without registering them in sys.modules.
        spec = importlib.util.spec_from_file_location("antigravity_package_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        package = module.build_package(self.repo)
        self.assertEqual(package.version, self.version)
        self.assertEqual(package.files, self.files)
        self.assertEqual(sorted(package.skill_bodies), sorted(path for path in self.files
                                                              if re.fullmatch(r"skills/[^/]+/SKILL\.md", path)))

    def test_layout_is_exact(self) -> None:
        self.assertEqual({path.split("/", 1)[0] for path in self.files}, TOP_LEVEL)
        self.assertEqual({path.split("/", 1)[1] for path in self.files if path.startswith("hooks/")}, HOOK_FILES)
        expected_skills = set(SHARED_SKILLS) | set(_commands())
        self.assertEqual(len(expected_skills), 17)
        self.assertEqual({path.split("/")[1] for path in self.files if path.startswith("skills/")}, expected_skills)
        for name in expected_skills:
            with self.subTest(skill=name):
                self.assertIn(f"skills/{name}/SKILL.md", self.files)
        references = {Path(path).name for path in self.files
                      if path.startswith("skills/agentic-vault/references/")}
        self.assertEqual(references, {path.name for path in (ROOT / "skills/agentic-vault/references").glob("*.md")}
                         | {"optional-helpers.md"})
        extra = {path for path in self.files if path.startswith("skills/") and not path.endswith("/SKILL.md")
                 and not path.startswith(("skills/agentic-vault/references/", "skills/agentic-vault/scripts/"))}
        self.assertEqual(extra, {"skills/vault-init/references/claude-code-hooks.md",
                                 "skills/vault-upgrade/references/helper-bundle.md"})
        for path in self.files:
            with self.subTest(path=path):
                self.assertNotIn("__pycache__", path)
                self.assertFalse(path.endswith((".pyc", ".pyo")))
                if path.startswith("docs/"):
                    self.assertRegex(path, r"^docs/(?:security/)?[^/]+\.md$")
                    self.assertNotIn(path.split("/")[1], EXCLUDED_DOC_FOLDERS)

    def test_copies_are_byte_equal_to_the_repository(self) -> None:
        expected = {}
        for top in ("assets", "skills/agentic-vault/scripts"):
            for relative in _tree(ROOT / top):
                if "__pycache__" in relative.split("/") or relative.endswith((".pyc", ".pyo")):
                    continue
                expected[f"{top}/{relative}"] = _lf((ROOT / top / relative).read_bytes(), relative)
        for name in HOOK_FILES:
            source = ROOT / "hooks" / name
            if not source.exists():
                source = self.repo / "hooks" / name
            expected[f"hooks/{name}"] = _lf(source.read_bytes(), name)
        copied = {path: data for path, data in self.files.items()
                  if path.startswith(("assets/", "skills/agentic-vault/scripts/", "hooks/"))}
        self.assertEqual(sorted(copied), sorted(expected))
        for relative, data in expected.items():
            with self.subTest(path=relative):
                self.assertEqual(copied[relative], data)

    def test_text_files_use_lf_and_utf8(self) -> None:
        for relative, data in self.files.items():
            if _is_binary(data, relative):
                continue
            with self.subTest(path=relative):
                self.assertNotIn(b"\r\n", data)
                data.decode("utf-8")

    def test_manifest_keys_and_values(self) -> None:
        text = self.text("plugin.json")
        manifest = json.loads(text)
        claude = _claude_manifest()
        self.assertEqual(tuple(manifest), MANIFEST_KEYS)
        self.assertLessEqual(set(manifest), SCHEMA_KEYS)
        self.assertEqual(text, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        self.assertEqual(manifest["$schema"], SCHEMA_URL)
        self.assertEqual(manifest["name"], "agentic-vault")
        self.assertEqual(manifest["displayName"], "Agentic Vault")
        self.assertEqual(manifest["version"], claude["version"])
        description = manifest["description"]
        self.assertTrue(0 < len(description) <= 160, len(description))
        self.assertTrue(description.isascii())
        self.assertNotRegex(description, r"(?i)claude|codex|antigravity")
        prompts = manifest["suggestedPrompts"]
        self.assertEqual(len(prompts), 3)
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertTrue(prompt.isascii() and 0 < len(prompt) <= 60, prompt)
        joined = " ".join(prompts).lower()
        for word in ("session", "search", "integrity"):
            self.assertIn(word, joined)
        for key in COPIED_MANIFEST_KEYS:
            with self.subTest(key=key):
                self.assertEqual(manifest[key], claude[key])
        self.assertEqual(manifest["keywords"], [keyword.lower() for keyword in claude["keywords"]])

    def test_hooks_json_is_exact(self) -> None:
        def handlers(script: str, timeout: int) -> list[dict]:
            return [{"type": "command", "command": f"{runner} ./hooks/{script} --host antigravity", "timeout": timeout}
                    for runner in ("sh ./hooks/run_python_hook.sh", "py -3", "python")]

        expected = {"agentic-vault": {"SessionStart": handlers("session_start.py", 30),
                                      "Stop": handlers("session_checkpoint.py", 15)}}
        text = self.text("hooks.json")
        self.assertEqual(json.loads(text), expected)
        self.assertEqual(text, json.dumps(expected, indent=2, ensure_ascii=False) + "\n")
        for group in json.loads(text).values():
            for event in group.values():
                for handler in event:
                    self.assertEqual(list(handler), ["type", "command", "timeout"])

    def test_readme_states_install_generation_and_docs(self) -> None:
        readme = self.text("README.md")
        install = f"agy plugin install {REPO_URL}/tree/v{self.version}/antigravity"
        self.assertEqual(readme.count(install), 2)
        self.assertIn("scripts/build_antigravity_package.py", readme)
        self.assertIn(f"]({REPO_URL}/blob/v{self.version}/docs/antigravity.md)", readme)
        self.assertLess(readme.index("생성물"), readme.index("generated"))
        self.assertLess(len(readme.encode("utf-8")), 4000)
        # The hook effects are stated as built, not as observed: no real agy conversation ran, and the Stop
        # checkpoint is written only when the agent is fully idle.
        korean, english = readme.split("\n## English\n", 1)
        self.assertIn(README_UNCONFIRMED[0], korean)
        self.assertIn(README_UNCONFIRMED[1], english)
        for part in (korean, english):
            with self.subTest(part=part[:20]):
                self.assertIn("`fullyIdle`", part)
        self.assertNotIn("에이전트가 멈출 때 checkpoint를 남긴다", korean)
        self.assertNotIn("write a checkpoint when the agent stops", english)

    def test_skill_frontmatter_names_descriptions_and_bodies(self) -> None:
        for name in sorted(SHARED_SKILLS + tuple(_commands())):
            with self.subTest(skill=name):
                text = self.text(f"skills/{name}/SKILL.md")
                pairs, body = _frontmatter(text)
                values = dict(pairs)
                self.assertEqual(values["name"], name)
                description = _scalar(values["description"])
                self.assertTrue(1 <= len(description) <= MAX_DESCRIPTION_CHARS)
                self.assertLessEqual(_body_bytes(text), MAX_BODY_BYTES)
                if name in SHARED_SKILLS:
                    source = _text(ROOT / "skills" / name / "SKILL.md")
                    self.assertEqual(_frontmatter(source)[0], pairs)
                else:
                    self.assertEqual([key for key, _ in pairs], ["name", "description", "disable-model-invocation"])
                    self.assertEqual(values["disable-model-invocation"], "true")
                    self.assertTrue(values["description"].startswith('"'))

    def test_command_descriptions_carry_the_argument_hint(self) -> None:
        for name in _commands():
            with self.subTest(command=name):
                source = dict(_frontmatter(_text(ROOT / "commands" / f"{name}.md"))[0])
                expected = _scalar(source["description"])
                if "argument-hint" in source:
                    expected += " 입력: " + _scalar(source["argument-hint"])
                generated = dict(_frontmatter(self.text(f"skills/{name}/SKILL.md"))[0])
                self.assertEqual(json.loads(generated["description"]), expected)
        self.assertIn("argument-hint", dict(_frontmatter(_text(ROOT / "commands" / "vault-day.md"))[0]))

    def test_command_skill_header_and_body(self) -> None:
        split_sources = {source for source, *_ in SPLITS}
        for name in _commands():
            with self.subTest(command=name):
                body = _frontmatter(self.text(f"skills/{name}/SKILL.md"))[1].strip("\n")
                header = HEADER.format(name=name)
                self.assertTrue(body.startswith(header + "\n\n"), body[:200])
                rest = body[len(header) + 2:]
                original = _placeholders(_frontmatter(_text(ROOT / "commands" / f"{name}.md"))[1]).strip("\n")
                self.assertEqual(rest.split("\n", 1)[0], original.split("\n", 1)[0])
                if f"commands/{name}.md" not in split_sources:
                    # Only link targets differ from the source body.
                    self.assertEqual(_masked(rest), _masked(original))

    def test_moved_sections(self) -> None:
        for source, is_moved, target, replacement in SPLITS:
            with self.subTest(source=source):
                original = _frontmatter(_text(ROOT / source))[1]
                moved_headings = [line for line in _headings(original) if is_moved(line)]
                self.assertTrue(moved_headings)
                package_source = (source if source.startswith("skills/")
                                  else f"skills/{Path(source).stem}/SKILL.md")
                kept = _frontmatter(self.text(package_source))[1]
                self.assertEqual(kept.split("\n").count(replacement), 1)
                self.assertEqual(_headings(kept), [line for line in _headings(original) if not is_moved(line)])
                moved = self.text(target)
                lines = moved.split("\n")
                self.assertTrue(lines[0].startswith("# "), lines[0])
                self.assertEqual(lines[1], "")
                self.assertTrue(lines[2].startswith("> "), lines[2])
                self.assertEqual(lines[3], "")
                self.assertEqual(_headings(moved)[1:], moved_headings)
                self.assertTrue(moved.endswith("\n") and not moved.endswith("\n\n"))
                # Nothing is lost or reworded: the kept and moved lines are the source lines (link targets aside).
                restored = _content_lines(kept) + _content_lines("\n".join(lines[4:]))
                self.assertEqual(restored[_masked(replacement)], 1)
                restored[_masked(replacement)] -= 1
                if not source.startswith("skills/"):
                    restored -= _content_lines(HEADER.format(name=Path(source).stem))
                restored += Counter()
                self.assertEqual(restored, _content_lines(_placeholders(original)))
        helpers = self.text("skills/agentic-vault/references/optional-helpers.md")
        self.assertTrue(helpers.startswith("# 선택형 도구 안내\n\n> "))

    def _upgrade_source_lines(self) -> list[str]:
        return _placeholders(_frontmatter(_text(ROOT / "commands" / "vault-upgrade.md"))[1]).split("\n")

    def test_vault_upgrade_keeps_its_verification_lines(self) -> None:
        # The helper bundle section of commands/vault-upgrade.md has no heading after it before `## 3. 보고`,
        # so the move ends at the step 2 verification lines; they stay in the skill body.
        source = self._upgrade_source_lines()
        start = next(index for index, line in enumerate(source) if line.startswith(UPGRADE_VERIFY_START))
        verification = source[start:source.index("## 3. 보고")]
        verification = [line for line in verification if line.strip()]
        self.assertEqual(len(verification), 2, verification)
        self.assertIn(UPGRADE_HEALTHCHECK, verification[1])
        lines = _frontmatter(self.text("skills/vault-upgrade/SKILL.md"))[1].split("\n")
        step = lines[lines.index("## 2. 검증"):lines.index("## 3. 보고")]
        self.assertEqual([line for line in step if line.strip()],
                         ["## 2. 검증", UPGRADE_REPLACEMENT, *verification])
        self.assertEqual(sum(UPGRADE_HEALTHCHECK in line for line in lines), 1)
        reference = self.text("skills/vault-upgrade/references/helper-bundle.md")
        self.assertNotIn(UPGRADE_HEALTHCHECK, reference)
        self.assertNotIn(UPGRADE_VERIFY_START, reference)

    def test_vault_upgrade_moves_the_090_list_only_because_the_body_would_not_fit(self) -> None:
        # The 0.9.0 list stays in the skill body when the body then fits 23,500 bytes; it fits not, so it
        # moves with the helper bundle paragraphs and the replacement line names it.
        source = self._upgrade_source_lines()
        first = source.index(next(line for line in source if line.startswith(UPGRADE_LIST_START)))
        stop = next(index for index, line in enumerate(source) if line.startswith(UPGRADE_VERIFY_START))
        listed = [line for line in source[first:stop] if line.strip()]
        self.assertEqual(len(listed), 4, listed)
        self.assertTrue(all(line.startswith("- ") for line in listed), listed)
        body = _frontmatter(self.text("skills/vault-upgrade/SKILL.md"))[1]
        reference = self.text("skills/vault-upgrade/references/helper-bundle.md").split("\n")
        for line in listed:
            with self.subTest(line=line[:40]):
                self.assertNotIn(_masked(line), _masked(body).split("\n"))
                self.assertIn(_masked(line), [_masked(kept) for kept in reference])
        self.assertIn("`0.9.0` 안내 목록 4개", UPGRADE_REPLACEMENT)
        kept = body.replace(UPGRADE_REPLACEMENT, UPGRADE_HELPER_ONLY_REPLACEMENT + "\n\n" + "\n".join(listed), 1)
        self.assertGreater(len(kept.strip("\n").encode("utf-8")), MAX_BODY_BYTES,
                           "the 0.9.0 list fits the vault-upgrade skill body again: keep it there (end the move "
                           "before the list) and restore the helper-bundle-only replacement")

    def test_references_and_docs_change_only_link_targets(self) -> None:
        for path, text in self.markdown().items():
            if path.startswith("docs/"):
                source = ROOT / path
            elif path.startswith("skills/agentic-vault/references/") and path != \
                    "skills/agentic-vault/references/optional-helpers.md":
                source = ROOT / path
            else:
                continue
            with self.subTest(path=path):
                expected = source.read_bytes().decode("utf-8").replace("\r\n", "\n")
                if path.startswith("skills/"):
                    expected = _placeholders(expected)
                self.assertEqual(_masked(text), _masked(expected))

    def test_no_claude_placeholders_under_skills(self) -> None:
        for path, text in self.markdown().items():
            if path.startswith("skills/"):
                with self.subTest(path=path):
                    self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text)
                    self.assertNotIn("$ARGUMENTS", text)

    def test_relative_links_resolve_inside_the_package(self) -> None:
        package = self.out.resolve()
        checked = 0
        for path, text in self.markdown().items():
            for target in _link_targets(text):
                if not _is_relative(target):
                    continue
                checked += 1
                with self.subTest(path=path, target=target):
                    resolved = (self.out / posixpath.dirname(path) / unquote(target.split("#", 1)[0])).resolve()
                    self.assertTrue(resolved.exists(), target)
                    resolved.relative_to(package)
        self.assertGreater(checked, 100)

    def test_rewritten_github_links_name_repository_paths(self) -> None:
        prefix = re.compile(re.escape(REPO_URL) + r"/(blob|tree)/v" + re.escape(self.version) + r"/([^#]+)")
        checked = 0
        for path, text in self.markdown().items():
            if path == "README.md":
                # Generated text, not rewritten links; its fixed URLs are pinned by the README test
                # (docs/antigravity.md is written with the release).
                continue
            for target in _link_targets(text):
                match = prefix.fullmatch(target.split("#", 1)[0])
                if not match:
                    continue
                checked += 1
                with self.subTest(path=path, target=target):
                    repository_path = ROOT / unquote(match.group(2))
                    if match.group(1) == "blob":
                        self.assertTrue(repository_path.is_file(), target)
                    else:
                        self.assertTrue(repository_path.is_dir(), target)
        self.assertGreater(checked, 50)

    def test_docs_are_the_link_closure(self) -> None:
        docs = {path for path in self.files if path.startswith("docs/")}
        self.assertTrue(docs)
        linked = set()
        for path, text in self.markdown().items():
            for target in _link_targets(text):
                if _is_relative(target):
                    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path),
                                                               unquote(target.split("#", 1)[0])))
                    if joined != path:
                        linked.add(joined)
        self.assertLessEqual(docs, linked)
        for path, text in self.markdown().items():
            for target in _link_targets(text):
                if _is_relative(target):
                    joined = posixpath.normpath(posixpath.join(posixpath.dirname(path),
                                                               unquote(target.split("#", 1)[0])))
                    for folder in EXCLUDED_DOC_FOLDERS:
                        self.assertFalse(joined.startswith(f"docs/{folder}"), (path, target))

    def test_plugin_root_paths_exist_in_the_package(self) -> None:
        checked = 0
        for path, text in self.markdown().items():
            if not path.startswith("skills/"):
                continue
            for relative in PLUGIN_ROOT_PATH.findall(text):
                relative = relative.rstrip(".,")
                if not relative:
                    continue
                checked += 1
                with self.subTest(path=path, target=relative):
                    self.assertTrue((self.out / relative).exists(), relative)
        self.assertGreater(checked, 20)


class GeneratorRefusalTests(unittest.TestCase):
    """Builds that must fail, and the folder rules of build and check, on a reduced copy."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="av-agy-refusal-"))
        cls.base = _copy_repo(cls.temp / "base", minimal=True)
        cls.count = 0

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def variant(self) -> Path:
        type(self).count += 1
        repo = self.temp / f"variant{self.count}"
        shutil.copytree(self.base, repo)
        return repo

    def assertRefused(self, result: subprocess.CompletedProcess, *fragments: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        for fragment in fragments:
            self.assertIn(fragment, result.stderr)

    def test_missing_hook_module_fails_clearly(self) -> None:
        repo = self.variant()
        (repo / HOOK_MODULE).unlink()
        out = repo.parent / f"{repo.name}-out"
        self.assertRefused(_run("build", "--repo", str(repo), "--out", str(out)), HOOK_MODULE)
        self.assertFalse(out.exists())
        self.assertRefused(_run("check", "--repo", str(repo), "--against", str(out)), HOOK_MODULE)

    def test_oversized_skill_body_fails(self) -> None:
        repo = self.variant()
        command = repo / "commands" / "vault-day.md"
        command.write_bytes(command.read_bytes() + ("\n가나다라마바사아자차" * 1000).encode("utf-8") + b"\n")
        out = repo.parent / f"{repo.name}-out"
        self.assertRefused(_run("build", "--repo", str(repo), "--out", str(out)),
                           "skills/vault-day/SKILL.md", "23500")
        self.assertFalse(out.exists())

    def test_overlong_description_fails(self) -> None:
        repo = self.variant()
        command = repo / "commands" / "vault-day.md"
        text = _text(command)
        long = "기록 " * 400
        command.write_bytes(re.sub(r"(?m)^description: .*$", "description: " + long.strip(), text, count=1)
                            .encode("utf-8"))
        out = repo.parent / f"{repo.name}-out"
        self.assertRefused(_run("build", "--repo", str(repo), "--out", str(out)),
                           "skills/vault-day/SKILL.md", "description", "1024")
        self.assertFalse(out.exists())

    def test_leftover_claude_placeholder_fails(self) -> None:
        repo = self.variant()
        (repo / "skills/agentic-vault/scripts/resources/notes.md").write_bytes(
            b"Run ${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py\n")
        out = repo.parent / f"{repo.name}-out"
        self.assertRefused(_run("build", "--repo", str(repo), "--out", str(out)),
                           "skills/agentic-vault/scripts/resources/notes.md", "${CLAUDE_PLUGIN_ROOT}")
        self.assertFalse(out.exists())

    def test_missing_section_to_move_fails(self) -> None:
        repo = self.variant()
        command = repo / "commands" / "vault-upgrade.md"
        command.write_bytes(_text(command).replace("### 보강 helper bundle과 선택 이행", "### 보강 helper bundle")
                            .encode("utf-8"))
        out = repo.parent / f"{repo.name}-out"
        self.assertRefused(_run("build", "--repo", str(repo), "--out", str(out)), "commands/vault-upgrade.md")
        self.assertFalse(out.exists())

    def test_missing_end_of_a_moved_section_fails(self) -> None:
        # Without its end line the vault-upgrade move would take the step 2 verification lines along.
        repo = self.variant()
        command = repo / "commands" / "vault-upgrade.md"
        text = _text(command)
        self.assertEqual(text.count("\n" + UPGRADE_VERIFY_START), 1)
        command.write_bytes(text.replace("\n" + UPGRADE_VERIFY_START, "\n- git 볼트에 훅을 설치했으면").encode("utf-8"))
        out = repo.parent / f"{repo.name}-out"
        self.assertRefused(_run("build", "--repo", str(repo), "--out", str(out)), "commands/vault-upgrade.md",
                           UPGRADE_VERIFY_START.split(" 훅을")[0])
        self.assertFalse(out.exists())

    def test_binary_assets_keep_their_bytes(self) -> None:
        repo = self.variant()
        samples = {"assets/logo.png": b"\x89PNG\r\n\x1a\nIHDR\r\nno NUL byte\r\n",
                   "assets/photo.JPG": b"\xff\xd8\xff\xe0\r\nJFIF\r\n"}
        for relative, data in samples.items():
            (repo / relative).write_bytes(data)
        (repo / "assets" / "plain.txt").write_bytes(b"one\r\ntwo\r\n")
        out = repo.parent / f"{repo.name}-out"
        built = _run("build", "--repo", str(repo), "--out", str(out))
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        for relative, data in samples.items():
            with self.subTest(path=relative):
                self.assertEqual((out / relative).read_bytes(), data)
        self.assertEqual((out / "assets" / "plain.txt").read_bytes(), b"one\ntwo\n")
        same = _run("check", "--repo", str(repo), "--against", str(out))
        self.assertEqual(same.returncode, 0, same.stdout + same.stderr)

    def test_check_compares_git_modes_inside_a_repository(self) -> None:
        # The generator writes bytes only; an executable source (the git hooks) keeps its mode in the package
        # only through the git index, so check compares the index modes when the package is in the repository.
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        repo = self.variant()
        # The ceiling keeps git from finding a repository above the temporary folder.
        environment = dict(git_environment(), GIT_CEILING_DIRECTORIES=str(self.temp))

        def git(*args: str) -> None:
            result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", env=environment,
                                    stdin=subprocess.DEVNULL, timeout=300)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        def check(*args: str) -> tuple[int, list[str]]:
            result = _run("check", "--repo", str(repo), *args, env=environment)
            self.assertNotIn("Traceback", result.stderr)
            return result.returncode, result.stdout.splitlines()

        built = _run("build", "--repo", str(repo), env=environment)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        # Outside a git work tree (a release ZIP, for one) only the bytes are compared, and check says so.
        code, lines = check()
        self.assertEqual(code, 0, lines)
        self.assertTrue(lines[-1].startswith("git modes: not compared (git finds no work tree at "), lines)
        git("init", "-q")
        # Files the index does not hold yet (a build not added) are not compared.
        code, lines = check()
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], "git modes: not compared (no copied file is in the git index together with "
                                    "its source)")
        git("add", "-A")
        hooks = ("assets/git-hooks/pre-commit", "assets/git-hooks/pre-push")
        copies = tuple(f"antigravity/{path}" for path in hooks)
        git("update-index", "--chmod=+x", *hooks)
        git("update-index", "--chmod=-x", *copies)
        code, lines = check()
        self.assertEqual(code, 1, lines)
        for path, copy in zip(hooks, copies):
            with self.subTest(path=path):
                found = [line for line in lines if line.startswith(f"mode differs: {path} ")]
                self.assertEqual(len(found), 1, lines)
                self.assertIn("100644", found[0])
                self.assertIn("100755", found[0])
                self.assertIn(f"git update-index --chmod=+x {copy}", found[0])
        self.assertEqual(len([line for line in lines if line.startswith("mode differs:")]), 2, lines)
        self.assertFalse([line for line in lines if line.startswith(("differs:", "only in", "missing from"))])
        self.assertTrue(lines[-1].startswith("git modes: compared "), lines)
        self.assertTrue(lines[-1].endswith("; run the git update-index commands above there"), lines)
        git("update-index", "--chmod=+x", *copies)
        code, lines = check()
        self.assertEqual(code, 0, lines)
        self.assertRegex(lines[-1], r"^git modes: compared [1-9][0-9]* copied files with their sources in the git "
                                    r"index of ")
        self.assertNotIn("update-index", lines[-1])
        # An executable copy of a plain source is a difference too, fixed with --chmod=-x.
        script = "skills/agentic-vault/scripts/vault_paths.py"
        git("update-index", "--chmod=+x", f"antigravity/{script}")
        code, lines = check()
        self.assertEqual(code, 1, lines)
        self.assertEqual([line for line in lines if line.startswith("mode differs:")],
                         [f"mode differs: {script} (package copy 100755, source 100644): "
                          f"git update-index --chmod=-x antigravity/{script}"])
        git("update-index", "--chmod=-x", f"antigravity/{script}")
        # A package folder outside the repository is compared by bytes only.
        outside = self.temp / f"{repo.name}-outside" / "antigravity"
        elsewhere = _run("build", "--repo", str(repo), "--out", str(outside), env=environment)
        self.assertEqual(elsewhere.returncode, 0, elsewhere.stdout + elsewhere.stderr)
        code, lines = check("--against", str(outside))
        self.assertEqual(code, 0, lines)
        self.assertTrue(lines[-1].startswith("git modes: not compared ("), lines)
        self.assertIn("is outside the git work tree", lines[-1])

    def test_build_replaces_only_a_previous_package(self) -> None:
        repo = self.variant()
        out = repo / "antigravity"
        first = _run("build", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue((out / "plugin.json").is_file())
        (out / "stale.txt").write_bytes(b"left over\n")
        again = _run("build", "--repo", str(repo))
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertFalse((out / "stale.txt").exists())
        foreign = [
            ("no-manifest", None),
            ("other-name", {"$schema": SCHEMA_URL, "name": "something-else"}),
            # The Claude Code manifest carries the same name but is not a generated package.
            ("claude-manifest", {"name": "agentic-vault", "version": "0.0.0"}),
        ]
        for label, manifest in foreign:
            with self.subTest(folder=label):
                folder = repo.parent / f"{repo.name}-{label}"
                folder.mkdir()
                (folder / "keep.txt").write_bytes(b"keep\n")
                if manifest is not None:
                    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
                self.assertRefused(_run("build", "--repo", str(repo), "--out", str(folder)), str(folder.name))
                self.assertEqual((folder / "keep.txt").read_bytes(), b"keep\n")
        afile = repo.parent / f"{repo.name}-file"
        afile.write_bytes(b"not a folder\n")
        self.assertRefused(_run("build", "--repo", str(repo), "--out", str(afile)))
        self.assertEqual(afile.read_bytes(), b"not a folder\n")

    def test_check_reports_each_difference(self) -> None:
        repo = self.variant()
        built = _run("build", "--repo", str(repo))
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        out = repo / "antigravity"
        same = _run("check", "--repo", str(repo))
        self.assertEqual(same.returncode, 0, same.stdout + same.stderr)
        (out / "hooks.json").write_bytes((out / "hooks.json").read_bytes() + b" ")
        (out / "extra.md").write_bytes(b"extra\n")
        (out / "README.md").unlink()
        cache = out / "hooks" / "__pycache__"
        cache.mkdir()
        (cache / "session_start.cpython-312.pyc").write_bytes(b"\0cache")
        result = _run("check", "--repo", str(repo), "--against", str(out))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("differs: hooks.json", lines)
        self.assertIn("only in package folder: extra.md", lines)
        self.assertIn("missing from package folder: README.md", lines)
        self.assertFalse([line for line in lines if "__pycache__" in line])
        missing = _run("check", "--repo", str(repo), "--against", str(repo / "nowhere"))
        self.assertRefused(missing, "nowhere")

    def test_usage_errors_exit_2(self) -> None:
        self.assertEqual(_run().returncode, 2)
        self.assertEqual(_run("publish").returncode, 2)


class MarkdownScanningTests(unittest.TestCase):
    """Which link targets the generator sees, and how it cuts sections, on small texts."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("antigravity_package_scanning", SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def targets(self, text: str) -> list[str]:
        return [text[start:end] for start, end in self.module.link_destinations(text)]

    def test_code_is_never_a_link(self) -> None:
        text = ("[a](one.md) `[b](two.md)` ``x [c](three.md) x``\n"
                "\n```\n[d](four.md)\n```\n\n"
                "   ~~~text\n   [e](five.md)\n   ~~~\n\n"
                "start `span\ncontinues [f](six.md)` end [g](seven.md)\n"
                "\n## Heading with `tick\n"
                "next [h](eight.md) and ` [i](nine.md)\n"
                "\\[escaped](ten.md) ```inline [j](eleven.md)```\n")
        self.assertEqual(self.targets(text), ["one.md", "seven.md", "eight.md", "nine.md"])

    def test_link_forms(self) -> None:
        text = ('![img](pic.png "title") [t](<a b.md>) [p](x_(y).md) [u](https://e.com) [h](#anchor) '
                "[n [nested]](n.md#part)\n- item [l](list.md)\n| cell [c](cell.md) |\n")
        self.assertEqual(self.targets(text), ["pic.png", "a b.md", "x_(y).md", "https://e.com", "#anchor",
                                              "n.md#part", "list.md", "cell.md"])

    def test_cut_sections_keeps_order_and_skips_fenced_headings(self) -> None:
        move = self.module.SectionMove(source="x.md", target="refs/x.md", title="# T", origin="> o",
                                       replacement="> moved", wanted="sections",
                                       matches=lambda level, title: level == 3 and title.startswith("Opt"))
        body = ("# Top\n\n### Opt one\n\nA\n\n```\n### Opt fenced\n```\n\n#### Sub\n\nB\n\n"
                "### Keep\n\nC\n\n### Opt two\n\nD\n\n## Next\n\nE\n")
        kept, sections = self.module._cut_sections(body, move)
        self.assertEqual(kept.replace(self.module.SENTINEL, "<moved>"),
                         "# Top\n\n<moved>\n\n### Keep\n\nC\n\n## Next\n\nE\n")
        self.assertEqual(sections, ["### Opt one\n\nA\n\n```\n### Opt fenced\n```\n\n#### Sub\n\nB",
                                    "### Opt two\n\nD"])
        none = self.module.SectionMove(source="x.md", target="refs/x.md", title="# T", origin="> o",
                                       replacement="> moved", wanted="sections", matches=lambda level, title: False)
        with self.assertRaises(self.module.BuildError):
            self.module._cut_sections(body, none)

    def test_cut_sections_stops_at_the_end_line(self) -> None:
        # Text after a section's last paragraph that belongs to the parent step has no heading of its own;
        # the end condition keeps it (and everything after it) in place.
        move = self.module.SectionMove(source="x.md", target="refs/x.md", title="# T", origin="> o",
                                       replacement="> moved", wanted="section 'Move'",
                                       matches=lambda level, title: level == 3 and title == "Move",
                                       ends=lambda line: line.startswith("- Stay"),
                                       end_wanted="a line starting '- Stay'")
        body = ("## Step\n\n### Move\n\nA\n\n```\n- Stay inside a fence\n```\n\n- listed\n\n"
                "- Stay one\n- Stay two\n\n## Next\n\nE\n")
        kept, sections = self.module._cut_sections(body, move)
        self.assertEqual(kept.replace(self.module.SENTINEL, "<moved>"),
                         "## Step\n\n<moved>\n\n- Stay one\n- Stay two\n\n## Next\n\nE\n")
        self.assertEqual(sections, ["### Move\n\nA\n\n```\n- Stay inside a fence\n```\n\n- listed"])
        # An end line after the next heading of the same or a higher level is not this section's end.
        late = "## Step\n\n### Move\n\nA\n\n## Next\n\n- Stay late\n"
        with self.assertRaises(self.module.BuildError) as caught:
            self.module._cut_sections(late, move)
        self.assertIn("a line starting '- Stay'", str(caught.exception))
        self.assertIn("x.md", str(caught.exception))

    def test_only_text_files_are_normalised(self) -> None:
        normalise = self.module._normalise
        self.assertEqual(normalise(b"a\r\nb\r\n", "assets/notes.txt"), b"a\nb\n")
        self.assertEqual(normalise(b"a\r\nb\r\n", "hooks/run_python_hook.sh"), b"a\nb\n")
        signature = b"\x89PNG\r\n\x1a\nIHDR\r\n"  # a PNG signature has CRLF; this sample has no NUL byte
        for name in ("assets/logo.png", "assets/LOGO.PNG", "a.jpg", "a.jpeg", "a.webp", "a.gif", "a.ico",
                     "a.pdf", "a.zip"):
            with self.subTest(name=name):
                self.assertEqual(normalise(signature, name), signature)
        self.assertEqual(normalise(b"\0a\r\n", "assets/data.bin"), b"\0a\r\n")


class GitAttributesTests(unittest.TestCase):
    def test_generated_package_is_lf_everywhere(self) -> None:
        lines = _text(ROOT / ".gitattributes").rstrip("\n").split("\n")
        self.assertEqual(tuple(lines[-2:]), GITATTRIBUTES_LINES)
        self.assertEqual(lines.count(GITATTRIBUTES_LINES[1]), 1)

    def test_git_leaves_binary_package_files_alone(self) -> None:
        # text=auto: git normalises the text files to LF and does not touch a file it finds binary (a logo).
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        result = subprocess.run(["git", "-C", str(ROOT), "check-attr", "text", "eol", "--",
                                 "antigravity/assets/logo.png", "antigravity/README.md"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                env=git_environment(), stdin=subprocess.DEVNULL, timeout=120)
        if result.returncode != 0:
            self.skipTest("not a git work tree (a release ZIP, for one)")
        self.assertEqual(result.stdout.splitlines(), [
            "antigravity/assets/logo.png: text: auto", "antigravity/assets/logo.png: eol: lf",
            "antigravity/README.md: text: auto", "antigravity/README.md: eol: lf"])


class CommittedPackageSyncTests(unittest.TestCase):
    def test_committed_package_matches_the_generator(self) -> None:
        # The committed antigravity/ folder is generated; this check is the only guard on its copies.
        self.assertTrue((ROOT / "antigravity" / "plugin.json").is_file(), "generated package antigravity/ is missing")
        result = _run("check", "--repo", str(ROOT))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_committed_copies_keep_the_git_mode_of_their_sources(self) -> None:
        # The generator cannot set file modes, so the commit of the package does: an executable source (the
        # git hooks, 100755) needs `git update-index --chmod=+x` on its copy, or POSIX hosts install hooks that
        # git ignores.
        modes = _git_modes(ROOT, git_environment()) if shutil.which("git") else None
        if modes is None:
            self.skipTest("not the top of a git work tree (a release ZIP, for one) or git is not installed")
        copies = sorted(path[len("antigravity/"):] for path in modes
                        if path.startswith(tuple(f"antigravity/{prefix}" for prefix in COPIED_PREFIXES)))
        self.assertIn("assets/git-hooks/pre-commit", copies)
        self.assertIn("100755", {modes.get(path) for path in copies})
        mismatched = [f"antigravity/{path}: {modes[f'antigravity/{path}']}, source {modes.get(path)}"
                      for path in copies if modes.get(path) != modes[f"antigravity/{path}"]]
        self.assertEqual(mismatched, [], "set the copies' modes with git update-index --chmod=+x (or -x)")


if __name__ == "__main__":
    unittest.main()
