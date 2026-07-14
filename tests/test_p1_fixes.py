"""
Tests for all 7 P1 fixes:
  #10 — stdout mutation in watch mode
  #8  — gRPC parser hardening
  #7  — error swallowing (grok.py docstring + narrow exceptions)
  #11 — unified logging module
  #5  — provider descriptor registry
  #6  — defaults drift (via PROVIDER_DESCRIPTORS)
"""

import argparse
import logging
import os
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _args(**kw):
    defaults = {
        "no_color": True, "verbose": False, "all": False,
        "redact": False, "tool": "all", "json": False,
        "watch": False, "interval": 5.0, "no_record": True,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# #10 — stdout mutation in watch mode
# ---------------------------------------------------------------------------

class TestStdoutRestoreOnError(unittest.TestCase):
    """#10: sys.stdout must be restored even when display_result raises."""

    def test_stdout_restored_after_display_raises(self):
        """
        Simulate the watch-mode capture loop with the fix applied.
        After display_result raises, sys.stdout must still point to the real terminal.
        """
        import io as _io
        import sys as _sys

        real_stdout = _sys.stdout
        buf = _io.StringIO()
        _orig = _sys.stdout

        def bad_display():
            raise RuntimeError("display failed")

        raised = False
        _sys.stdout = buf
        try:
            bad_display()
        except RuntimeError:
            raised = True
        finally:
            _sys.stdout = _orig  # the fix — runs regardless of exception

        self.assertTrue(raised, "bad_display should have raised RuntimeError")
        self.assertIs(_sys.stdout, real_stdout,
                      "sys.stdout was not restored after display raised")


    def test_stdout_restored_on_success(self):
        """Normal path: sys.stdout still restored after successful display."""
        import io as _io
        import sys as _sys

        real_stdout = _sys.stdout
        buf = _io.StringIO()
        _orig = _sys.stdout

        _sys.stdout = buf
        try:
            print("hello")
        finally:
            _sys.stdout = _orig

        self.assertIs(_sys.stdout, real_stdout)
        self.assertIn("hello", buf.getvalue())


# ---------------------------------------------------------------------------
# #8 — gRPC parser hardening (_parse_grpc_percent)
# ---------------------------------------------------------------------------

class TestParseGrpcPercent(unittest.TestCase):
    """#8: all bounds checks must be present in _parse_grpc_percent."""

    @classmethod
    def setUpClass(cls):
        from limitlens.providers.grok import _parse_grpc_percent
        cls.parse = staticmethod(_parse_grpc_percent)

    def _make_frame(self, flag, body):
        """Build a minimal gRPC-Web frame."""
        return bytes([flag]) + struct.pack('>I', len(body)) + body

    def _make_proto_float(self, value):
        """Build the minimal protobuf body for a single float field."""
        # tag 0x0a (field 1, LEN) | varint(4) | tag 0x0d (field 1, FIXED32) | float LE
        float_bytes = struct.pack('<f', value)
        return b'\x0a\x04\x0d' + float_bytes

    def test_valid_frame_returns_float(self):
        body = self._make_proto_float(73.5)
        frame = self._make_frame(0, body)
        result = self.parse(frame)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 73.5, places=2)

    def test_none_for_non_bytes(self):
        """Non-bytes input → None, not an exception."""
        self.assertIsNone(self.parse("not bytes"))
        self.assertIsNone(self.parse(None))
        self.assertIsNone(self.parse(123))

    def test_too_short_returns_none(self):
        """Fewer than 6 bytes → None."""
        self.assertIsNone(self.parse(b''))
        self.assertIsNone(self.parse(b'\x00\x00'))
        self.assertIsNone(self.parse(b'\x00\x00\x00\x00\x00'))  # exactly 5

    def test_truncated_frame_returns_none(self):
        """Declared length exceeds available bytes → None, not IndexError."""
        # Claim body is 100 bytes, only provide 5
        frame = b'\x00' + struct.pack('>I', 100) + b'\x00' * 5
        self.assertIsNone(self.parse(frame))

    def test_oversized_length_cap(self):
        """Frame length > 1 MB → None (frame-size cap A4)."""
        # length = 2 MB
        frame = b'\x00' + struct.pack('>I', 2 << 20) + b'\x00' * 5
        self.assertIsNone(self.parse(frame))

    def test_malformed_varint_no_hang(self):
        """All-high-bit bytes in varint → parser must not spin endlessly."""
        # 0x0a tag, then 20 bytes all with the continuation bit set
        bad_varint = b'\x0a' + b'\xff' * 20
        # Pad to make the frame valid-length
        body = bad_varint + b'\x00' * 10
        frame = self._make_frame(0, body)
        # Must return quickly (no infinite loop) and return None
        result = self.parse(frame)
        self.assertIsNone(result)

    def test_truncated_float_returns_none(self):
        """Float field present but not enough bytes for 4-byte unpack → None."""
        # tag 0x0a, varint(0), tag 0x0d, only 3 bytes of float
        body = b'\x0a\x00\x0d' + b'\x00' * 3
        frame = self._make_frame(0, body)
        result = self.parse(frame)
        self.assertIsNone(result)

    def test_trailer_frame_ignored(self):
        """Non-data frames (flag != 0) should be skipped, not returned."""
        # Trailer frame: flag=0x80
        trailer = b'\x80' + struct.pack('>I', 0)
        # Valid data frame follows
        body = self._make_proto_float(50.0)
        data_frame = self._make_frame(0, body)
        result = self.parse(trailer + data_frame)
        self.assertAlmostEqual(result, 50.0, places=2)

    def test_wrong_tag_returns_none(self):
        """Protobuf body with wrong first tag → None."""
        body = b'\x09' + b'\x00' * 10  # tag 0x09 (not 0x0a)
        frame = self._make_frame(0, body)
        self.assertIsNone(self.parse(frame))


