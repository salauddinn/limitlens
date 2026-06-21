# LimitLens 1.5.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship LimitLens 1.5.0 as a stability-first CLI UX release with safer config writes, easier commands, clearer AI suggestions, day-wise usage, and a plain display mode.

**Architecture:** Keep the existing argparse CLI and provider renderers. Add thin argument normalization for command aliases, shared config-write helpers for atomic updates/backups, and targeted output improvements without a renderer rewrite. Preserve existing flags and test coverage while adding focused regression tests for the new workflows.

**Tech Stack:** Python 3.9+, argparse, pytest/unittest, standard-library JSON/file APIs, existing LimitLens modules (`limitlens.cli`, `limitlens.config`, `limitlens.usage_tracker`, `limitlens.recommendations`, `limitlens.core`).

---

## File structure

- Modify `limitlens/cli.py`: argument aliases, short flags, `--plain`, command normalization, smokeable help text, routing for suggestion/usage/all/watch.
- Modify `limitlens/core.py`: shared plain/no-icon helpers where safe, ANSI stripping helper if needed, optional icon formatting helpers.
- Modify `limitlens/config.py`: atomic JSON writes, timestamped backups, safer custom-tool reset write path.
- Modify `limitlens/usage_tracker.py`: daily usage text/export improvements and approximate labeling.
- Modify `limitlens/recommendations.py`: clearer suggestion reason formatting if recommendation display currently lives there.
- Modify provider display call sites only where necessary for `--plain` or visibility; do not rewrite all providers.
- Modify `pyproject.toml` and `limitlens/__init__.py`: bump version to `1.5.0` if both exist.
- Modify `CHANGELOG.md`: add 1.5.0 notes.
- Test files: add focused tests to existing relevant files (`tests/test_cli.py`, `tests/test_config.py`, `tests/test_usage_tracker.py`, `tests/test_recommendations.py`) unless a new single `tests/test_v1_5_0_features.py` keeps tests clearer.

---

### Task 1: Establish baseline and locate exact implementation points

**Files:**
- Read: `limitlens/cli.py`
- Read: `limitlens/config.py`
- Read: `limitlens/usage_tracker.py`
- Read: `limitlens/recommendations.py`
- Read: `tests/test_cli.py`
- Read: `tests/test_config.py`

- [ ] **Step 1: Run full baseline tests**

Run:

```bash
.venv/bin/python -m pytest
```

Expected: all current tests pass.

- [ ] **Step 2: Inspect existing CLI routing**

Run:

```bash
rg -n "add_argument|parse_args|args\.reco|args\.usage|args\.all|args\.watch|export_usage|import_usage" limitlens/cli.py tests/test_cli.py
```

Expected: identify the smallest location to normalize aliases after parsing and before business logic.

- [ ] **Step 3: Inspect config write paths**

Run:

```bash
rg -n "json\.dump|os\.replace|NamedTemporaryFile|mkstemp|reset_custom_tool_spend|limitlens_config_path" limitlens/config.py limitlens/cli.py tests/test_config.py
```

Expected: identify every config write touched by reset-spend and whether it is already atomic.

- [ ] **Step 4: Do not commit**

No code changes in this task.

---

### Task 2: Config safety helpers and backup-before-reset

**Files:**
- Modify: `limitlens/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for atomic config write and backup**

Add tests to `tests/test_config.py` using temporary directories. Include this behavior:

```python
def test_reset_custom_tool_spend_creates_backup_and_preserves_unrelated_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "amp": {"enabled": False, "individual_credits": True},
        "custom_tools": {
            "enabled": True,
            "tools": {
                "demo": {"name": "Demo", "used": 12, "request_count": 3, "total": 100}
            },
        },
    }), encoding="utf-8")

    assert reset_custom_tool_spend(str(config_path)) is True

    updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated["amp"] == {"enabled": False, "individual_credits": True}
    assert updated["custom_tools"]["tools"]["demo"]["used"] == 0
    assert updated["custom_tools"]["tools"]["demo"]["request_count"] == 0

    backups = list(tmp_path.glob("config.backup.*.json"))
    assert len(backups) == 1
    backup = json.loads(backups[0].read_text(encoding="utf-8"))
    assert backup["custom_tools"]["tools"]["demo"]["used"] == 12
