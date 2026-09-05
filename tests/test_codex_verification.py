"""Verifier subprocess failures must not leave children holding temporary resources."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_verification", ROOT / "scripts/verify_codex_plugin.py",
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class CodexVerificationTests(unittest.TestCase):
    def test_direct_executable_returns_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = verifier.run_json(
                sys.executable, ["-c", "print('{\"source\": \"native\"}')"],
                Path(temporary), os.environ.copy(),
            )
            self.assertEqual(result, {"source": "native"})

    @unittest.skipUnless(os.name == "nt", "Windows npm launcher regression")
    def test_npm_launcher_uses_native_binary_for_supported_vendor_layouts(self) -> None:
        target = "aarch64" if platform.machine().lower() == "arm64" else "x86_64"
        package_arch = "arm64" if target == "aarch64" else "x64"
        for layout in ("nested", "hoisted", "bundled"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                launcher = root / "codex.CMD"
                launcher.write_text('@echo {"source":"wrapper"}\n', encoding="utf-8")
                package = root / "node_modules/@openai/codex"
                (package / "bin").mkdir(parents=True)
                (package / "bin/codex.js").write_text("// npm entrypoint\n", encoding="utf-8")
                if layout == "bundled":
                    vendor = package / "vendor"
                else:
                    modules = package / "node_modules" if layout == "nested" else root / "node_modules"
                    vendor = modules / f"@openai/codex-win32-{package_arch}/vendor"
                native = vendor / f"{target}-pc-windows-msvc/bin/codex.exe"
                native.parent.mkdir(parents=True)
                shutil.copyfile(sys.executable, native)
                for dll in Path(sys.executable).parent.glob("python*.dll"):
                    shutil.copyfile(dll, native.parent / dll.name)
                environment = {**os.environ, "PYTHONHOME": sys.base_prefix}

                result = verifier.run_json(
                    str(launcher), ["-c", "print('{\"source\": \"native\"}')"],
                    root, environment,
                )

                self.assertEqual(result, {"source": "native"})

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher regression")
    def test_unknown_batch_is_rejected_before_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for extension in ("cmd", "bat"):
                with self.subTest(extension=extension):
                    launcher = root / f"custom.{extension}"
                    launcher.write_text('@echo {"source":"wrapper"}\n', encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "--codex.*native"):
                        verifier.run_json(str(launcher), [], root, os.environ.copy())

    def make_sleeping_launcher(self, root: Path) -> Path:
        child = root / "child.py"
        child.write_text(
            "from pathlib import Path\n"
            "import os, time\n"
            "root = Path.cwd()\n"
            "handle = (root / 'child.lock').open('w')\n"
            "if os.name != 'nt':\n"
            "    import fcntl\n"
            "    fcntl.flock(handle, fcntl.LOCK_EX)\n"
            "(root / 'started').write_text('started')\n"
            "print('invalid JSON', flush=True)\n"
            "deadline = time.monotonic() + 15\n"
            "while not (root / 'release').exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.02)\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            launcher = root / "custom.cmd"
            launcher.write_text(f'@"{sys.executable}" "{child}"\n', encoding="utf-8")
        else:
            import shlex
            launcher = root / "custom.sh"
            launcher.write_text(
                f"#!/bin/sh\n{shlex.quote(sys.executable)} {shlex.quote(str(child))} &\nwait\n",
                encoding="utf-8",
            )
            launcher.chmod(0o700)
        return launcher

    def cleanup_fixture(self, temporary: tempfile.TemporaryDirectory) -> None:
        root = Path(temporary.name)
        if root.exists():
            (root / "release").touch()
            if os.name != "nt" and (root / "child.lock").is_file():
                self.assert_child_lock_released(root)
        # Release an old broken implementation's child before removing its cwd.
        deadline = time.monotonic() + 5
        while True:
            try:
                temporary.cleanup()
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    def assert_child_lock_released(self, root: Path) -> None:
        import fcntl
        with (root / "child.lock").open("a") as handle:
            deadline = time.monotonic() + 0.75
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self.fail("Verifier child retained its lock after process cleanup")
                    time.sleep(0.01)

    def test_failed_inspection_releases_the_temporary_directory(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        launcher = self.make_sleeping_launcher(root)
        expected = "--codex.*native" if os.name == "nt" else "invalid JSON"

        try:
            with self.assertRaisesRegex(RuntimeError, expected):
                verifier.inspect_host(str(launcher), root, os.environ.copy(), root)
        finally:
            # This is deliberately immediate: retries would mask an orphaned process.
            temporary.cleanup()

        self.assertFalse(root.exists())

    def test_native_command_timeout_releases_temporary_resources(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        self.make_sleeping_launcher(root)

        with self.assertRaises(subprocess.TimeoutExpired):
            verifier.run_json(
                sys.executable, [str(root / "child.py")], root,
                os.environ.copy(), timeout=1,
            )

        self.assertTrue((root / "started").is_file())
        temporary.cleanup()
        self.assertFalse(root.exists())

    @unittest.skipIf(os.name == "nt", "POSIX owned process group regression")
    def test_run_json_timeout_releases_child_lock(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        launcher = self.make_sleeping_launcher(root)

        with self.assertRaises(subprocess.TimeoutExpired):
            verifier.run_json(str(launcher), [], root, os.environ.copy(), timeout=0.5)

        self.assertTrue((root / "started").is_file())
        self.assert_child_lock_released(root)

    @unittest.skipIf(os.name == "nt", "POSIX owned process group regression")
    def test_failed_inspection_releases_child_lock(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_fixture, temporary)
        root = Path(temporary.name)
        launcher = self.make_sleeping_launcher(root)

        with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
            verifier.inspect_host(str(launcher), root, os.environ.copy(), root)

        self.assertTrue((root / "started").is_file())
        self.assert_child_lock_released(root)


if __name__ == "__main__":
    unittest.main()
