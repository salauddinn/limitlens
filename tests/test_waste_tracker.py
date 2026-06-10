from datetime import datetime, timezone, timedelta
from unittest.mock import patch, mock_open, MagicMock

from limitlens import waste_tracker


def test_is_unlimited_model():
    assert waste_tracker._is_unlimited_model(None) is False
    assert waste_tracker._is_unlimited_model("") is False
    assert waste_tracker._is_unlimited_model("something") is False
    assert waste_tracker._is_unlimited_model("flash") is True
    assert waste_tracker._is_unlimited_model("Flash-1.5") is True
    assert waste_tracker._is_unlimited_model("other-model") is False


def test_reset_at_seconds():
    assert waste_tracker._reset_at_seconds(None) is None
    assert waste_tracker._reset_at_seconds(123) == 123.0
    assert waste_tracker._reset_at_seconds(123.45) == 123.45

    # ISO string with Z
    assert waste_tracker._reset_at_seconds("2023-01-01T12:00:00Z") == datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()

    # ISO string without tzinfo
    assert waste_tracker._reset_at_seconds("2023-01-01T12:00:00") == datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()

    # Invalid string
    assert waste_tracker._reset_at_seconds("invalid-time") is None

    # Invalid type
    assert waste_tracker._reset_at_seconds([]) is None


def test_is_reset_event():
    # pct_jump >= 30
    assert waste_tracker._is_reset_event({"pct_left": 10}, {"pct_left": 40}) is True
    assert waste_tracker._is_reset_event({"pct_left": 10}, {"pct_left": 39}) is False

    # Empty pct_left
    assert waste_tracker._is_reset_event({}, {"pct_left": 40}) is True
    assert waste_tracker._is_reset_event({"pct_left": 10}, {}) is False

    # reset_at moved forward by > 60 after deadline passed
    assert waste_tracker._is_reset_event(
        {"pct_left": 100, "reset_at": 100},
        {"pct_left": 100, "reset_at": 200, "ts": 150}
    ) is True

    # ts not passed previous reset_at
    assert waste_tracker._is_reset_event(
        {"pct_left": 100, "reset_at": 100},
        {"pct_left": 100, "reset_at": 200, "ts": 50}
    ) is False

    # reset_at didn't move enough
    assert waste_tracker._is_reset_event(
        {"pct_left": 100, "reset_at": 100},
        {"pct_left": 100, "reset_at": 150, "ts": 150}
    ) is False


def test_flatten_snapshot():
    result = {
        "codex": {
            "accounts": [
                {"name": "acc1", "limits": [{"label": "l1", "left_percent": 20, "reset_time": "2023-01-01T00:00:00Z"}]},
                {"name": "acc2", "error": "some error"},
                {"name": "acc3", "limits": [{"label": "l2"}]}
            ]
        },
        "antigravity": {
            "profiles": [
                {
                    "name": "prof1", "status": "running",
                    "models": [
                        {"label": "m1", "pct_left": 30, "reset_time": "2023-01-01T00:00:00Z"},
                        {"label": "flash", "pct_left": 10, "reset_time": "2023-01-01T00:00:00Z"},
                        {"label": "m2"}
                    ]
                },
                {"name": "prof2", "status": "stopped", "models": [{"label": "m3", "pct_left": 50}]}
            ]
        }
    }

    rows = waste_tracker._flatten_snapshot(result)
    assert len(rows) == 2

    # Check codex row
    codex_row = next(r for r in rows if r["tool"] == "codex")
    assert codex_row["key"] == "codex-acc1::l1"
    assert codex_row["pct_left"] == 20.0

    # Check antigravity row
    ag_row = next(r for r in rows if r["tool"] == "antigravity")
    assert ag_row["key"] == "antigravity:prof1::m1"
    assert ag_row["pct_left"] == 30.0


