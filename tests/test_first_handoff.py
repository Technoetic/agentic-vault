"""Replay the commands and expected excerpts published in the first-handoff tutorial."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "first-handoff.md"


class FirstHandoffTutorialTests(unittest.TestCase):
    def test_published_commands_resume_and_find_the_source(self):
        tutorial = DOC.read_text(encoding="utf-8")
        commands = re.findall(r"^```console\n(.+?)\n```$", tutorial, re.MULTILINE | re.DOTALL)
        expected = re.findall(r"^```text\n(.+?)\n```$", tutorial, re.MULTILINE | re.DOTALL)
        self.assertEqual(len(commands), 3)
        self.assertEqual(len(expected), 2)
        with tempfile.TemporaryDirectory() as temporary:
            # Include a space so the path substitution exercises the tutorial's quotes.
            practice_parent = Path(temporary) / "practice files"
            practice_parent.mkdir()
            env = dict(os.environ, TMP=str(practice_parent), TEMP=str(practice_parent),
                       TMPDIR=str(practice_parent), PYTHONUTF8="1")

            def run(command, folder=None):
                program, *args = shlex.split(command)
                self.assertEqual(program, "python")
                args = [str(folder) if arg == "DEMO_FOLDER" else arg for arg in args]
                result = subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                                        stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, encoding="utf-8", timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                return result.stdout.strip()

            folder = Path(run(commands[0]))
            self.assertEqual(folder.parent.resolve(), practice_parent.resolve())
            self.assertTrue(folder.is_dir())
            self.assertIn(expected[0], run(commands[1], folder))
            self.assertEqual(expected[1], run(commands[2], folder))

            source = folder / "notes" / "label-trial.md"
            self.assertEqual(source.read_text(encoding="utf-8").splitlines()[2],
                             expected[1].splitlines()[1])
            handoff = folder / "handoff.md"
            old_action = expected[0].splitlines()[-1]
            new_action = "Next: Bring the sample to the shelf."
            handoff.write_text(handoff.read_text(encoding="utf-8").replace(old_action, new_action),
                               encoding="utf-8")
            resumed = run(commands[1], folder)
            self.assertIn(new_action, resumed)
            self.assertNotIn(old_action, resumed)