```

Also add a failure-preservation test that monkeypatches `os.replace` in `limitlens.config` to raise `OSError` and asserts the original file remains unchanged.

- [ ] **Step 2: Run focused tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Expected: new backup/atomic tests fail before implementation.

- [ ] **Step 3: Implement `atomic_write_json` and `backup_file`**

In `limitlens/config.py`, add small helpers near config write functions:

```python
def atomic_write_json(path, data):
    import tempfile
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".config.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def backup_file(path):
    import shutil
    from datetime import datetime
    if not os.path.exists(path):
        return None
    directory = os.path.dirname(path) or "."
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(directory, f"config.backup.{stamp}.json")
    counter = 1
    while os.path.exists(backup_path):
        backup_path = os.path.join(directory, f"config.backup.{stamp}.{counter}.json")
        counter += 1
    shutil.copy2(path, backup_path)
    return backup_path
```

Update `reset_custom_tool_spend` to call `backup_file(config_path)` only immediately before an actual write, and replace ad-hoc temp writing with `atomic_write_json(config_path, user_config)`.

- [ ] **Step 4: Run focused config tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py -q
```

Expected: pass.

- [ ] **Step 5: Commit config safety**

```bash
git add limitlens/config.py tests/test_config.py
git commit -m "fix: make config reset writes safer"
```

---

### Task 3: CLI aliases and low-conflict short flags

**Files:**
- Modify: `limitlens/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for aliases**

Add tests that patch provider collection/display enough to avoid network/local-provider dependence. Assert parsed forms are equivalent:

```python
@pytest.mark.parametrize(("argv", "expected"), [
    (["suggest"], "reco"),
    (["s"], "reco"),
    (["usage"], "usage"),
    (["u"], "usage"),
    (["all"], "all"),
    (["a"], "all"),
])
def test_common_command_aliases_route_to_existing_modes(monkeypatch, capsys, argv, expected):
    # Patch the CLI's expensive provider/recommendation paths with deterministic stubs.
    # Invoke cli.main with monkeypatched sys.argv.
    # Assert output or patched function call proves the expected mode ran.
```

Also add tests for `-u`, `-a`, `-w`, and assert `-s` is not accepted.

- [ ] **Step 2: Run focused CLI tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: new alias tests fail.

- [ ] **Step 3: Implement alias normalization**

In `limitlens/cli.py`, add a positional optional command before known flags:

```python
parser.add_argument("command", nargs="?", choices=["suggest", "s", "usage", "u", "all", "a", "watch", "w"], help=argparse.SUPPRESS)
parser.add_argument("--reco", action="store_true", help="Only print the AI suggestion")
parser.add_argument("-u", "--usage", action="store_true", help="Show usage tracking history")
parser.add_argument("-a", "--all", action="store_true", help="Show all limits, bypassing auto-hide rules")
parser.add_argument("-w", "--watch", action="store_true", help="Refresh continuously for live status updates")
```

After `args = parser.parse_args()`:

```python
if args.command in ("suggest", "s"):
    args.reco = True
elif args.command in ("usage", "u"):
    args.usage = True
elif args.command in ("all", "a"):
    args.all = True
elif args.command in ("watch", "w"):
    args.watch = True
