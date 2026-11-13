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
    "> - 볼트 루트는 작업 공간에서 `00-meta/vault-config.json`이 있는 폴더다. 엔진 스크립트에는 "
    "`--vault <볼트 루트>`로 넘긴다.\n"
    "> - 본문의 `/vault-*` 표기는 이 플러그인의 같은 이름 스킬을 뜻한다. Claude Code 전용 단계"
    "(설정 파일 `.claude/…`, PreToolUse 훅)는 Antigravity 세션에서 실행하지 않는다."
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
    "> 보강 helper bundle과 선택 이행 절차는 [references/helper-bundle.md](references/helper-bundle.md)로 "
    "옮겼다(Antigravity 스킬 본문 한도). 해당 단계에서 그 파일을 읽는다."
)
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
    "# Generated Antigravity package: LF everywhere so the committed copy equals the generator output on every OS.",
    "antigravity/** text eol=lf",
)

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^(#{1,6})(?:[ \t]|$)")
# An inline link or image; one level of nested brackets in the text, balanced parentheses in the target.
LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")
LINK_TARGET = re.compile(r"\]\([^()\s]*(?:\([^()\s]*\)[^()\s]*)*(?:\s+\"[^\"]*\")?\)")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
PLUGIN_ROOT_PATH = re.compile(r"<PLUGIN_ROOT>/([A-Za-z0-9_./-]*)")


def _lf(data: bytes) -> bytes:
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


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


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=600)


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
                expected[f"{top}/{relative}"] = _lf((ROOT / top / relative).read_bytes())
        for name in HOOK_FILES:
            source = ROOT / "hooks" / name
            if not source.exists():
                source = self.repo / "hooks" / name
            expected[f"hooks/{name}"] = _lf(source.read_bytes())
        copied = {path: data for path, data in self.files.items()
                  if path.startswith(("assets/", "skills/agentic-vault/scripts/", "hooks/"))}
        self.assertEqual(sorted(copied), sorted(expected))
        for relative, data in expected.items():
            with self.subTest(path=relative):
                self.assertEqual(copied[relative], data)

    def test_text_files_use_lf_and_utf8(self) -> None:
        for relative, data in self.files.items():
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


class GitAttributesTests(unittest.TestCase):
    def test_generated_package_is_lf_everywhere(self) -> None:
        lines = _text(ROOT / ".gitattributes").rstrip("\n").split("\n")
        self.assertEqual(tuple(lines[-2:]), GITATTRIBUTES_LINES)
        self.assertEqual(lines.count(GITATTRIBUTES_LINES[1]), 1)


class CommittedPackageSyncTests(unittest.TestCase):
    def test_committed_package_matches_the_generator(self) -> None:
        if not (ROOT / "antigravity").exists():
            self.skipTest("generated package not committed yet")
        result = _run("check", "--repo", str(ROOT))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