def test_flatten_snapshot_includes_amp_for_usage_tracking():
    result = {
        "amp": {
            "tiers": [
                {
                    "label": "amp-pro",
                    "remaining": 47.5,
                    "total": 50.0,
                    "used": 2.5,
                    "pct_left": 95.0,
                    "replenish_rate": 0.5,
                },
                {"label": "credits", "remaining": 10.0, "total": None, "pct_left": None},
            ]
        }
    }

    rows = waste_tracker._flatten_snapshot(result)

    assert len(rows) == 2
    first = rows[0]
    assert first["tool"] == "amp"
    assert first["key"] == "amp::amp-pro"
    assert first["remaining"] == 47.5
    assert first["total"] == 50.0
    assert first["used"] == 2.5
    assert first["pct_left"] == 95.0
    assert first["replenish_rate"] == 0.5


@patch("limitlens.waste_tracker.os.makedirs")
@patch("builtins.open", new_callable=mock_open)
def test_record_snapshot(mock_open_fn, mock_makedirs):
    result = {
        "codex": {
            "accounts": [
                {"name": "acc1", "limits": [{"label": "l1", "left_percent": 20, "reset_time": "2023-01-01T00:00:00Z"}]}
            ]
        }
    }
    waste_tracker.record_snapshot(result)
    mock_makedirs.assert_called_once()
    # record_snapshot opens the snapshot file to write AND may open the .pruned
    # temp file during pruning — assert at least one call happened and the
    # main snapshots.jsonl write was among them.
    assert mock_open_fn.call_count >= 1
    opened_paths = [str(c.args[0]) for c in mock_open_fn.call_args_list]
    assert any("snapshots.jsonl" in p and not p.endswith(".pruned") for p in opened_paths)

    # Test empty snapshot — nothing should be written
    mock_makedirs.reset_mock()
    mock_open_fn.reset_mock()
    waste_tracker.record_snapshot({})
    mock_makedirs.assert_not_called()
    mock_open_fn.assert_not_called()



@patch("limitlens.waste_tracker.os.makedirs", side_effect=OSError("Permission denied"))
def test_record_snapshot_oserror(mock_makedirs):
    result = {
        "codex": {
            "accounts": [
                {"name": "acc1", "limits": [{"label": "l1", "left_percent": 20, "reset_time": "2023-01-01T00:00:00Z"}]}
            ]
        }
    }
    # Should not raise exception
    waste_tracker.record_snapshot(result)


def test_parse_ts():
    dt = waste_tracker._parse_ts("2023-01-01T12:00:00Z")
    assert dt == datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)

    dt2 = waste_tracker._parse_ts("2023-01-01T12:00:00")
    assert dt2 == datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert waste_tracker._parse_ts("invalid") is None
    assert waste_tracker._parse_ts(None) is None


@patch("limitlens.waste_tracker.os.path.exists", return_value=False)
def test_load_snapshots_not_exists(mock_exists):
    assert waste_tracker._load_snapshots() == []


@patch("limitlens.waste_tracker.os.path.exists", return_value=True)
def test_load_snapshots_oserror(mock_exists):
    with patch("builtins.open", side_effect=OSError("Read error")):
        assert waste_tracker._load_snapshots() == []


@patch("limitlens.waste_tracker.os.path.exists", return_value=True)
def test_load_snapshots(mock_exists):
    mock_file_content = """{"ts": "2023-01-01T00:00:00Z", "key": "k1"}
{"invalid_json"
{"ts": "invalid_ts", "key": "k2"}
{"ts": "2023-01-02T00:00:00Z", "key": "k3"}
"""
    with patch("builtins.open", mock_open(read_data=mock_file_content)):
        since = datetime(2023, 1, 1, 12, 0, tzinfo=timezone.utc)
        rows = waste_tracker._load_snapshots(since=since)
        assert len(rows) == 1
        assert rows[0]["key"] == "k3"
        assert "_ts" in rows[0]

        # Test without since
        rows_all = waste_tracker._load_snapshots()
        assert len(rows_all) == 2


@patch("limitlens.waste_tracker.os.path.exists", return_value=True)
@patch("limitlens.waste_tracker.os.remove")
def test_reset_snapshots(mock_remove, mock_exists):
    assert waste_tracker.reset_snapshots() is True
    mock_remove.assert_called_once()


@patch("limitlens.waste_tracker.os.path.exists", return_value=False)
@patch("limitlens.waste_tracker.os.remove")
def test_reset_snapshots_not_exists(mock_remove, mock_exists):
    assert waste_tracker.reset_snapshots() is True
    mock_remove.assert_not_called()


