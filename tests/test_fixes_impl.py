import unittest
from unittest.mock import patch
import os
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

from limitlens.core import file_lock
from limitlens import waste_tracker, usage_tracker
from limitlens.providers import observed
from limitlens.config import reset_custom_tool_spend

class TestFixesLocking(unittest.TestCase):
    def test_file_lock_acquired_and_released(self):
        temp_dir = tempfile.mkdtemp()
        try:
            lock_path = os.path.join(temp_dir, "test.lock")
            # First acquisition should succeed
            with file_lock(lock_path):
                self.assertTrue(os.path.exists(lock_path))
                # Reentrant acquisition should succeed
                with file_lock(lock_path):
                    self.assertTrue(os.path.exists(lock_path))
            # Lock should be cleaned up
            self.assertFalse(os.path.exists(lock_path))
        finally:
            shutil.rmtree(temp_dir)

    def test_file_lock_timeout(self):
        temp_dir = tempfile.mkdtemp()
        try:
            lock_path = os.path.join(temp_dir, "test.lock")
            os.mkdir(lock_path)  # Manually lock it

            # Trying to lock it now should fail/timeout
            with self.assertRaises(TimeoutError):
                with file_lock(lock_path, timeout=0.1, delay=0.01):
                    pass
        finally:
            shutil.rmtree(temp_dir)

    def test_file_lock_stale_cleanup(self):
        temp_dir = tempfile.mkdtemp()
        try:
            lock_path = os.path.join(temp_dir, "test.lock")
            os.mkdir(lock_path)
            # Make the lock directory look stale (older than 10 seconds)
            stale_time = datetime.now(timezone.utc).timestamp() - 20
            os.utime(lock_path, (stale_time, stale_time))

            # It should clean up the stale lock and successfully acquire it
            with file_lock(lock_path, timeout=0.1, delay=0.01):
                self.assertTrue(os.path.exists(lock_path))
        finally:
            shutil.rmtree(temp_dir)

class TestFixesTempCleanup(unittest.TestCase):
    @patch("json.dump", side_effect=OSError("Write failed"))
    def test_save_imported_data_temp_cleanup(self, mock_json_dump):
        temp_dir = tempfile.mkdtemp()
        try:
            test_path = os.path.join(temp_dir, "imported_usage.json")
            with patch("limitlens.usage_tracker.IMPORTED_USAGE_PATH", test_path):
                usage_tracker._save_imported_data({"test": 123})
                files = os.listdir(temp_dir)
                temp_files = [f for f in files if f.endswith(".tmp")]
                self.assertEqual(len(temp_files), 0)
        finally:
            shutil.rmtree(temp_dir)

    @patch("json.dump", side_effect=OSError("Write failed"))
    def test_mark_spend_reset_temp_cleanup(self, mock_json_dump):
        temp_dir = tempfile.mkdtemp()
        try:
            test_path = os.path.join(temp_dir, "spend_resets.json")
            with patch("limitlens.providers.observed.SPEND_RESETS_PATH", test_path):
                observed.mark_spend_reset("test_tool")
                files = os.listdir(temp_dir)
                temp_files = [f for f in files if f.endswith(".tmp")]
                self.assertEqual(len(temp_files), 0)
        finally:
            shutil.rmtree(temp_dir)

    @patch("json.dump", side_effect=OSError("Write failed"))
    def test_reset_custom_tool_spend_temp_cleanup(self, mock_json_dump):
        temp_dir = tempfile.mkdtemp()
        try:
            test_path = os.path.join(temp_dir, "config.json")
            with open(test_path, "w") as f:
                f.write('{"custom_tools": {"tools": {"tool1": {"used": 10}}}}')

            with self.assertRaises(Exception):
                reset_custom_tool_spend(test_path)

            files = os.listdir(temp_dir)
            temp_files = [f for f in files if f.endswith(".tmp")]
            self.assertEqual(len(temp_files), 0)
        finally:
            shutil.rmtree(temp_dir)

    @patch("os.replace", side_effect=OSError("Replace failed"))
    def test_merge_snapshots_temp_cleanup(self, mock_replace):
        temp_dir = tempfile.mkdtemp()
        try:
            test_path = os.path.join(temp_dir, "snapshots.jsonl")
            with patch("limitlens.waste_tracker.SNAPSHOT_PATH", test_path):
                waste_tracker.merge_snapshots([{"ts": "2023-01-01T00:00:00Z", "key": "k", "tool": "t"}])
                files = os.listdir(temp_dir)
                temp_files = [f for f in files if f.endswith(".tmp")]
                self.assertEqual(len(temp_files), 0)
        finally:
            shutil.rmtree(temp_dir)

class TestFixesDirectoryTraversal(unittest.TestCase):
    def test_get_pi_usage_directory_traversal(self):
        temp_dir = tempfile.mkdtemp()
        try:
            root = Path(temp_dir)
            now_ts = datetime.now(timezone.utc).timestamp()

            # Create subdirs
            (root / "dir1" / "dir2" / "dir3" / "dir4").mkdir(parents=True, exist_ok=True)
            (root / "dir_old").mkdir(parents=True, exist_ok=True)

            f1 = root / "file1.jsonl"
            f2 = root / "dir1" / "file2.jsonl"
            f3 = root / "dir1" / "dir2" / "file3.jsonl"
            f4 = root / "dir1" / "dir2" / "dir3" / "file4.jsonl"
            f5 = root / "dir1" / "dir2" / "dir3" / "dir4" / "file5.jsonl"
            f_old = root / "dir_old" / "file_old.jsonl"

            for f in [f1, f2, f3, f4, f5, f_old]:
                f.write_text('{"type": "message", "message": {"role": "assistant", "timestamp": "2026-06-14T00:00:00Z", "usage": {"total": 10}}}')

            # Set times: min_since_ts is now_ts - 100
            min_since_ts = now_ts - 100

            # Make dir_old have an old modification time (now_ts - 200)
            os.utime(str(root / "dir_old"), (now_ts - 200, now_ts - 200))

            config = {
                "pi": {
                    "enabled": True,
                    "sessions_dir": temp_dir,
                    "days": [1]
                }
            }

            since_dt = datetime.fromtimestamp(min_since_ts, timezone.utc)

            opened_paths = []
            original_open = Path.open

            def mock_open_file(self, *args, **kwargs):
                opened_paths.append(self)
                return original_open(self, *args, **kwargs)

            with patch.object(Path, "open", mock_open_file):
                with patch("limitlens.providers.observed.usage_window_start", return_value=since_dt):
                    observed.get_pi_usage(config)

            opened_names = [p.name for p in opened_paths]
            self.assertIn("file1.jsonl", opened_names)
            self.assertIn("file2.jsonl", opened_names)
            self.assertIn("file3.jsonl", opened_names)

            self.assertNotIn("file4.jsonl", opened_names)
            self.assertNotIn("file5.jsonl", opened_names)
            self.assertNotIn("file_old.jsonl", opened_names)

        finally:
            shutil.rmtree(temp_dir)
