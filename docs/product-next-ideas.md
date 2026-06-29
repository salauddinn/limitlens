# LimitLens Next Ideas

These are parked ideas to revisit later. The goal is to make LimitLens easier to try, easier to understand, and more useful for beta feedback.

## Best next bets

### 1. Add `limitlens doctor`

A guided health check for first-time users and beta testers.

It should show:

- Which AI tools are installed or detected.
- Which providers are ready.
- Which providers need auth, config, or a missing local app.
- Whether the menubar app is available.
- Whether the iTerm2 widget can be installed.
- The best next command to run.

Example shape:

```text
LimitLens Doctor

Codex        ready
Amp          ready
Cursor       detected, auth may be expired
Pioneer      not configured
CommandCode  token missing

Next: run `limitlens` for the dashboard or `limitlens suggest` for routing advice.
```

Why it matters: LimitLens is already powerful, but a new user needs a simple “is this working?” command.

### 2. Improve README onboarding

The README is strong but dense. Add a short “Start here” section near the top.

Suggested flow:

```md
## Start here

1. Install LimitLens.
2. Run `limitlens doctor`.
3. Run `limitlens` to see all quotas.
4. Run `limitlens suggest` to pick the best tool for your next task.
5. Optional: enable the macOS menubar app or iTerm2 widget.
```

Positioning copy to test:

> Stop wasting paid AI quota. See all your AI coding limits in one place and route tasks to the best available tool.

### 3. Add beta feedback support

For friends testing the product, add an easy way to send useful feedback without leaking private data.

Possible commands:

```bash
limitlens feedback
limitlens doctor --report
```

The report should be sanitized and include:

- OS family and Python version, without machine-specific identifiers.
- LimitLens version.
- Detected providers.
- Provider readiness states.
- Missing setup steps.
- Recent non-sensitive errors.

It should avoid including tokens, cookies, emails, full local paths, or private config values.

### 4. Make recommendations more human

The recommendation output can explain the decision more clearly.

Instead of only naming the best tool, show:

```text
Best now: Amp
Why: high remaining quota and good fit for coding tasks.
Save: Codex for harder multi-file reasoning.
Avoid: Cursor for now because fast requests are low.
```

Why it matters: this makes LimitLens feel like a workflow assistant, not just a quota table.

### 5. Add provider health labels

Make the dashboard easier to scan with states like:

```text
Codex        ready
Amp          ready
Cursor       detected, auth expired
Pioneer      not configured
CommandCode  token missing
```

This can reuse the same underlying readiness model as `limitlens doctor`.

### 6. Do release hygiene before a wider launch

Before sharing more broadly, check that generated files, caches, build outputs, and local artifacts are not tracked.

Useful checks:

```bash
git status --short
git ls-files | grep -E '(__pycache__|pytest_cache|ruff_cache|dist|coverage|egg-info)'
```

If anything accidental is tracked, clean it up and strengthen `.gitignore`.

## Recommended order

1. Add `limitlens doctor`.
2. Improve README onboarding.
3. Add sanitized beta feedback/report mode.
4. Polish recommendation explanations.
5. Add provider health labels.
6. Do release hygiene and a launch checklist.
