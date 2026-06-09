import unittest
from unittest.mock import patch, mock_open
from datetime import datetime, timezone, timedelta

from limitlens import usage_tracker

class TestUsageTracker(unittest.TestCase):

    @patch('limitlens.usage_tracker.os.path.exists', return_value=False)
    def test_load_imported_data_not_exists(self, mock_exists):
        data = usage_tracker._load_imported_data()
        self.assertEqual(data, {})

    @patch('limitlens.usage_tracker.os.path.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='{"2023-10-01": {"codex": 10.0}}')
    def test_load_imported_data_exists(self, mock_file, mock_exists):
        data = usage_tracker._load_imported_data()
        self.assertEqual(data, {"2023-10-01": {"codex": 10.0}})

    @patch('limitlens.usage_tracker.os.makedirs')
    @patch('limitlens.usage_tracker.os.replace')
    @patch('builtins.open', new_callable=mock_open)
    def test_save_imported_data(self, mock_file, mock_replace, mock_makedirs):
        usage_tracker._save_imported_data({"2023-10-01": {"codex": 10.0}})
        mock_makedirs.assert_called_once()
        mock_file.assert_called_once()
        mock_replace.assert_called_once()

    @patch('limitlens.usage_tracker.waste_tracker._load_snapshots_with_anchor')
    def test_compute_consolidated_usage(self, mock_load_snapshots):
        now = datetime.now(timezone.utc)
        dt1 = now - timedelta(days=2)
        dt2 = now - timedelta(days=1)
        dt3 = now

        mock_load_snapshots.return_value = [
            {"key": "codex-foo", "pct_left": 100.0, "_ts": dt1, "reset_at": None},
            {"key": "codex-foo", "pct_left": 80.0, "_ts": dt2, "reset_at": None}, # usage 20
            {"key": "codex-foo", "pct_left": 90.0, "_ts": dt3, "reset_at": dt3.timestamp() + 3600}, # reset occurred, usage 10
        ]

        with patch('limitlens.usage_tracker.waste_tracker._is_reset_event', side_effect=[False, True]):
            usage = usage_tracker.compute_consolidated_usage(days=3)

        self.assertEqual(usage["codex-foo"], 30.0)

    @patch('limitlens.usage_tracker.waste_tracker._load_snapshots_with_anchor')
    def test_compute_consolidated_usage_amp_dollars(self, mock_load_snapshots):
        now = datetime.now(timezone.utc)
        dt1 = now - timedelta(days=2)
        dt2 = now - timedelta(days=1)
        dt3 = now

        mock_load_snapshots.return_value = [
            {"tool": "amp", "key": "amp::amp-pro", "remaining": 50.0, "_ts": dt1},
            {"tool": "amp", "key": "amp::amp-pro", "remaining": 47.5, "_ts": dt2},
            {"tool": "amp", "key": "amp::amp-pro", "remaining": 49.0, "_ts": dt3},  # refill ignored
        ]

        usage = usage_tracker.compute_consolidated_usage(days=3)

        self.assertEqual(usage["amp::amp-pro"], 2.5)

    @patch('limitlens.usage_tracker.waste_tracker._load_snapshots')
    @patch('builtins.open', new_callable=mock_open)
    def test_export_usage(self, mock_file, mock_snapshots):
        mock_snapshots.return_value = [{"key": "codex", "pct_left": 10.0, "_ts": "internal"}]
        res = usage_tracker.export_usage("dummy.json")
        self.assertTrue(res)
        mock_file.assert_called_once_with("dummy.json", "w", encoding="utf-8")
        # verify dump
        written = "".join(call[0][0] for call in mock_file().write.call_args_list)
        self.assertIn('"codex"', written)
        self.assertNotIn('_ts', written)

    @patch('limitlens.usage_tracker.waste_tracker.merge_snapshots')
    @patch('builtins.open', new_callable=mock_open, read_data='{"version": 3, "snapshots": [{"key": "c"}]}')
    def test_import_usage_snapshots(self, mock_file, mock_merge):
        mock_merge.return_value = True
        res = usage_tracker.import_usage("dummy.json")
        self.assertTrue(res)
        mock_merge.assert_called_once()

    @patch('builtins.open', new_callable=mock_open, read_data='{"history": {"2023-10-01": {"codex": 50.0}}}')
    @patch('limitlens.usage_tracker._load_imported_data', return_value={"2023-10-01": {"codex": 10.0}})
    @patch('limitlens.usage_tracker._save_imported_data')
    def test_import_usage_legacy(self, mock_save, mock_load, mock_file):
        res = usage_tracker.import_usage("dummy.json")
        self.assertTrue(res)
        mock_save.assert_called_once_with({"2023-10-01": {"codex": 60.0}}) # 50 + 10 = 60

if __name__ == '__main__':
    unittest.main()