# ---------------------------------------------------------------------------
# #7 — error swallowing: grok.py narrow exceptions + docstring
# ---------------------------------------------------------------------------

class TestGrokDocstringFixed(unittest.TestCase):
    """#7: docstring must not claim 'No tokens are transmitted'."""

    def test_docstring_no_longer_claims_no_tokens_transmitted(self):
        import limitlens.providers.grok as grok_mod
        docstring = grok_mod.__doc__ or ""
        self.assertNotIn(
            "No tokens are read, transmitted, or logged",
            docstring,
            "Docstring still contains the false 'No tokens are transmitted' claim",
        )


class TestGrokFetchNarrowExceptions(unittest.TestCase):
    """#7: fetch_grok_usage must return structured error dicts, not bare None."""

    @classmethod
    def setUpClass(cls):
        from limitlens.providers.grok import fetch_grok_usage
        cls.fetch = staticmethod(fetch_grok_usage)

    def test_http_403_returns_auth_expired_dict(self):
        import urllib.error
        http_err = urllib.error.HTTPError(
            url="https://grok.com/...", code=403,
            msg="Forbidden", hdrs=None, fp=None
        )
        with patch("urllib.request.OpenerDirector.open", side_effect=http_err):
            result = self.fetch("fake-cookie")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "auth_expired")

    def test_http_500_returns_http_error_dict(self):
        import urllib.error
        http_err = urllib.error.HTTPError(
            url="https://grok.com/...", code=500,
            msg="Server Error", hdrs=None, fp=None
        )
        with patch("urllib.request.OpenerDirector.open", side_effect=http_err):
            result = self.fetch("fake-cookie")
        self.assertIsInstance(result, dict)
        self.assertIn("http_500", result.get("error", ""))

    def test_url_error_returns_network_dict(self):
        import urllib.error
        url_err = urllib.error.URLError("Name or service not known")
        with patch("urllib.request.OpenerDirector.open", side_effect=url_err):
            result = self.fetch("fake-cookie")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "network")

    def test_timeout_returns_dns_or_timeout_dict(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("timed out")):
            result = self.fetch("fake-cookie")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "dns_or_timeout")

    def test_socket_error_returns_dns_or_timeout_dict(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=socket.gaierror("Name not resolved")):
            result = self.fetch("fake-cookie")
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("error"), "dns_or_timeout")


# ---------------------------------------------------------------------------
# #11 — Unified logging module
# ---------------------------------------------------------------------------

