"""Focused behavioral tests for Jarvis configuration and message authorization."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


_BRIDGE_PATH = Path(__file__).parents[1] / "skills" / "agentic-vault" / "scripts" / "jarvis_bridge.py"
_SPEC = importlib.util.spec_from_file_location("jarvis_bridge", _BRIDGE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BRIDGE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BRIDGE)


class JarvisConfigAuthorizationTests(unittest.TestCase):
    def test_parse_briefing_slots_prefers_multiple_and_deduplicates(self):
        block = {"briefing_time": "07:30", "briefing_times": ["19:30", "08:30", "08:30"]}
        self.assertEqual(_BRIDGE.parse_briefing_slots(block), [(8, 30), (19, 30)])

    def test_parse_briefing_slots_falls_back_only_when_key_absent(self):
        self.assertEqual(_BRIDGE.parse_briefing_slots({"briefing_time": "07:30"}), [(7, 30)])
        with self.assertRaises(_BRIDGE.JarvisConfigError):
            _BRIDGE.parse_briefing_slots({"briefing_time": "07:30", "briefing_times": []})

    def test_parse_briefing_slots_rejects_bad_values(self):
        for block in (
            {"briefing_times": "08:30"},
            {"briefing_times": ["24:00"]},
            {"briefing_times": ["08:60"]},
            {"briefing_times": [8]},
        ):
            with self.subTest(block=block), self.assertRaises(_BRIDGE.JarvisConfigError):
                _BRIDGE.parse_briefing_slots(block)

    def test_authorization_requires_private_self_chat_and_whitelist(self):
        allowed = {111}
        self.assertTrue(_BRIDGE.is_authorized_private_message(
            {"from": {"id": 111}, "chat": {"id": 111, "type": "private"}}, allowed))
        self.assertFalse(_BRIDGE.is_authorized_private_message(
            {"from": {"id": 111}, "chat": {"id": -99, "type": "group"}}, allowed))
        self.assertFalse(_BRIDGE.is_authorized_private_message(
            {"from": {"id": 111}, "chat": {"id": 222, "type": "private"}}, allowed))
