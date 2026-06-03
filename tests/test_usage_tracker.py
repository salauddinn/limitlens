import unittest
from unittest.mock import patch, mock_open
from datetime import datetime, timezone

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

    @patch('limitlens.usage_tracker.waste_tracker._load_snapshots')
    def test_compute_daily_usage(self, mock_load_snapshots):
        # Provide some snapshots
        dt1 = datetime(2023, 10, 1, 10, 0, tzinfo=timezone.utc)
        dt2 = datetime(2023, 10, 1, 11, 0, tzinfo=timezone.utc)
        dt3 = datetime(2023, 10, 2, 9, 0, tzinfo=timezone.utc)
        
        mock_load_snapshots.return_value = [
            {"key": "codex-foo", "pct_left": 100.0, "_ts": dt1, "reset_at": None},
            {"key": "codex-foo", "pct_left": 80.0, "_ts": dt2, "reset_at": None}, # usage 20
            {"key": "codex-foo", "pct_left": 90.0, "_ts": dt3, "reset_at": dt3.timestamp() + 3600}, # reset occurred, usage 10
        ]
        
        with patch('limitlens.usage_tracker.waste_tracker._is_reset_event', side_effect=[False, True]):
            history = usage_tracker.compute_daily_usage()
            
        self.assertIn("2023-10-01", history)
        self.assertIn("2023-10-02", history)
        self.assertEqual(history["2023-10-01"]["codex-foo"], 20.0)
        self.assertEqual(history["2023-10-02"]["codex-foo"], 10.0)

    @patch('limitlens.usage_tracker.compute_daily_usage')
    @patch('limitlens.usage_tracker._load_imported_data')
    def test_get_merged_history(self, mock_imported, mock_live):
        mock_live.return_value = {"2023-10-01": {"codex": 20.0}}
        mock_imported.return_value = {"2023-10-01": {"codex": 15.0, "other": 5.0}, "2023-10-02": {"codex": 30.0}}
        
        merged = usage_tracker._get_merged_history()
        self.assertEqual(merged["2023-10-01"]["codex"], 20.0) # max wins
        self.assertEqual(merged["2023-10-01"]["other"], 5.0)
        self.assertEqual(merged["2023-10-02"]["codex"], 30.0)

    @patch('limitlens.usage_tracker._get_merged_history')
    @patch('builtins.open', new_callable=mock_open)
    def test_export_usage(self, mock_file, mock_merged):
        mock_merged.return_value = {"2023-10-01": {"codex": 10.0}}
        res = usage_tracker.export_usage("dummy.json")
        self.assertTrue(res)
        mock_file.assert_called_once()
        # verify dump
        written = "".join(call[0][0] for call in mock_file().write.call_args_list)
        self.assertIn('"codex": 10.0', written)

    @patch('builtins.open', new_callable=mock_open, read_data='{"history": {"2023-10-01": {"codex": 50.0}}}')
    @patch('limitlens.usage_tracker._load_imported_data', return_value={"2023-10-01": {"codex": 10.0}})
    @patch('limitlens.usage_tracker._save_imported_data')
    def test_import_usage(self, mock_save, mock_load, mock_file):
        res = usage_tracker.import_usage("dummy.json")
        self.assertTrue(res)
        mock_save.assert_called_once_with({"2023-10-01": {"codex": 50.0}}) # 50 > 10

if __name__ == '__main__':
    unittest.main()
