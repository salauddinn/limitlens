#!/usr/bin/env python3
"""Tests for limitlens.switcher."""

import unittest

from limitlens.switcher import SwitchArgs

class TestSwitcher(unittest.TestCase):
    def test_switch_args_defaults(self):
        args = SwitchArgs()
        self.assertEqual(args.tool, "all")
        self.assertFalse(args.json)
        self.assertTrue(args.redact)
        self.assertFalse(args.sync_codex)
        self.assertFalse(args.verbose)
        self.assertFalse(args.no_color)

    def test_switch_args_custom(self):
        args = SwitchArgs(tool="amp", json=True, redact=False, sync_codex=True, verbose=True, no_color=True)
        self.assertEqual(args.tool, "amp")
        self.assertTrue(args.json)
        self.assertFalse(args.redact)
        self.assertTrue(args.sync_codex)
        self.assertTrue(args.verbose)
        self.assertTrue(args.no_color)

if __name__ == "__main__":
    unittest.main()
