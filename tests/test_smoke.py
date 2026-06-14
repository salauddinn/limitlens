"""
Smoke tests: invoke limitlens as a subprocess and verify basic correctness.

These tests run the installed/editable package via `python -m limitlens`
to catch integration-level regressions (import errors, missing flags, broken
JSON output) that unit tests can miss.
"""
import json
import subprocess
import sys
import unittest


def _run(*extra_args, timeout=30):
    cmd = [sys.executable, "-m", "limitlens"] + list(extra_args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestSmokeVersion(unittest.TestCase):
    def test_version_flag_exits_zero_and_prints_version(self):
        result = _run("--version", timeout=10)
        combined = result.stdout + result.stderr
        self.assertIn("limitlens", combined.lower())

    def test_help_flag(self):
        result = _run("--help", timeout=10)
        self.assertEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertIn("--json", combined)


class TestSmokeJSON(unittest.TestCase):
    def test_json_output_is_valid_dict(self):
        result = _run("--json", "--no-record")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, dict)

    def test_json_output_has_schema_version(self):
        result = _run("--json", "--no-record")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("schema_version", data)
        self.assertEqual(data["schema_version"], 1)

    def test_reco_json_has_tier_keys(self):
        result = _run("--json", "--no-record", "--reco")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, dict)
        for key in ("hard", "quick", "cli"):
            self.assertIn(key, data)

    def test_waste_json_is_dict(self):
        result = _run("--json", "--no-record", "--waste")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, dict)

    def test_usage_json_is_dict(self):
        result = _run("--json", "--no-record", "--usage")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, dict)

    def test_tool_filter_json(self):
        result = _run("--json", "--no-record", "--tool", "codex")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        data = json.loads(result.stdout)
        self.assertIsInstance(data, dict)


class TestSmokeInvalidArgs(unittest.TestCase):
    def test_invalid_interval_exits_nonzero(self):
        result = _run("--watch", "--interval", "-1", timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_unknown_flag_exits_nonzero(self):
        result = _run("--not-a-real-flag", timeout=10)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
