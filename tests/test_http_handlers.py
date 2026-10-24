"""Loopback test servers must read the request body before they answer.

An ``http.server`` handler that answers a POST without reading the body can close
the connection while request bytes are still unread. TCP then sends a reset (RST)
instead of a normal close (RFC 1122 section 4.2.2.13). A Windows client can get the
reset before it has read the response and raises ConnectionAbortedError (WinError
10053) or ConnectionResetError (10054); ``LocalChatTransport`` reports any such
OSError as ``transport_error``, so a test that expects another error fails now and
then. See docs/evaluation-stability.md.

The scan covers every tests/*.py like the junction check in tests/test_winfs.py,
but that check is a text-marker scan; this one parses the files with ast because
the rule is scoped to one method. It guards against a handler that forgets the
read. It is not a proof: it does not follow reads through aliases, parent classes
or helpers other than BODY_READ_HELPERS, and it does not check every code path.
"""
from __future__ import annotations

import ast
from pathlib import Path
import tempfile
import unittest

TESTS_DIR = Path(__file__).resolve().parent
# Handler methods for requests that carry a body.
BODY_METHODS = frozenset({"do_POST", "do_PUT", "do_PATCH"})
# Shared helpers that read the whole request body of the handler they are given;
# a call to one of these names counts as a read. None exists yet: the handlers
# read inline with self.rfile.read(int(self.headers.get('Content-Length') or 0)).
BODY_READ_HELPERS: frozenset[str] = frozenset()
# "file:qualified.name" -> reason, for a handler that leaves the body unread on
# purpose. An entry that no longer names an unread handler fails the check.
UNREAD_BODY_ALLOWED: dict[str, str] = {}
FIX_HINT = ("read the request body before answering, for example "
            "self.rfile.read(int(self.headers.get('Content-Length') or 0)), or list the "
            "handler in UNREAD_BODY_ALLOWED with a reason")

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _reads_body(method: ast.FunctionDef | ast.AsyncFunctionDef, helpers: frozenset[str]) -> bool:
    """True when the method calls <first parameter>.rfile.read(...) or a listed helper."""
    params = method.args.posonlyargs + method.args.args
    owner = params[0].arg if params else "self"
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in helpers:
            return True
        if isinstance(func, ast.Attribute):
            if func.attr in helpers:
                return True
            target = func.value
            if (func.attr == "read" and isinstance(target, ast.Attribute)
                    and target.attr == "rfile" and isinstance(target.value, ast.Name)
                    and target.value.id == owner):
                return True
    return False


def body_handlers(source: str | bytes, filename: str,
                  helpers: frozenset[str] = BODY_READ_HELPERS) -> list[tuple[str, int, bool]]:
    """Return (qualified name, line, reads body) for each body method in the source."""
    found = []
    stack: list[tuple[ast.AST, tuple[str, ...]]] = [(ast.parse(source, filename=filename), ())]
    while stack:
        node, scope = stack.pop()
        for child in ast.iter_child_nodes(node):
            child_scope = scope
            if isinstance(child, (*_FUNCTIONS, ast.ClassDef)):
                child_scope = scope + (child.name,)
                if isinstance(child, _FUNCTIONS) and child.name in BODY_METHODS:
                    found.append((".".join(child_scope), child.lineno, _reads_body(child, helpers)))
            stack.append((child, child_scope))
    return sorted(found)


def scan(directory: Path, helpers: frozenset[str] = BODY_READ_HELPERS) -> dict[str, tuple[int, bool]]:
    """Map "file:qualified.name" to (line, reads body) for directory/*.py, not subdirectories."""
    handlers: dict[str, tuple[int, bool]] = {}
    for path in sorted(directory.glob("*.py")):
        for name, line, reads in body_handlers(path.read_bytes(), path.name, helpers):
            key = f"{path.name}:{name}"
            # A repeated name (two classes with the same path) is unread if either is.
            if key not in handlers or handlers[key][1]:
                handlers[key] = (line, reads)
    return handlers


def review(handlers: dict[str, tuple[int, bool]],
           allowed: dict[str, str]) -> tuple[list[str], list[str]]:
    """Split a scan into unexpected unread handlers and stale allow-list entries."""
    unread = {key: line for key, (line, reads) in handlers.items() if not reads}
    offenders = sorted(f"{key} (line {line})" for key, line in unread.items() if key not in allowed)
    stale = sorted(key for key in allowed if key not in unread)
    return offenders, stale


