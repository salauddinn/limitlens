#!/usr/bin/env python3
"""Tests for limitlens.keychain."""

import unittest
from unittest.mock import patch, MagicMock

from limitlens.keychain import set_keychain_token, get_keychain_token

class TestKeychain(unittest.TestCase):
    @patch("limitlens.keychain.sys.platform", "darwin")
    @patch("limitlens.keychain.subprocess.run")
    def test_set_keychain_token_mac(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(set_keychain_token("test_acc", "test_tok"))
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "security")

    @patch("limitlens.keychain.sys.platform", "linux")
    @patch("limitlens.keychain.subprocess.run")
    def test_set_keychain_token_linux(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(set_keychain_token("test_acc", "test_tok"))
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "secret-tool")

    @patch("limitlens.keychain.sys.platform", "darwin")
    @patch("limitlens.keychain.subprocess.run")
    def test_get_keychain_token_mac(self, mock_run):
        mock_run.return_value = MagicMock(stdout="test_tok\n")
        self.assertEqual(get_keychain_token("test_acc"), "test_tok")

    @patch("limitlens.keychain.sys.platform", "linux")
    @patch("limitlens.keychain.subprocess.run")
    def test_get_keychain_token_linux(self, mock_run):
        mock_run.return_value = MagicMock(stdout="test_tok\n")
        self.assertEqual(get_keychain_token("test_acc"), "test_tok")

    @patch("limitlens.keychain.sys.platform", "win32")
    def test_get_keychain_token_unsupported(self):
        self.assertIsNone(get_keychain_token("test_acc"))

if __name__ == "__main__":
    unittest.main()