```

If existing `--usage`, `--all`, or `--watch` add_argument lines already exist, modify them rather than duplicating.

- [ ] **Step 4: Improve help examples minimally**

Use `argparse.RawDescriptionHelpFormatter` and an epilog with examples:

```python
parser = argparse.ArgumentParser(
    description="Unified status checker for AI coding tools",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""Common commands:
  limitlens suggest   Which AI should I use now?
  limitlens usage     Show day-wise usage
  limitlens all       Show hidden/empty providers too
  limitlens watch     Refresh continuously
""",
)
```

- [ ] **Step 5: Run focused CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 6: Commit aliases**

```bash
git add limitlens/cli.py tests/test_cli.py
git commit -m "feat: add short common CLI commands"
```

---

### Task 4: `--plain` display mode

**Files:**
- Modify: `limitlens/cli.py`
- Modify: `limitlens/core.py`
- Test: `tests/test_cli.py` or `tests/test_core.py`

- [ ] **Step 1: Write failing plain-mode tests**

Add tests proving:

```python
def test_plain_implies_no_color(monkeypatch):
    # parse/invoke `limitlens --plain --reco` with stub result
    # assert args.no_color is effectively true in printed output
    # assert no ANSI escape sequence appears


def test_plain_strips_known_icons_from_shared_icon_helper():
    args = argparse.Namespace(plain=True, no_color=True)
    assert display_icon("🔥", args) == ""
```

Use the actual helper name after implementation; if a helper already exists, test it directly.

- [ ] **Step 2: Run focused tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/test_core.py -q
```

Expected: new tests fail.

- [ ] **Step 3: Implement `--plain` argument and shared helpers**

In `cli.py` add:

```python
parser.add_argument("--plain", action="store_true", help="Plain output: no color, fewer icons/decorations")
```

After parse:

```python
if args.plain:
    args.no_color = True
```

In `core.py`, add helpers:

```python
def is_plain(args):
    return bool(getattr(args, "plain", False))


def plain_icon(icon, args):
    return "" if is_plain(args) else icon
```

Use these only in high-value shared headings or recommendation output. Do not attempt to remove every emoji in one pass.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/test_core.py -q
```

Expected: pass.

- [ ] **Step 5: Commit plain mode**

```bash
git add limitlens/cli.py limitlens/core.py tests/test_cli.py tests/test_core.py
git commit -m "feat: add plain CLI output mode"
```

---

### Task 5: Clear AI suggestion output

**Files:**
- Modify: `limitlens/cli.py` or `limitlens/recommendations.py` depending on current display ownership
- Test: `tests/test_recommendations.py` and/or `tests/test_cli.py`

- [ ] **Step 1: Locate recommendation display function**

Run:

```bash
rg -n "display.*reco|recommendation|At a glance|hard task|quick edit|cli" limitlens tests
```

Expected: identify the function printing `--reco` output.

- [ ] **Step 2: Write failing tests for direct suggestion language**

Add a deterministic unit test around the display function using fake recommendations:

```python
def test_suggestion_output_answers_which_ai_to_use(capsys):
    recs = {
        "hard": [{"name": "codex", "headroom_pct": 82.0, "reset_label": "2 days left", "note": "premium"}],
        "quick": [{"name": "antigravity flash", "headroom_pct": 60.0, "reset_label": "tomorrow", "note": "cheap quota"}],
        "cli": [{"name": "amp", "headroom_pct": 23.0, "reset_label": "replenishing", "note": "$1.15 pool"}],
    }
    display_suggestions(recs, argparse.Namespace(no_color=True, plain=True))
    out = capsys.readouterr().out
    assert "AI suggestion" in out
    assert "Hard task" in out
    assert "Quick edit" in out
    assert "CLI work" in out
    assert "82% left" in out
    assert "replenishing" in out
```

- [ ] **Step 3: Run focused tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_recommendations.py tests/test_cli.py -q
```

Expected: new suggestion-output test fails.

- [ ] **Step 4: Implement a small display function**

Add or adjust a function like:

```python
def display_suggestions(recs, args):
    print_c("\n  AI suggestion", "\033[1;35m", getattr(args, "no_color", False))
    labels = (("hard", "Hard task"), ("quick", "Quick edit"), ("cli", "CLI work"))
    for key, label in labels:
        picks = recs.get(key) or []
        if not picks:
            print_c(f"  {label:<10}: no usable option", "\033[33m", getattr(args, "no_color", False))
            continue
        top = picks[0]
        reason = f"{top.get('headroom_pct', 0):.0f}% left"
        if top.get("reset_label"):
            reason += f", {top['reset_label']}"
        if top.get("note"):
            reason += f", {top['note']}"
        print(f"  {label:<10}: {top['name']} — {reason}")
```

Wire `--reco`, `suggest`, and `s` to this same function.

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_recommendations.py tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 6: Commit suggestions**

```bash
git add limitlens/cli.py limitlens/recommendations.py tests/test_recommendations.py tests/test_cli.py
git commit -m "feat: clarify AI suggestions"
```

---

### Task 6: Day-wise usage output and export shape

**Files:**
- Modify: `limitlens/usage_tracker.py`
- Modify: `limitlens/cli.py` if export/display routing lives there
- Test: `tests/test_usage_tracker.py`

- [ ] **Step 1: Inspect current usage analytics shape**

Run:

```bash
rg -n "compute_usage_analytics|compute_daily_usage|export|display_usage|usage history|daily" limitlens/usage_tracker.py limitlens/cli.py tests/test_usage_tracker.py
```

Expected: identify existing display/export functions and avoid duplicating analytics.

- [ ] **Step 2: Write failing tests for daily output/export**

Add tests asserting `compute_usage_analytics` includes `daily` and approximate metadata:

```python
def test_usage_analytics_includes_daily_breakdown(monkeypatch):
    monkeypatch.setattr(waste_tracker, "_load_snapshots_with_anchor", lambda since: [
        {"key": "amp::pool", "tool": "amp", "remaining": 2.0, "pct_left": 40.0, "_ts": datetime(2026, 6, 20, 10, tzinfo=timezone.utc)},
        {"key": "amp::pool", "tool": "amp", "remaining": 1.5, "pct_left": 30.0, "_ts": datetime(2026, 6, 20, 11, tzinfo=timezone.utc)},
    ])
    data = compute_usage_analytics(days=7, config={})
    assert "daily" in data
    assert "2026-06-20" in data["daily"]
    assert data["daily"]["2026-06-20"]["approximate"] is True
```

Adjust expected fields to match the final existing analytics style.

- [ ] **Step 3: Run focused tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_usage_tracker.py -q
```

Expected: new daily export test fails.

- [ ] **Step 4: Implement daily analytics without replacing existing functions**

Use `compute_daily_usage(days, config)` inside `compute_usage_analytics` and add:

```python
"daily": {
    date: {
        "approximate": True,
        "usage": usage_by_key,
    }
    for date, usage_by_key in sorted(_get_merged_history(days, config=config).items())
}
```

If observed usage totals already exist separately, include them under existing analytics keys rather than faking exact per-provider billing.

- [ ] **Step 5: Improve text display**

In the existing usage display function, add a compact `Daily (approx)` block using the new `daily` data. Keep it short and skip if empty.

- [ ] **Step 6: Run focused usage tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_usage_tracker.py -q
```

Expected: pass.

- [ ] **Step 7: Commit usage improvements**

```bash
git add limitlens/usage_tracker.py limitlens/cli.py tests/test_usage_tracker.py
git commit -m "feat: add day-wise usage breakdown"
```

---

### Task 7: CLI visibility fixes without broad renderer rewrite

**Files:**
- Modify: `limitlens/cli.py`
- Modify: provider display functions only if a focused test proves `--tool` hides requested provider
- Test: `tests/test_cli.py` and provider-specific tests as needed

- [ ] **Step 1: Write failing visibility tests**

Add tests for three rules:

```python
def test_tool_specific_output_shows_provider_error(monkeypatch, capsys):
    # Stub collect_results to return {"amp": {"error": "boom"}}
    # Invoke `limitlens --tool amp --no-color`
    # Assert "boom" appears.


def test_all_mode_shows_empty_provider_state(monkeypatch, capsys):
    # Stub empty/disabled provider data and invoke `limitlens --all --no-color`
    # Assert some empty/disabled state is visible.


def test_default_output_does_not_hide_provider_errors(monkeypatch, capsys):
    # Stub one provider error in all mode default path
    # Assert error appears without --verbose.
```

- [ ] **Step 2: Run focused tests to verify failure if behavior is missing**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: tests fail only where current behavior is insufficient; if a test already passes, keep it as regression coverage.

- [ ] **Step 3: Implement minimal visibility improvements**

In the main CLI display loop, enforce:

```python
if args.tool != "all":
    # call the selected provider display even for disabled/error/empty payloads
if provider_result_has_error:
    # display error in default output
```

Avoid centralizing every provider renderer unless necessary. If a provider display function returns early because `args.tool` does not match, fix only that provider or call site.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 5: Commit visibility fixes**

```bash
git add limitlens/cli.py tests/test_cli.py limitlens/providers/*.py
git commit -m "fix: make CLI provider visibility predictable"
```

Only include provider files actually changed.

---

### Task 8: Version and changelog

**Files:**
- Modify: `pyproject.toml`
- Modify: `limitlens/__init__.py` if it contains `__version__`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Locate version strings**

Run:

```bash
rg -n "1\.4\.0|__version__|version =" pyproject.toml limitlens CHANGELOG.md
```

Expected: find all release version declarations.

- [ ] **Step 2: Update version to 1.5.0**

Change package version strings from `1.4.0` to `1.5.0` only in release metadata.

- [ ] **Step 3: Add changelog entry**

At the top of `CHANGELOG.md`, add:

```markdown
## 1.5.0 - 2026-06-21

- Added common command aliases: `suggest`/`s`, `usage`/`u`, `all`/`a`, and `watch`/`w`.
- Added low-conflict short flags: `-u`, `-a`, and `-w`.
- Added `--plain` for simpler human-readable output.
- Improved AI suggestions so `--reco` directly recommends tools for hard tasks, quick edits, and CLI work.
- Added clearer day-wise usage reporting with approximate snapshot-derived quota usage.
- Made config reset writes safer with atomic updates and backup creation.
- Improved CLI visibility so requested providers and provider errors are harder to miss.
```

- [ ] **Step 4: Run metadata/help smoke checks**

Run:

```bash
.venv/bin/python -m limitlens.cli --version
.venv/bin/python -m limitlens.cli --help
```

Expected: version shows `1.5.0`; help shows common commands.

- [ ] **Step 5: Commit release metadata**

```bash
git add pyproject.toml limitlens/__init__.py CHANGELOG.md
git commit -m "chore: prepare 1.5.0 release"
```

Only add `limitlens/__init__.py` if it changed.

---

### Task 9: Full local verification and smoke tests

**Files:**
- No code changes expected unless verification finds bugs.

- [ ] **Step 1: Run full tests**

```bash
.venv/bin/python -m pytest
```

Expected: all tests pass.

- [ ] **Step 2: Run smoke commands without writing user config**

Use temporary config/cache paths where needed:

```bash
LIMITLENS_CONFIG=$(mktemp -d)/config.json .venv/bin/python -m limitlens.cli --json
LIMITLENS_CONFIG=$(mktemp -d)/config.json .venv/bin/python -m limitlens.cli --reco --no-color
LIMITLENS_CONFIG=$(mktemp -d)/config.json .venv/bin/python -m limitlens.cli suggest --plain
LIMITLENS_CONFIG=$(mktemp -d)/config.json .venv/bin/python -m limitlens.cli --usage --no-color
LIMITLENS_CONFIG=$(mktemp -d)/config.json .venv/bin/python -m limitlens.cli usage --plain
LIMITLENS_CONFIG=$(mktemp -d)/config.json .venv/bin/python -m limitlens.cli --all --no-color
LIMITLENS_CONFIG=$(mktemp -d)/config.json .venv/bin/python -m limitlens.cli --tool amp --no-color
.venv/bin/python -m limitlens.switcher --help
```

Expected: commands exit successfully or show provider-specific unavailable messages without traceback.

- [ ] **Step 3: Check git status**

```bash
git status --short
```

Expected: only intentional tracked changes remain; no temp files.

---

### Task 10: Vertex review loop and final merge

**Files:**
- No planned code changes unless review finds issues.

- [ ] **Step 1: Ask Vertex 3.5 Flash to review diff**

Prompt with `git diff main...HEAD --stat` and relevant diff excerpts. Ask for correctness, regression, and scope risks.

- [ ] **Step 2: Ask Vertex 3.1 Pro to review diff independently**

Use the same review brief but ask for deeper edge cases and test gaps.

- [ ] **Step 3: Fix any real issues**

For each issue, verify it against code/tests before changing. Do not implement speculative or broad redesign suggestions.

- [ ] **Step 4: Repeat Vertex review once after fixes**

Ask both models whether remaining issues are release-blocking. Stop when there are no substantiated release blockers.

- [ ] **Step 5: Run final full verification**

```bash
.venv/bin/python -m pytest
```

Expected: all tests pass.

- [ ] **Step 6: Merge only after user confirmation**

Because merge affects shared history, report final status and ask for explicit confirmation before merging into `main`.