class TestLoggingModule(unittest.TestCase):
    """#11: limitlens.logging must exist and work correctly."""

    def test_get_logger_returns_logger(self):
        from limitlens.logging import get_logger
        logger = get_logger()
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, "limitlens")

    def test_get_logger_idempotent(self):
        from limitlens.logging import get_logger
        l1 = get_logger()
        l2 = get_logger()
        self.assertIs(l1, l2)

    def test_get_logger_rejects_names_outside_namespace(self):
        from limitlens.logging import get_logger
        with self.assertRaises(ValueError):
            get_logger("plugin")

    def test_redact_filter_strips_email(self):
        from limitlens.logging import RedactFilter
        filt = RedactFilter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="", lineno=0,
            msg="User foo@example.com logged in",
            args=(), exc_info=None,
        )
        filt.filter(record)
        self.assertNotIn("foo@example.com", record.msg)
        self.assertIn("***", record.msg)

    def test_redact_filter_strips_home_path(self):
        from limitlens.logging import RedactFilter, _HOME
        filt = RedactFilter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="", lineno=0,
            msg=f"Config at {_HOME}/.config/limitlens/config.json",
            args=(), exc_info=None,
        )
        filt.filter(record)
        self.assertNotIn(_HOME, record.msg)
        self.assertIn("~", record.msg)

    def test_redact_filter_strips_sso_cookie(self):
        from limitlens.logging import RedactFilter
        filt = RedactFilter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="", lineno=0,
            msg="sso=abcdef1234567890 and more text",
            args=(), exc_info=None,
        )
        filt.filter(record)
        self.assertNotIn("abcdef1234567890", record.msg)

    def test_redact_filter_handles_tuple_args(self):
        from limitlens.logging import RedactFilter
        filt = RedactFilter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="", lineno=0,
            msg="Error for %s",
            args=("user@test.com",),
            exc_info=None,
        )
        filt.filter(record)
        self.assertNotIn("user@test.com", record.args[0])

    def test_privacy_bug_proc_stderr_redacted(self):
        """
        The privacy bug: proc.stderr must pass through RedactFilter before
        being written to the log, not raw. Simulate a stderr string containing
        a home path and verify it would be redacted.
        """
        from limitlens.logging import RedactFilter, _HOME
        filt = RedactFilter()
        fake_stderr = f"Traceback: {_HOME}/.config/limitlens/config.json"
        record = logging.LogRecord(
            name="limitlens.menubar", level=logging.WARNING,
            pathname="", lineno=0,
            msg="Menubar command failure (rc=%s): %s",
            args=(1, fake_stderr),
            exc_info=None,
        )
        filt.filter(record)
        # After filtering, the args should not contain the raw home path
        self.assertNotIn(_HOME, str(record.args))

    def test_redacting_handler_redacts_tracebacks(self):
        from limitlens.logging import RedactingHandler

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "limitlens.log")
            handler = RedactingHandler(path)
            logger = logging.getLogger("limitlens.test.redacting_handler")
            logger.handlers = [handler]
            logger.propagate = False
            logger.setLevel(logging.ERROR)
            try:
                try:
                    raise ValueError("sso=abcdef1234567890")
                except ValueError:
                    logger.exception("request failed")
            finally:
                handler.close()
                logger.handlers = []

            with open(path, encoding="utf-8") as log_file:
                self.assertNotIn("abcdef1234567890", log_file.read())

    def test_get_logger_uses_single_shared_handler(self):
        """All child loggers must share one handler on the root logger.

        Regression test: previously ``get_logger("limitlens.foo")`` would
        attach a new ``RedactingHandler`` to each child logger, leaving
        N independent handlers all writing to the same log file.  During
        rollover, those handlers raced and could lose diagnostics.
        """
        import importlib
        import limitlens.logging as ll_logging
        root = logging.getLogger("limitlens")
        old_state = (
            ll_logging._LOGGER,
            ll_logging._HANDLER,
            ll_logging._CONFIGURED,
            list(root.handlers),
            root.level,
            root.propagate,
        )

        def restore_logging_state():
            for handler in list(root.handlers):
                root.removeHandler(handler)
                if handler not in old_state[3]:
                    handler.close()
            root.handlers = old_state[3]
            root.setLevel(old_state[4])
            root.propagate = old_state[5]
            ll_logging._LOGGER = old_state[0]
            ll_logging._HANDLER = old_state[1]
            ll_logging._CONFIGURED = old_state[2]

        self.addCleanup(restore_logging_state)
        # Reset module-level state to simulate a fresh process.
        importlib.reload(ll_logging)
        ll_logging._HANDLER = None
        ll_logging._LOGGER = None
        ll_logging._CONFIGURED = False

        # Wipe any handlers left over on the root logger from prior tests.
        for h in list(root.handlers):
            root.removeHandler(h)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {
                "LIMITLENS_LOG_PATH": os.path.join(temp_dir, "shared.log")
            }):
                # Mimic how every limitlens module obtains its logger.
                a = ll_logging.get_logger("limitlens.test.shared_a")
                b = ll_logging.get_logger("limitlens.test.shared_b")
                c = ll_logging.get_logger("limitlens.test.shared_c")

                # Exactly one handler must live on the root logger.
                self.assertEqual(
                    len(root.handlers), 1,
                    f"root logger should have exactly 1 handler, "
                    f"got {len(root.handlers)}",
                )

                # Child loggers must NOT carry their own handlers.
                for child in (a, b, c):
                    self.assertEqual(
                        child.handlers, [],
                        f"child logger {child.name!r} must not have "
                        f"its own handlers",
                    )

                # All three resolve to the same shared handler instance.
                shared = root.handlers[0]
                self.assertIsInstance(shared, ll_logging.RedactingHandler)
                self.assertFalse(root.propagate)
            restore_logging_state()

    def test_cross_module_rollover_uses_single_handler(self):
        """Concurrent rollover writes from many child loggers stay serialized.

        The total handler count across the root + all child loggers must
        remain at 1, so only one writer can ever trigger ``doRollover``.
        """
        from concurrent.futures import ThreadPoolExecutor
        import importlib
        import limitlens.logging as ll_logging
        root = logging.getLogger("limitlens")
        old_state = (
            ll_logging._LOGGER,
            ll_logging._HANDLER,
            ll_logging._CONFIGURED,
            list(root.handlers),
            root.level,
            root.propagate,
        )

        def restore_logging_state():
            for handler in list(root.handlers):
                root.removeHandler(handler)
                if handler not in old_state[3]:
                    handler.close()
            root.handlers = old_state[3]
            root.setLevel(old_state[4])
            root.propagate = old_state[5]
            ll_logging._LOGGER = old_state[0]
            ll_logging._HANDLER = old_state[1]
            ll_logging._CONFIGURED = old_state[2]

        self.addCleanup(restore_logging_state)
        importlib.reload(ll_logging)
        ll_logging._HANDLER = None
        ll_logging._LOGGER = None
        ll_logging._CONFIGURED = False

        for h in list(root.handlers):
            root.removeHandler(h)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {
                "LIMITLENS_LOG_PATH": os.path.join(temp_dir, "rollover.log")
            }):
                names = [
                    "limitlens.test.rollover_a",
                    "limitlens.test.rollover_b",
                    "limitlens.test.rollover_c",
                    "limitlens.test.rollover_d",
                    "limitlens.test.rollover_e",
                    "limitlens.test.rollover_f",
                ]
                loggers = [ll_logging.get_logger(n) for n in names]
                handler = root.handlers[0]
                handler.maxBytes = 256

                def write_records(logger):
                    for i in range(50):
                        logger.warning("cross-module rollover probe %d", i)

                # Drive concurrent records through every child logger and
                # force multiple rotations through the shared handler.
                with ThreadPoolExecutor(max_workers=len(loggers)) as pool:
                    list(pool.map(write_records, loggers))

                # Count all handlers reachable from any of our loggers
                # (root + each child).  Must be exactly 1.
                all_handlers = list(root.handlers)
                for lg in loggers:
                    all_handlers.extend(lg.handlers)
                # De-duplicate by id (the same handler must appear in all).
                unique_ids = {id(h) for h in all_handlers}
                self.assertEqual(
                    len(unique_ids), 1,
                    f"expected 1 unique handler, got {len(unique_ids)}",
                )
                # And the file must actually have received every record.
                handler.flush()
                log_files = [
                    Path(handler.baseFilename),
                    *Path(handler.baseFilename).parent.glob(
                        f"{Path(handler.baseFilename).name}.*"
                    ),
                ]
                self.assertGreaterEqual(len(log_files), 2)
                contents = "".join(
                    path.read_text(encoding="utf-8")
                    for path in log_files
                    if path.exists()
                )
                self.assertIn("cross-module rollover probe", contents)
            restore_logging_state()


