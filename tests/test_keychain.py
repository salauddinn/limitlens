#!/usr/bin/env python3
"""Tests for limitlens.keychain."""

import sys
import unittest
from unittest.mock import patch, MagicMock

# Mock keyring module BEFORE importing get_keychain_token/set_keychain_token
mock_keyring = MagicMock()
sys.modules['keyring'] = mock_keyring

from limitlens.keychain import set_keychain_token, get_keychain_token  # noqa: E402

class TestKeychain(unittest.TestCase):
    @patch("limitlens.keychain._keyring_available", False)
    @patch("limitlens.keychain.sys.platform", "darwin")
    def test_set_keychain_token_mac(self):
        self.assertFalse(set_keychain_token("test_acc", "test_tok"))

    @patch("limitlens.keychain._keyring_available", False)
    @patch("limitlens.keychain.sys.platform", "linux")
    @patch("limitlens.keychain.subprocess.run")
    def test_set_keychain_token_linux(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(set_keychain_token("test_acc", "test_tok"))
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "secret-tool")

    @patch("limitlens.keychain._keyring_available", False)
    @patch("limitlens.keychain.sys.platform", "darwin")
    @patch("limitlens.keychain.subprocess.run")
    def test_get_keychain_token_mac(self, mock_run):
        mock_run.return_value = MagicMock(stdout="test_tok\n")
        self.assertEqual(get_keychain_token("test_acc"), "test_tok")

    @patch("limitlens.keychain._keyring_available", False)
    @patch("limitlens.keychain.sys.platform", "linux")
    @patch("limitlens.keychain.subprocess.run")
    def test_get_keychain_token_linux(self, mock_run):
        mock_run.return_value = MagicMock(stdout="test_tok\n")
        self.assertEqual(get_keychain_token("test_acc"), "test_tok")

    @patch("limitlens.keychain._keyring_available", False)
    @patch("limitlens.keychain.sys.platform", "win32")
    def test_get_keychain_token_unsupported(self):
        self.assertIsNone(get_keychain_token("test_acc"))

    @patch("limitlens.keychain._keyring_available", True)
    @patch("keyring.set_password")
    def test_set_keychain_token_keyring_success(self, mock_set):
        self.assertTrue(set_keychain_token("test_acc", "test_tok"))
        mock_set.assert_called_once_with("limitlens", "test_acc", "test_tok")

    @patch("limitlens.keychain._keyring_available", True)
    @patch("keyring.get_password")
    def test_get_keychain_token_keyring_success(self, mock_get):
        mock_get.return_value = "test_tok"
        self.assertEqual(get_keychain_token("test_acc"), "test_tok")
        mock_get.assert_called_once_with("limitlens", "test_acc")

    @patch("limitlens.keychain._keyring_available", True)
    @patch("keyring.set_password")
    @patch("limitlens.keychain.sys.platform", "darwin")
    def test_set_keychain_token_keyring_fails_fallback(self, mock_set):
        mock_set.side_effect = RuntimeError("error")
        self.assertFalse(set_keychain_token("test_acc", "test_tok"))
        mock_set.assert_called_once()

    @patch("limitlens.keychain._keyring_available", True)
    @patch("keyring.get_password")
    @patch("limitlens.keychain.sys.platform", "darwin")
    @patch("limitlens.keychain.subprocess.run")
    def test_get_keychain_token_keyring_fails_fallback(self, mock_run, mock_get):
        mock_get.side_effect = RuntimeError("error")
        mock_run.return_value = MagicMock(stdout="fallback_tok\n")
        self.assertEqual(get_keychain_token("test_acc"), "fallback_tok")
        mock_get.assert_called_once()
        mock_run.assert_called_once()

if __name__ == "__main__":
    unittest.main()