@patch("limitlens.waste_tracker.os.path.exists", return_value=True)
@patch("limitlens.waste_tracker.os.remove", side_effect=OSError)
def test_reset_snapshots_error(mock_remove, mock_exists):
    assert waste_tracker.reset_snapshots() is False


@patch("limitlens.waste_tracker.os.path.exists", return_value=False)
def test_prune_old_snapshots_not_exists(mock_exists):
    waste_tracker.prune_old_snapshots()


@patch("limitlens.waste_tracker.os.path.exists", return_value=True)
@patch("limitlens.waste_tracker._load_snapshots")
@patch("limitlens.waste_tracker.os.replace")
def test_prune_old_snapshots(mock_replace, mock_load, mock_exists):
    mock_load.return_value = [{"key": "k1", "_ts": "fake", "ts": "something"}]
    with patch("builtins.open", mock_open()) as mock_file:
        waste_tracker.prune_old_snapshots()
        mock_file.assert_called_once()
        mock_file().write.assert_called_once_with('{"key": "k1", "ts": "something"}\n')
        mock_replace.assert_called_once()


@patch("limitlens.waste_tracker.os.path.exists", return_value=True)
@patch("limitlens.waste_tracker._load_snapshots")
@patch("limitlens.waste_tracker.os.replace", side_effect=OSError)
def test_prune_old_snapshots_error(mock_replace, mock_load, mock_exists):
    mock_load.return_value = []
    with patch("builtins.open", mock_open()):
        waste_tracker.prune_old_snapshots()


@patch("limitlens.waste_tracker._load_snapshots_with_anchor")
def test_compute_waste(mock_load):
    mock_load.return_value = []
    assert waste_tracker.compute_waste() == {}

    now = datetime.now(timezone.utc)
    t1 = now.replace(microsecond=0)
    t2 = t1 + timedelta(seconds=1)
    mock_load.return_value = [
        # Unlimited model should be ignored
        {"key": "antigravity:prof::flash", "ts": "t1", "_ts": t1, "pct_left": 100},

        # Amp is recorded for usage tracking but ignored for waste reports.
        {"key": "amp::amp-pro", "ts": "t1", "_ts": t1, "pct_left": 10, "tool": "amp"},
        {"key": "amp::amp-pro", "ts": "t2", "_ts": t2, "pct_left": 100, "tool": "amp"},

        # A valid codex reset (jump > 30)
        {"key": "codex-acc::l1", "ts": "t1", "_ts": t1, "pct_left": 10, "tool": "codex"},
        {"key": "codex-acc::l1", "ts": "t2", "_ts": t2, "pct_left": 50, "tool": "codex"},

        # A valid antigravity reset above threshold
        {"key": "antigravity:prof::m1", "ts": "t1", "_ts": t1, "pct_left": 30, "tool": "antigravity"},
        {"key": "antigravity:prof::m1", "ts": "t2", "_ts": t2, "pct_left": 100, "tool": "antigravity"},

        # Antigravity reset below threshold (ignored)
        {"key": "antigravity:prof::m2", "ts": "t1", "_ts": t1, "pct_left": 15, "tool": "antigravity"},
        {"key": "antigravity:prof::m2", "ts": "t2", "_ts": t2, "pct_left": 100, "tool": "antigravity"},
    ]

    result = waste_tracker.compute_waste()

    assert "antigravity:prof::flash" not in result
    assert "antigravity:prof::m2" not in result
    assert "amp::amp-pro" not in result

    assert "codex-acc::l1" in result
    assert result["codex-acc::l1"]["reset_count"] == 1
    assert result["codex-acc::l1"]["avg_wasted_pct"] == 10.0
    assert result["codex-acc::l1"]["max_wasted_pct"] == 10.0

    assert "antigravity:prof::m1" in result
    assert result["antigravity:prof::m1"]["reset_count"] == 1
    assert result["antigravity:prof::m1"]["avg_wasted_pct"] == 30.0