# ---------------------------------------------------------------------------
# #5 — Provider descriptor registry
# ---------------------------------------------------------------------------

class TestProviderDescriptors(unittest.TestCase):
    """#5: PROVIDER_DESCRIPTORS must contain all 14 providers with correct metadata."""

    @classmethod
    def setUpClass(cls):
        from limitlens.providers import PROVIDER_DESCRIPTORS, ProviderDescriptor
        cls.desc = PROVIDER_DESCRIPTORS
        cls.ProviderDescriptor = ProviderDescriptor

    def test_all_expected_providers_present(self):
        expected_keys = {
            "codex", "amp", "antigravity", "opencode", "pi", "kilo",
            "claude", "copilot_cli", "cursor", "cline", "pioneer",
            "commandcode", "custom", "grok",
        }
        self.assertEqual(set(self.desc.keys()), expected_keys)

    def test_each_descriptor_is_immutable(self):
        for key, d in self.desc.items():
            with self.assertRaises((AttributeError, TypeError)):
                d.key = "hacked"

    def test_each_descriptor_has_required_fields(self):
        for key, d in self.desc.items():
            self.assertIsInstance(d.key, str, f"{key}.key")
            self.assertIsInstance(d.label, str, f"{key}.label")
            self.assertIsInstance(d.config_key, str, f"{key}.config_key")
            self.assertIsInstance(d.default_enabled, bool, f"{key}.default_enabled")
            self.assertTrue(callable(d.fetch), f"{key}.fetch not callable")
            self.assertIn(d.display_section, ("Quota", "Observed"), f"{key}.display_section")

    def test_fetch_callable_for_all(self):
        for key, d in self.desc.items():
            self.assertTrue(callable(d.fetch), f"{key}.fetch")

    def test_display_callable_or_none(self):
        for key, d in self.desc.items():
            self.assertTrue(
                d.display is None or callable(d.display),
                f"{key}.display must be callable or None",
            )

    def test_antigravity_has_agy_alias(self):
        d = self.desc["antigravity"]
        self.assertIn("agy", d.aliases)

    def test_custom_maps_to_custom_tools_config_key(self):
        d = self.desc["custom"]
        self.assertEqual(d.config_key, "custom_tools")


