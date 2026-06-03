"""
Waste tracker: persist quota snapshots and report wasted-at-reset stats.

Mechanism:
  • Every limitlens run appends a snapshot row per (tool, identity, window).
  • A "reset event" is detected when pct_left jumps UP by >= RESET_DETECT_PCT
    between two consecutive snapshots — the previous snapshot's pct_left is
    the % that was wasted at that reset.
  • Aggregate over the last N days → average waste per key + reset count.

Tracked tools:
  • Codex      — per (account, window)
  • Antigravity — per (profile, model), only when status=running (stale data
                  is unreliable for waste detection because it can't see resets)
  • Amp        — SKIPPED (replenishing $ pool, no fixed reset cycle)
  • Copilot    — SKIPPED (flat-fee unmetered)

Storage: JSONL at ~/.cache/limitlens/snapshots.jsonl (append-only, durable).
"""

import json
import os
from datetime import datetime, timedelta, timezone


SNAPSHOT_PATH = os.environ.get("LIMITLENS_SNAPSHOT_PATH") or os.path.expanduser("~/.cache/limitlens/snapshots.jsonl")
RESET_DETECT_PCT = 30   # pct_left jump that signals a reset event
RESET_AT_MIN_DELTA_SEC = 60  # reset_at must shift forward by at least this much
SNAPSHOT_PRUNE_DAYS = 90  # keep ~3 months of history

# Models considered effectively unlimited — exclude from waste tracking entirely.
# (They reset frequently with huge headroom; "wasted" doesn't apply.)
UNLIMITED_MODEL_KEYWORDS = ("flash",)

# Antigravity's displayed pct_left is unreliable near the bottom (sometimes
# shows ~20% left when the bucket is actually exhausted). Don't count an
# Antigravity reset event as "waste" unless the prior pct_left was strictly
# above this threshold.
ANTIGRAVITY_WASTE_MIN_PCT = 20


def _is_unlimited_model(label):
    if not label:
        return False
    lower = label.lower()
    return any(k in lower for k in UNLIMITED_MODEL_KEYWORDS)


def _reset_at_seconds(value):
    """Normalize reset_at (epoch int/float OR ISO string) → epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _is_reset_event(prev, curr):
    """
    A reset between prev → curr is detected if EITHER:
      • pct_left jumped up by >= RESET_DETECT_PCT (used some, then refilled), OR
      • reset_at moved forward by > RESET_AT_MIN_DELTA_SEC
        after the previous reset deadline passed. This catches "never-touched"
        buckets that stay at 100%.
    """
    pct_jump = (curr.get("pct_left") or 0) - (prev.get("pct_left") or 0)
    if pct_jump >= RESET_DETECT_PCT:
        return True
    p = _reset_at_seconds(prev.get("reset_at"))
    c = _reset_at_seconds(curr.get("reset_at"))
    ts = _reset_at_seconds(curr.get("ts"))
    if p and c and ts and ts >= p and c > p + RESET_AT_MIN_DELTA_SEC:
        return True
    return False


def _flatten_snapshot(result):
    """Turn a status result into per-quota rows for logging."""
    rows = []
    ts = datetime.now(timezone.utc).isoformat()

    codex = result.get("codex") or {}
    for acc in codex.get("accounts", []):
        if "error" in acc:
            continue
        for lim in acc.get("limits", []):
            pct_left = lim.get("left_percent")
            if pct_left is None:
                continue
            rows.append({
                "ts": ts,
                "tool": "codex",
                "key": f"codex-{acc['name']}::{lim['label']}",
                "pct_left": float(pct_left),
                "reset_at": lim.get("reset_time"),
            })

    ag = result.get("antigravity") or {}
    for prof in ag.get("profiles", []):
        # Only log fresh data — stale snapshots can't tell us about new resets.
        if prof.get("status") != "running":
            continue
        for m in prof.get("models", []):
            pct_left = m.get("pct_left")
            if pct_left is None:
                continue
            label = m.get("label", "")
            if _is_unlimited_model(label):
                continue  # Flash etc. — effectively unlimited, not "wasted"
            rows.append({
                "ts": ts,
                "tool": "antigravity",
                "key": f"antigravity:{prof['name']}::{label}",
                "pct_left": float(pct_left),
                "reset_at": m.get("reset_time"),
            })

    return rows


def record_snapshot(result):
    """Append snapshot to JSONL. Silent on any failure — never break limitlens."""
    rows = _flatten_snapshot(result)
    if not rows:
        return
    try:
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with open(SNAPSHOT_PATH, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(row) + "\n" for row in rows))
    except OSError:
        pass


def _parse_ts(s):
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _load_snapshots(since=None):
    if not os.path.exists(SNAPSHOT_PATH):
        return []
    rows = []
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(row.get("ts"))
                if ts is None:
                    continue
                if since and ts < since:
                    continue
                row["_ts"] = ts
                rows.append(row)
    except OSError:
        return []
    return rows


def reset_snapshots():
    """Delete the entire snapshot history. Returns True on success."""
    try:
        if os.path.exists(SNAPSHOT_PATH):
            os.remove(SNAPSHOT_PATH)
        return True
    except OSError:
        return False


def prune_old_snapshots():
    """Optional: drop rows older than SNAPSHOT_PRUNE_DAYS to keep file small."""
    if not os.path.exists(SNAPSHOT_PATH):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_PRUNE_DAYS)
    rows = _load_snapshots(since=cutoff)
    try:
        tmp_path = SNAPSHOT_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            for row in rows:
                row.pop("_ts", None)
                f.write(json.dumps(row) + "\n")
        os.replace(tmp_path, SNAPSHOT_PATH)
    except OSError:
        pass


def compute_waste(days=7):
    """
    Return dict[key] -> {
        reset_count, avg_wasted_pct, max_wasted_pct,
        last_seen_pct, last_seen_at,
        events: [{at, wasted_pct}],
    }
    Only keys with at least one detected reset event are included.
    Unlimited-model keys (Flash etc.) are filtered out — they may exist in
    historical snapshots from before the exclusion was added.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = _load_snapshots(since=since)
    if not rows:
        return {}

    by_key = {}
    for row in rows:
        # Filter legacy rows for unlimited models (e.g. recorded before exclusion).
        key = row["key"]
        label = key.split("::", 1)[-1] if "::" in key else ""
        if _is_unlimited_model(label):
            continue
        by_key.setdefault(key, []).append(row)

    out = {}
    for key, series in by_key.items():
        series.sort(key=lambda r: r["_ts"])
        is_antigravity = any(r.get("tool") == "antigravity" for r in series)
        events = []
        for prev, curr in zip(series, series[1:]):
            if _is_reset_event(prev, curr):
                wasted = float(prev["pct_left"] or 0)
                # Antigravity's bottom-end pct is unreliable; don't count
                # "waste" of <=20% — it was probably actually exhausted.
                if is_antigravity and wasted <= ANTIGRAVITY_WASTE_MIN_PCT:
                    continue
                events.append({
                    "at": curr["ts"],
                    "wasted_pct": wasted,
                })
        if not events:
            continue
        wastes = [e["wasted_pct"] for e in events]
        out[key] = {
            "reset_count": len(events),
            "avg_wasted_pct": sum(wastes) / len(wastes),
            "max_wasted_pct": max(wastes),
            "last_seen_pct": series[-1]["pct_left"],
            "last_seen_at": series[-1]["ts"],
            "events": events,
        }
    return out


