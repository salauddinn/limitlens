# LimitLens 1.5.0 Stable Release Design

## Purpose

LimitLens 1.5.0 is a stability-first user experience release. The goal is not to add a large new subsystem; it is to make the tool feel reliable, understandable, and easy to use before merging and releasing the current work.

The release addresses five user-facing problems:

1. Local config can feel fragile or disappear.
2. The CLI hides too much, making users think providers vanished.
3. Day-wise usage exists internally but is not visible enough.
4. AI recommendations should directly answer which AI tool to use.
5. Common workflows require too much typing and the display can feel visually busy.

## Scope

### In scope

- Safer config read/write behavior.
- Clearer hidden/disabled/provider visibility in CLI output.
- First-class day-wise usage display and export shape.
- Better AI suggestion output for hard tasks, quick edits, and CLI work.
- Easier commands and short aliases for common workflows.
- A simple/plain display mode for users who find color, icons, or dense styling difficult.
- Version, changelog, tests, and release smoke checks for 1.5.0.

### Out of scope

- Rewriting the full dashboard renderer.
- Adding external AI calls or remote recommendation services.
- Changing provider authentication flows beyond config safety.
- Removing existing flags or breaking existing automation.
- Major menubar redesign unless a release-blocking bug is found.

## Design principles

- Preserve user data first. Never silently replace or destructively rewrite local config.
- Keep defaults compact, but never hide important errors or make providers look missing.
- Make common commands memorable without removing advanced flags.
- Keep recommendations explainable with quota, reset, and reliability reasons.
- Prefer small, tested changes over broad UI rewrites.

## Config stability

LimitLens should treat local config as user-owned state.

Behavior:

- Loading config merges the checked-in defaults with the user config.
- Existing config is never replaced by auto-detection.
- Invalid JSON produces a clear validation error and does not rewrite the file.
- Any operation that writes config uses an atomic temp-file-and-replace pattern.
- Destructive config edits, such as resetting custom tool counters, create a timestamped backup first.
- `--reset-spend` only changes intended counters and reset metadata; it must not disable providers or remove unrelated settings.

Testing:

- Missing config falls back safely to defaults and auto-detection only when no user config exists.
- Invalid JSON raises a clear `ConfigValidationError`.
- Resetting custom tool spend preserves unrelated config keys.
- Config writes are atomic and create backups where expected.

## CLI visibility and hiding

The CLI should stay clean without making data feel missing.

Behavior:

- Default output remains compact.
- `--tool <provider>` always shows that provider, including disabled, empty, hidden, stale, or errored status.
- `--all` shows hidden, disabled, stale, and empty providers where possible.
- `--verbose` explains why providers or rows are hidden.
- Default output may include a short hidden-summary line when providers were intentionally omitted, but only if this can be implemented cleanly without redesigning every provider renderer.
- Provider errors are never hidden in normal output.

Example default summary:

```text
3 providers hidden or empty; use --all to show them, --verbose for reasons
```

Testing:

- Hidden providers remain hidden in default compact output.
- `--all` and `--tool` reveal provider state.
- Provider errors are visible.
- Verbose output includes useful hiding reasons.

## Day-wise usage

Usage should be a first-class feature, not just internal analytics.

Behavior:

- `limitlens --usage` and `limitlens usage` show:
  - today summary
  - last 7 days summary
  - day-by-day usage rows
  - top observed provider/model when available
- Usage units remain honest:
  - percentage points for quota snapshots
  - dollars for Amp-style spend
  - tokens/requests/cost for observed provider logs
- Snapshot-derived quota usage is approximate. It is based on observed quota decreases between recorded snapshots, not provider-grade billing data.
- JSON export includes a clear `daily` object keyed by date.
- Existing import/export behavior continues working.

Example text:

```text
Usage
  Today
    amp                 $0.42 used
    opencode:gpt-5      12.4K tokens

  Last 7 days
    codex               38% quota used
    amp                 $3.15 used

  Daily
    2026-06-21          amp $0.42 · opencode 12.4K tokens
    2026-06-20          codex 8% · amp $1.10
```

Testing:

- Day-wise rows are generated from snapshots.
- Imported usage and live snapshot usage merge correctly.
- Empty usage prints a helpful empty state.
- JSON export contains the daily breakdown.

## AI suggestion

Recommendations should answer: “Which AI should I use now?”

Behavior:

- Keep the three existing task categories:
  - hard task
  - quick edit
  - CLI/pairing
- Improve `--reco` output to be direct and reasoned.
- Add `suggest` and `s` command aliases for recommendations.
- Recommendation reasons include quota/headroom, reset timing, replenishing vs prepaid quota, and stale/unreliable data warnings.
- Low or unreliable providers are either excluded or clearly marked.

Example:

```text
AI suggestion
  Hard task:   Codex Pro — 82% left, premium model, resets in 2 days
  Quick edit:  Antigravity Flash — use cheaper quota first
  CLI work:    Amp — $1.15 left, replenishing
```

Testing:

- `--reco`, `suggest`, and `s` produce equivalent recommendation output.
- Empty recommendation sets show a fallback message.
- Stale/unreliable options are not presented as confident picks.

## Less typing and simpler display

Common workflows should not require remembering long flags.

Behavior:

- Existing flags remain supported.
- Add command aliases:
  - `limitlens suggest` and `limitlens s` map to `--reco`.
  - `limitlens usage` and `limitlens u` map to `--usage`.
  - `limitlens all` and `limitlens a` map to `--all`.
  - `limitlens watch` and `limitlens w` map to `--watch`.
- Add only low-conflict short flags for common actions:
  - `-u` for usage.
  - `-a` for all.
  - `-w` for watch.
- Do not add `-s` in 1.5.0. It is ambiguous with sync, silent, store, and suggest. Use `suggest` or `s` instead.
- Add `--plain` as a simple display preset:
  - no ANSI color
  - no emoji/icons where output already flows through shared display helpers
  - no decorative separators where easy to suppress safely
  - still human-readable text
  - not a JSON replacement
- Avoid building a second full renderer for `--plain`; implement it by extending existing display helpers and targeted call sites.
- Help text lists common commands first with examples.

Testing:

- Subcommand aliases map to the correct existing behavior.
- Short flags map to the correct existing behavior.
- `--plain` disables color and reduces decorative output without breaking data.
- Existing long flags still work.

## Release validation

Before merge or release:

1. Run the full test suite.
2. Run smoke commands:
   - `limitlens --json`
   - `limitlens --reco`
   - `limitlens suggest`
   - `limitlens --usage`
   - `limitlens usage`
   - `limitlens --all`
   - `limitlens --tool amp`
   - `limitlens --plain`
   - `limitlens-switch --help`
3. Update version to `1.5.0`.
4. Update changelog with the stability and UX changes.
5. Check git status and ensure only intentional files are changed.

## Implementation order

1. Branch: `stable-release/1.5.0`.
2. Add focused tests for new CLI aliases, config safety, usage display, suggestion output, and `--plain`.
3. Implement safe CLI aliases first because they are low risk and immediately reduce typing.
4. Implement `--plain` without creating a second full renderer.
5. Improve AI suggestion output and wire `suggest`/`s` aliases to it.
6. Improve day-wise usage output and JSON export, with approximate snapshot usage clearly represented.
7. Implement config safety improvements, including atomic writes and backups for destructive config edits.
8. Implement CLI visibility improvements, prioritizing `--tool`, `--all`, `--verbose`, and visible errors. Add a hidden-summary line only if it stays simple and robust.
9. Update version and changelog.
10. Run full verification and release smoke checks.