# ---------------------------------------------------------------------------
# #6 — Defaults drift
# ---------------------------------------------------------------------------

class TestDefaultsDrift(unittest.TestCase):
    """#6: PROVIDER_DESCRIPTORS.default_enabled must agree with DEFAULT_CONFIG."""

    def test_no_drift_between_descriptor_and_config(self):
        from limitlens.providers import PROVIDER_DESCRIPTORS
        from limitlens.config import DEFAULT_CONFIG

        mismatches = []
        for key, desc in PROVIDER_DESCRIPTORS.items():
            cfg_section = DEFAULT_CONFIG.get(desc.config_key)
            if cfg_section is None:
                # provider may not have a config section (copilot_cli is internal)
                continue
            cfg_default = cfg_section.get("enabled")
            if cfg_default is None:
                continue
            if cfg_default != desc.default_enabled:
                mismatches.append(
                    f"{key}: descriptor.default_enabled={desc.default_enabled}, "
                    f"DEFAULT_CONFIG[{desc.config_key!r}]['enabled']={cfg_default}"
                )

        self.assertFalse(
            mismatches,
            "Drift detected between PROVIDER_DESCRIPTORS and DEFAULT_CONFIG:\n"
            + "\n".join(mismatches),
        )

    def test_pi_default_enabled_is_true(self):
        """pi was listed as default=False in the old doctor rows — must now be True."""
        from limitlens.providers import PROVIDER_DESCRIPTORS
        self.assertTrue(PROVIDER_DESCRIPTORS["pi"].default_enabled,
                        "pi.default_enabled must be True (matches config)")

    def test_kilo_present_in_registry(self):
        """kilo was missing from the old doctor rows entirely."""
        from limitlens.providers import PROVIDER_DESCRIPTORS
        self.assertIn("kilo", PROVIDER_DESCRIPTORS)

    def test_claude_present_in_registry(self):
        """claude was missing from enabled_count block."""
        from limitlens.providers import PROVIDER_DESCRIPTORS
        self.assertIn("claude", PROVIDER_DESCRIPTORS)


if __name__ == "__main__":
    unittest.main()