@patch("limitlens.waste_tracker._load_snapshots_with_anchor")
def test_compute_waste_filters_ignored_codex_accounts(mock_load):
    now = datetime.now(timezone.utc)
    t1 = now.replace(microsecond=0)
    t2 = t1 + timedelta(seconds=1)
    mock_load.return_value = [
        {"key": "codex-default::weekly", "ts": "t1", "_ts": t1, "pct_left": 10, "tool": "codex"},
        {"key": "codex-default::weekly", "ts": "t2", "_ts": t2, "pct_left": 80, "tool": "codex"},
        {"key": "codex-p1::weekly", "ts": "t1", "_ts": t1, "pct_left": 20, "tool": "codex"},
        {"key": "codex-p1::weekly", "ts": "t2", "_ts": t2, "pct_left": 90, "tool": "codex"},
    ]

    result = waste_tracker.compute_waste(config={"codex": {"ignored_accounts": ["~/.codex"]}})

    assert "codex-default::weekly" not in result
    assert "codex-p1::weekly" in result


def _snapshot(key, ts, pct_left, tool="codex", reset_at=None):
    row = {
        "key": key,
        "tool": tool,
        "ts": ts.isoformat(),
        "_ts": ts,
        "pct_left": pct_left,
    }
    if reset_at is not None:
        row["reset_at"] = reset_at
    return row


@patch("limitlens.waste_tracker._load_snapshots_with_anchor")
def test_compute_waste_detects_reset_when_previous_snapshot_before_since(mock_load):
    now = datetime.now(timezone.utc)
    anchor = now - timedelta(days=1, minutes=5)
    current = now - timedelta(hours=12)
    mock_load.return_value = [
        _snapshot("codex-acc::weekly", anchor, 10),
        _snapshot("codex-acc::weekly", current, 80),
    ]

    result = waste_tracker.compute_waste(days=1)

    mock_load.assert_called_once()
    assert result["codex-acc::weekly"]["reset_count"] == 1
    assert result["codex-acc::weekly"]["events"] == [
        {"at": current.isoformat(), "wasted_pct": 10.0}
    ]


@patch("limitlens.waste_tracker._load_snapshots_with_anchor")
def test_compute_waste_does_not_count_event_when_current_snapshot_before_since(mock_load):
    now = datetime.now(timezone.utc)
    anchor = now - timedelta(days=2)
    before_since = now - timedelta(days=1, minutes=5)
    mock_load.return_value = [
        _snapshot("codex-acc::weekly", anchor, 10),
        _snapshot("codex-acc::weekly", before_since, 80),
    ]

    assert waste_tracker.compute_waste(days=1) == {}


@patch("limitlens.waste_tracker._load_snapshots_with_anchor")
def test_compute_waste_detects_reset_at_reset_across_boundary(mock_load):
    now = datetime.now(timezone.utc)
    anchor = now - timedelta(days=1, minutes=5)
    current = now - timedelta(hours=12)
    prev_reset = (now - timedelta(hours=18)).timestamp()
    curr_reset = (now + timedelta(days=6)).timestamp()
    mock_load.return_value = [
        _snapshot("codex-acc::weekly", anchor, 100, reset_at=prev_reset),
        _snapshot("codex-acc::weekly", current, 100, reset_at=curr_reset),
    ]

    result = waste_tracker.compute_waste(days=1)

    assert result["codex-acc::weekly"]["reset_count"] == 1
    assert result["codex-acc::weekly"]["avg_wasted_pct"] == 100.0


@patch("limitlens.waste_tracker._load_snapshots_with_anchor")
def test_compute_waste_detects_pct_jump_reset_across_boundary(mock_load):
    now = datetime.now(timezone.utc)
    anchor = now - timedelta(days=1, minutes=5)
    current = now - timedelta(hours=12)
    mock_load.return_value = [
        _snapshot("codex-acc::weekly", anchor, 25),
        _snapshot("codex-acc::weekly", current, 90),
    ]

    result = waste_tracker.compute_waste(days=1)

    assert result["codex-acc::weekly"]["reset_count"] == 1
    assert result["codex-acc::weekly"]["avg_wasted_pct"] == 25.0