SYNTHETIC = '''
from http.server import BaseHTTPRequestHandler


class Unread(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()


class Reads(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.send_response(204)
        self.end_headers()

    def do_PUT(self):
        observed.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))


def build():
    class Handler(BaseHTTPRequestHandler):
        def do_PATCH(handler):
            handler.rfile.read(int(handler.headers.get('Content-Length') or 0))

        def do_PUT(self):
            self.rfile.readline()

        async def do_POST(self):
            other.rfile.read(10)

        def do_DELETE(self):
            pass
    return Handler
'''

HELPER_CALLS = '''
class Handler:
    def do_POST(self):
        read_request_body(self)

    def do_PUT(self):
        self.drain_body()
'''


class BodyHandlerScanTests(unittest.TestCase):
    def test_unread_body_methods_are_reported_with_qualified_name_and_line(self) -> None:
        handlers = body_handlers(SYNTHETIC, "synthetic.py", frozenset())
        unread = [(name, line) for name, line, reads in handlers if not reads]
        lines = SYNTHETIC.splitlines()
        self.assertEqual([name for name, _ in unread],
                         ["Unread.do_POST", "build.Handler.do_POST", "build.Handler.do_PUT"])
        for name, line in unread:
            with self.subTest(name=name):
                method = name.rsplit(".", 1)[1]
                self.assertIn(f"def {method}(", lines[line - 1])

    def test_reads_through_the_first_parameter_count_and_other_methods_are_ignored(self) -> None:
        handlers = {name: reads for name, _, reads in body_handlers(SYNTHETIC, "synthetic.py")}
        self.assertEqual(handlers, {
            "Unread.do_POST": False, "Reads.do_POST": True, "Reads.do_PUT": True,
            "build.Handler.do_PATCH": True, "build.Handler.do_PUT": False,
            "build.Handler.do_POST": False,
        })

    def test_only_listed_helpers_count_as_a_read(self) -> None:
        def reads(helpers):
            return {name: value for name, _, value in body_handlers(HELPER_CALLS, "h.py", helpers)}

        self.assertEqual(reads(frozenset()), {"Handler.do_POST": False, "Handler.do_PUT": False})
        self.assertEqual(reads(frozenset({"read_request_body"})),
                         {"Handler.do_POST": True, "Handler.do_PUT": False})
        self.assertEqual(reads(frozenset({"read_request_body", "drain_body"})),
                         {"Handler.do_POST": True, "Handler.do_PUT": True})

    def test_scan_keys_are_file_and_name_and_skip_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_a.py").write_text(SYNTHETIC, encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "test_b.py").write_text(SYNTHETIC, encoding="utf-8")
            handlers = scan(root)
        self.assertEqual(sorted(handlers), sorted("test_a.py:" + name for name in (
            "Unread.do_POST", "Reads.do_POST", "Reads.do_PUT", "build.Handler.do_PATCH",
            "build.Handler.do_PUT", "build.Handler.do_POST")))
        self.assertEqual(handlers["test_a.py:Reads.do_POST"][1], True)

    def test_a_repeated_name_is_unread_if_any_copy_is(self) -> None:
        reading = "    class Handler:\n        def do_POST(self):\n            self.rfile.read(1)\n"
        unread = "    class Handler:\n        def do_POST(self):\n            pass\n"
        for order in ((reading, unread), (unread, reading)):
            with self.subTest(first_reads=order[0] is reading), \
                    tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "test_twice.py").write_text("def make():\n" + "".join(order),
                                                         encoding="utf-8")
                self.assertEqual(scan(Path(tmp))["test_twice.py:make.Handler.do_POST"][1], False)

    def test_review_reports_unexpected_handlers_and_stale_allow_list_entries(self) -> None:
        handlers = {"a.py:X.do_POST": (3, False), "b.py:Y.do_PUT": (9, False),
                    "c.py:Z.do_POST": (4, True)}
        allowed = {"b.py:Y.do_PUT": "tests an unread body on purpose",
                   "c.py:Z.do_POST": "reads now", "d.py:W.do_POST": "removed"}
        self.assertEqual(review(handlers, allowed),
                         (["a.py:X.do_POST (line 3)"], ["c.py:Z.do_POST", "d.py:W.do_POST"]))


class RequestBodyReadTests(unittest.TestCase):
    def test_test_servers_read_the_request_body_before_answering(self) -> None:
        handlers = scan(TESTS_DIR)
        # The walk must see handlers at all, or an empty result would pass vacuously.
        self.assertTrue(any(reads for _, reads in handlers.values()))
        offenders, stale = review(handlers, UNREAD_BODY_ALLOWED)
        self.assertEqual(offenders, [], FIX_HINT)
        self.assertEqual(stale, [], "UNREAD_BODY_ALLOWED names handlers that read the body or are gone")
        self.assertTrue(all(reason.strip() for reason in UNREAD_BODY_ALLOWED.values()))


if __name__ == "__main__":
    unittest.main()