def _verdict(avg_pct):
    if avg_pct >= 60:
        return "heavy waste"
    if avg_pct >= 30:
        return "wasting some"
    if avg_pct >= 10:
        return "ok"
    return "well used"


def display_waste_report(report, days, args, print_c):
    print_c(f"\n  ═══ Waste Report (last {days} days) ═══", "\033[1;35m", args.no_color)

    if not report:
        print_c(
            "\n    (no reset events recorded yet — keep running `limitlens` for a few days,",
            "\033[33m", args.no_color,
        )
        print_c(
            "     or set up a cron/launchd job to record snapshots automatically.)",
            "\033[33m", args.no_color,
        )
        _print_setup_hint(args, print_c)
        return

    items = sorted(report.items(), key=lambda kv: -kv[1]["avg_wasted_pct"])

    if args.no_color:
        print(f"\n  {'identity / window':<48} {'resets':>7} {'avg':>7} {'max':>7}  verdict")
    else:
        print(f"\n  \033[1m{'identity / window':<48} {'resets':>7} {'avg':>7} {'max':>7}  verdict\033[0m")

    for key, data in items:
        avg = data["avg_wasted_pct"]
        mx  = data["max_wasted_pct"]
        n   = data["reset_count"]
        verdict = _verdict(avg)
        color = "\033[31m" if avg >= 60 else "\033[33m" if avg >= 30 else "\033[32m"
        line = f"  {key:<48} {n:>7d} {avg:>6.1f}% {mx:>6.1f}%  "
        if args.no_color:
            print(line + verdict)
        else:
            print(line + f"{color}{verdict}\033[0m")

    print_c(
        "\n  💡 'avg' = avg % unused at the moment each window reset. Lower = better.",
        "\033[90m", args.no_color,
    )
    _print_setup_hint(args, print_c)


def _print_setup_hint(args, print_c):
    print_c(
        "\n  Tip: snapshots are recorded on every `limitlens` run. To capture",
        "\033[90m", args.no_color,
    )
    print_c(
        "       resets you'd otherwise miss, add a cron/launchd job, e.g.:",
        "\033[90m", args.no_color,
    )
    print_c(
        "         */30 * * * *  limitlens --record >/dev/null 2>&1",
        "\033[90m", args.no_color,
    )