@patch("limitlens.waste_tracker._load_snapshots_with_anchor")
def test_compute_waste_anchor_does_not_create_duplicate_events(mock_load):
    now = datetime.now(timezone.utc)
    anchor = now - timedelta(days=1, minutes=5)
    current = now - timedelta(hours=12)
    later = now - timedelta(hours=6)
    mock_load.return_value = [
        _snapshot("codex-acc::weekly", anchor, 10),
        _snapshot("codex-acc::weekly", current, 80),
        _snapshot("codex-acc::weekly", later, 70),
    ]

    result = waste_tracker.compute_waste(days=1)

    assert result["codex-acc::weekly"]["reset_count"] == 1
    assert result["codex-acc::weekly"]["events"] == [
        {"at": current.isoformat(), "wasted_pct": 10.0}
    ]


@patch("limitlens.waste_tracker._load_snapshots")
def test_load_snapshots_with_anchor_returns_latest_pre_since_row_per_key(mock_load):
    since = datetime(2024, 1, 2, tzinfo=timezone.utc)
    old_anchor = _snapshot("codex-acc::weekly", since - timedelta(days=2), 40)
    latest_anchor = _snapshot("codex-acc::weekly", since - timedelta(minutes=1), 20)
    in_window = _snapshot("codex-acc::weekly", since + timedelta(minutes=1), 90)
    other_anchor = _snapshot("codex-other::weekly", since - timedelta(minutes=1), 30)
    mock_load.return_value = [old_anchor, latest_anchor, in_window, other_anchor]

    rows = waste_tracker._load_snapshots_with_anchor(since)

    assert rows == [latest_anchor, other_anchor, in_window]


def test_verdict():
    assert waste_tracker._verdict(65) == "heavy waste"
    assert waste_tracker._verdict(35) == "wasting some"
    assert waste_tracker._verdict(15) == "ok"
    assert waste_tracker._verdict(5) == "well used"


def test_display_waste_report(capsys):
    args = MagicMock()
    args.no_color = False

    def dummy_print_c(text, color, no_color):
        if no_color:
            print(text)
        else:
            print(f"{color}{text}\033[0m")

    # Test empty
    waste_tracker.display_waste_report({}, 7, args, dummy_print_c)
    out, err = capsys.readouterr()
    assert "Waste Report" in out
    assert "no reset events recorded yet" in out

    # Test with data
    report = {
        "k1": {"avg_wasted_pct": 65, "max_wasted_pct": 70, "reset_count": 2},
        "k2": {"avg_wasted_pct": 35, "max_wasted_pct": 40, "reset_count": 1},
        "k3": {"avg_wasted_pct": 15, "max_wasted_pct": 20, "reset_count": 3},
    }

    waste_tracker.display_waste_report(report, 7, args, dummy_print_c)
    out, err = capsys.readouterr()
    assert "k1" in out
    assert "heavy waste" in out

    # Test with no_color=True
    args.no_color = True
    waste_tracker.display_waste_report(report, 7, args, dummy_print_c)
    out, err = capsys.readouterr()
    assert "\033[" not in out


def test_display_waste_report_formats_amp_dollar_waste(capsys):
    args = MagicMock()
    args.no_color = True
    args.verbose = True

    def dummy_print_c(text, color, no_color):
        print(text)

    report = {
        "amp::amp-pro": {
            "waste_unit": "usd",
            "reset_count": 1,
            "avg_wasted_usd": 2.5,
            "max_wasted_usd": 2.5,
            "total_wasted_usd": 2.5,
            "avg_wasted_pct": 5.0,
            "max_wasted_pct": 5.0,
            "last_seen_at": "t2",
            "events": [{"at": "t2", "wasted_usd": 2.5, "estimated": True}],
        }
    }

    waste_tracker.display_waste_report(report, 7, args, dummy_print_c)
    out, err = capsys.readouterr()

    assert "$ 2.50 missed refill avg" in out
    assert "$2.50 missed refill" in out
    assert "None% unused" not in out


def test_print_setup_hint():
    args = MagicMock()
    args.no_color = True
    prints = []

    def print_c(text, color, no_color):
        prints.append(text)

    waste_tracker._print_setup_hint(args, print_c)
    assert len(prints) == 3
    assert "Tip: snapshots are recorded on every" in prints[0]
