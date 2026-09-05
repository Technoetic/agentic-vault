#!/usr/bin/env python3
"""Check native Codex installation/discovery in a disposable home; no model turn."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "agentic-vault@agentic-vault-local"


def resolve_codex_executable(codex: str) -> str:
    executable = Path(shutil.which(codex) or codex).resolve()
    if os.name != "nt" or executable.suffix.lower() not in {".cmd", ".bat"}:
        return str(executable)

    targets = {
        "amd64": ("x64", "x86_64"), "x86_64": ("x64", "x86_64"),
        "arm64": ("arm64", "aarch64"), "aarch64": ("arm64", "aarch64"),
    }
    target = targets.get(platform.machine().lower())
    if executable.stem.lower() == "codex" and target is not None:
        package_arch, architecture = target
        for package in (
            executable.parent / "node_modules/@openai/codex",
            executable.parent.parent / "@openai/codex",
        ):
            if not (package / "bin/codex.js").is_file():
                continue
            optional = f"@openai/codex-win32-{package_arch}/vendor"
            for vendor in (
                package / "node_modules" / optional,
                package.parents[1] / optional,
                package / "vendor",
            ):
                native = vendor / f"{architecture}-pc-windows-msvc/bin/codex.exe"
                if native.is_file():
                    return str(native.resolve())
    raise RuntimeError("Cannot resolve Windows batch launcher; pass --codex with a native codex.exe path")


def stop_owned_processes(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            process.kill()
    else:
        # Each verifier command owns a new session; include any wrapper children.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def run_json(
    codex: str, args: list[str], cwd: Path, environment: dict, *, timeout: float = 30,
) -> dict:
    with subprocess.Popen(
        [resolve_codex_executable(codex), *args], cwd=cwd, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", start_new_session=os.name != "nt",
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        finally:
            stop_owned_processes(process)
        if process.returncode:
            raise RuntimeError(f"Codex {' '.join(args[:3])} failed: {stderr.strip()}")
        return json.loads(stdout)


def inspect_host(codex: str, vault: Path, environment: dict, home: Path) -> dict:
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        process = subprocess.Popen([resolve_codex_executable(codex), "app-server", "--stdio"], cwd=vault,
                                   env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=error_log,
                                   text=True, encoding="utf-8", bufsize=1,
                                   start_new_session=os.name != "nt")
        responses: queue.Queue = queue.Queue()

        def consume() -> None:
            try:
                for line in process.stdout:
                    responses.put(json.loads(line))
            except (ValueError, OSError) as error:
                responses.put(error)
            finally:
                responses.put(None)

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()

        def send(message: dict) -> None:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def request(number: int, method: str, params: dict) -> dict:
            send({"id": number, "method": method, "params": params})
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    response = responses.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty as error:
                    raise RuntimeError(f"{method} timed out") from error
                if not isinstance(response, dict):
                    raise RuntimeError("Codex app-server closed or returned invalid JSON")
                if response.get("id") == number:
                    if "error" in response:
                        raise RuntimeError(f"{method}: {response['error']}")
                    return response["result"]
            raise RuntimeError(f"{method} timed out")

        try:
            initialized = request(1, "initialize", {
                "clientInfo": {"name": "agentic-vault-verification", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            })
            if Path(initialized["codexHome"]).resolve() != home:
                raise RuntimeError("Codex did not use the isolated test home")
            send({"method": "initialized", "params": {}})
            skills = request(2, "skills/list", {"cwds": [str(vault)], "forceReload": True})
            hooks = request(3, "hooks/list", {"cwds": [str(vault)]})
            if any(item.get("errors") for item in skills["data"] + hooks["data"]):
                raise RuntimeError("Codex reported skill or hook discovery errors")
            owned_skills = [skill for item in skills["data"] for skill in item["skills"]
                            if skill.get("pluginId") == PLUGIN_ID]
            owned_hooks = [hook for item in hooks["data"] for hook in item["hooks"]
                           if hook.get("pluginId") == PLUGIN_ID]
            primary = [skill for skill in owned_skills
                       if skill["name"] == "agentic-vault:agentic-vault" and skill["enabled"]]
            if len(primary) != 1 or not Path(primary[0]["path"]).is_file():
                raise RuntimeError("The shared agentic-vault skill was not discovered")
            if len(owned_hooks) != 2 or any(hook["eventName"] != "sessionStart" for hook in owned_hooks):
                raise RuntimeError("Expected both shared SessionStart hooks")
            if any(hook["trustStatus"] != "untrusted" for hook in owned_hooks):
                raise RuntimeError("Disposable-home hooks unexpectedly trusted")
            return {"codex": initialized["userAgent"], "skill": primary[0]["name"],
                    "session_start_hooks": len(owned_hooks), "hook_trust": "untrusted"}
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass  # A host that exited early may have already closed its pipe.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            stop_owned_processes(process)
            reader.join(timeout=2)
            process.stdout.close()


def verify(codex: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="agentic-vault-codex-") as temporary:
        root = Path(temporary).resolve()
        package = root / "agentic-vault"
        shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns(
            ".git", ".superpowers", "__pycache__", "*.pyc"))
        home = root / "codex-home"
        home.mkdir()
        vault = root / "test vault"
        vault.mkdir()
        environment = os.environ.copy()
        environment.update(CODEX_HOME=str(home), PYTHONUTF8="1")
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CLAUDE_PROJECT_DIR"):
            environment.pop(name, None)
        market = run_json(codex, ["plugin", "marketplace", "add", str(package), "--json"], vault, environment)
        if market["marketplaceName"] != "agentic-vault-local":
            raise RuntimeError("Unexpected marketplace identity")
        installed = run_json(codex, ["plugin", "add", PLUGIN_ID, "--json"], vault, environment)
        expected = json.loads((ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        cached = Path(installed["installedPath"]).resolve()
        cached.relative_to(home)
        if installed["version"] != expected["version"]:
            raise RuntimeError("Codex installed a different version")
        report = inspect_host(codex, vault, environment, home)
        for relative in ("hooks/session_start.py", "hooks/run_python_hook.sh",
                         "skills/agentic-vault/references/codex.md",
                         "skills/agentic-vault/scripts/vault_recall.py",
                         "assets/templates/AGENTS-vault-stub.md"):
            if (cached / relative).read_bytes() != (ROOT / relative).read_bytes():
                raise RuntimeError(f"Installed resource differs: {relative}")
        return {"version": installed["version"], "installed_resources": "verified",
                "isolated_home": True, "model_turns": 0, **report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", default=shutil.which("codex"),
        help="Codex executable path (Windows custom launchers require a native codex.exe)",
    )
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex CLI is required; pass --codex PATH")
    try:
        report = verify(args.codex)
    except (OSError, RuntimeError, ValueError, KeyError, queue.Empty, subprocess.TimeoutExpired) as error:
        print(f"Codex plugin verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
