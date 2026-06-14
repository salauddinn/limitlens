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

    def test_collect_results_threadpool_workers(self):
        from unittest.mock import patch
        from limitlens.switcher import collect_results
        
        config = {}
        args = SwitchArgs()
        
        with patch("limitlens.switcher.ThreadPoolExecutor") as mock_executor:
            try:
                collect_results(config, args)
            except Exception:
                pass
            
            mock_executor.assert_called_once()
            _, kwargs = mock_executor.call_args
            self.assertEqual(kwargs.get("max_workers"), 16)


if __name__ == "__main__":
    unittest.main()
