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
    @patch('tempfile.mkstemp', return_value=(3, "/tmp/usage_test.tmp"))
    @patch('os.fdopen', new_callable=mock_open)
    def test_save_imported_data(self, mock_fdopen, mock_mkstemp, mock_replace, mock_makedirs):
        usage_tracker._save_imported_data({"2023-10-01": {"codex": 10.0}})
        mock_makedirs.assert_called_once()
        mock_mkstemp.assert_called_once()
        mock_fdopen.assert_called_once()
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
    def test_compute_consolidated_usage_filters_ignored_codex_accounts(self, mock_load_snapshots):
        now = datetime.now(timezone.utc)
        mock_load_snapshots.return_value = [
            {"tool": "codex", "key": "codex-default::weekly", "pct_left": 90.0, "_ts": now - timedelta(hours=2)},
            {"tool": "codex", "key": "codex-default::weekly", "pct_left": 70.0, "_ts": now - timedelta(hours=1)},
            {"tool": "codex", "key": "codex-p1::weekly", "pct_left": 90.0, "_ts": now - timedelta(hours=2)},
            {"tool": "codex", "key": "codex-p1::weekly", "pct_left": 80.0, "_ts": now - timedelta(hours=1)},
        ]

        usage = usage_tracker.compute_consolidated_usage(
            days=1,
            config={"codex": {"ignored_accounts": ["~/.codex"]}},
        )

        self.assertNotIn("codex-default::weekly", usage)
        self.assertEqual(usage["codex-p1::weekly"], 10.0)

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

    @patch('limitlens.usage_tracker.compute_consolidated_usage')
    @patch('limitlens.usage_tracker.waste_tracker.compute_waste')
    def test_compute_usage_analytics_basic_structure(self, mock_waste, mock_usage):
        mock_usage.return_value = {"codex-foo::weekly": 30.0, "amp::amp-pro": 2.5}
        mock_waste.return_value = {
            "codex-foo::weekly": {"reset_count": 1, "avg_wasted_pct": 10.0, "events": []}
        }
        observed = {
            "opencode": {
                "windows": [
                    {
                        "days": 7,
                        "models": [
                            {
                                "provider": "anthropic",
                                "model": "claude",
                                "requests": 2,
                                "cost": 1.25,
                                "tokens": {"total": 100, "input": 40, "output": 60},
                            }
                        ],
                    }
                ]
            }
        }

        analytics = usage_tracker.compute_usage_analytics(days=7, observed=observed)

        self.assertEqual(analytics["metadata"]["version"], usage_tracker.ANALYTICS_VERSION)
        self.assertEqual(analytics["metadata"]["days"], 7)
        self.assertIn("generated_at", analytics["metadata"])
        self.assertEqual(analytics["snapshot_usage"]["codex-foo::weekly"]["unit"], "percent")
        self.assertEqual(analytics["snapshot_usage"]["codex-foo::weekly"]["tool"], "codex")
        self.assertEqual(analytics["snapshot_usage"]["amp::amp-pro"]["unit"], "usd")
        self.assertEqual(analytics["snapshot_usage"]["amp::amp-pro"]["used"], 2.5)
        self.assertEqual(analytics["waste"]["codex-foo::weekly"]["key"], "codex-foo::weekly")
        self.assertEqual(analytics["waste"]["codex-foo::weekly"]["tool"], "codex")
        self.assertEqual(analytics["waste"]["codex-foo::weekly"]["reset_count"], 1)
        self.assertEqual(analytics["observed"], observed)
        self.assertEqual(analytics["totals"]["snapshot_usage"]["percent"], 30.0)
        self.assertEqual(analytics["totals"]["snapshot_usage"]["usd"], 2.5)
        self.assertEqual(analytics["totals"]["observed"]["requests"], 2)
        self.assertEqual(analytics["totals"]["observed"]["cost"], 1.25)
        self.assertEqual(analytics["totals"]["observed"]["tokens"]["total"], 100)

    @patch('limitlens.usage_tracker.compute_consolidated_usage', return_value={"amp::amp-pro": 2.5})
    @patch('limitlens.usage_tracker.waste_tracker.compute_waste', return_value={})
    def test_compute_usage_analytics_amp_usage_remains_dollar_based(self, mock_waste, mock_usage):
        analytics = usage_tracker.compute_usage_analytics(days=7)

        amp = analytics["snapshot_usage"]["amp::amp-pro"]
        self.assertEqual(amp["used"], 2.5)
        self.assertEqual(amp["unit"], "usd")
        self.assertEqual(analytics["totals"]["snapshot_usage"]["usd"], 2.5)

    @patch('limitlens.usage_tracker.compute_consolidated_usage', return_value={"codex-foo::weekly": 30.0})
    @patch('limitlens.usage_tracker.waste_tracker.compute_waste', return_value={})
    def test_compute_usage_analytics_normal_quota_usage_remains_percent_based(self, mock_waste, mock_usage):
        analytics = usage_tracker.compute_usage_analytics(days=7)

        quota = analytics["snapshot_usage"]["codex-foo::weekly"]
        self.assertEqual(quota["used"], 30.0)
        self.assertEqual(quota["unit"], "percent")
        self.assertEqual(analytics["totals"]["snapshot_usage"]["percent"], 30.0)

    @patch('limitlens.usage_tracker._get_merged_history', return_value={"2023-10-01": {"codex": 50.0}})
    @patch('limitlens.usage_tracker.waste_tracker._load_snapshots')
    @patch('builtins.open', new_callable=mock_open)
    def test_export_usage(self, mock_file, mock_snapshots, mock_history):
        mock_snapshots.return_value = [{"key": "codex", "pct_left": 10.0, "_ts": "internal"}]
        res = usage_tracker.export_usage("dummy.json")
        self.assertTrue(res)
        mock_file.assert_any_call("dummy.json", "w", encoding="utf-8")
        # verify dump
        written = "".join(call[0][0] for call in mock_file().write.call_args_list)
        self.assertIn('"codex"', written)
        self.assertIn('"history"', written)
        self.assertIn('"imported_history"', written)
        self.assertIn('"snapshots"', written)
        self.assertNotIn('_ts', written)
        mock_history.assert_called_once_with(days=None)

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
        mock_save.assert_called_once_with({"2023-10-01": {"codex": 50.0}}) # max-merge keeps imports idempotent

    @patch('limitlens.usage_tracker.waste_tracker.merge_snapshots', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='{"version": 3, "snapshots": [{"key": "c"}], "history": {"2023-10-01": {"codex": 50.0}}, "imported_history": {"2023-09-01": {"legacy": 5.0}}}')
    @patch('limitlens.usage_tracker._load_imported_data', return_value={})
    @patch('limitlens.usage_tracker._save_imported_data')
    def test_import_usage_v3_preserves_only_imported_history_with_snapshots(self, mock_save, mock_load, mock_file, mock_merge):
        res = usage_tracker.import_usage("dummy.json")
        self.assertTrue(res)
        mock_merge.assert_called_once_with([{"key": "c"}])
        mock_save.assert_called_once_with({"2023-09-01": {"legacy": 5.0}})

    @patch('limitlens.usage_tracker.waste_tracker.merge_snapshots', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='{"version": 3, "snapshots": [{"key": "c"}], "history": {"2023-10-01": {"codex": 50.0}}}')
    @patch('limitlens.usage_tracker._load_imported_data', return_value={})
    @patch('limitlens.usage_tracker._save_imported_data')
    def test_import_usage_v3_does_not_double_count_derived_history(self, mock_save, mock_load, mock_file, mock_merge):
        res = usage_tracker.import_usage("dummy.json")
        self.assertTrue(res)
        mock_merge.assert_called_once_with([{"key": "c"}])
        mock_save.assert_called_once_with({})

if __name__ == '__main__':
    unittest.main()
