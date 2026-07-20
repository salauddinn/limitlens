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
  • Amp        — tracked as estimated missed replenishment when the pool
                  appears full across snapshot intervals
  • Copilot    — SKIPPED (flat-fee unmetered)

Storage: JSONL at ~/.cache/limitlens/snapshots.jsonl (append-only, durable).
"""

import json
import os
from datetime import datetime, timedelta, timezone

from .logging import get_logger
log = get_logger("limitlens.waste_tracker")


SNAPSHOT_PATH = os.environ.get("LIMITLENS_SNAPSHOT_PATH") or os.path.expanduser("~/.cache/limitlens/snapshots.jsonl")
RESET_DETECT_PCT = 30   # pct_left jump that signals a reset event
RESET_AT_MIN_DELTA_SEC = 60  # reset_at must shift forward by at least this much
SNAPSHOT_PRUNE_DAYS = 90  # keep ~3 months of history
SNAPSHOT_PRUNE_INTERVAL_HOURS = 24

# Models considered effectively unlimited — exclude from waste tracking entirely.
# (They reset frequently with huge headroom; "wasted" doesn't apply.)
UNLIMITED_MODEL_KEYWORDS = ("flash",)

# Antigravity's displayed pct_left is unreliable near the bottom (sometimes
# shows ~20% left when the bucket is actually exhausted). Don't count an
# Antigravity reset event as "waste" unless the prior pct_left was strictly
# above this threshold.
ANTIGRAVITY_WASTE_MIN_PCT = 20


def _codex_account_from_snapshot_key(key):
    """Return (account_name, expected_home) for a Codex snapshot key."""
    key = str(key or "")
    if key == "codex" or key.startswith("codex::"):
        return "default", "~/.codex"
    if not key.startswith("codex-"):
        return None
    account = key.split("::", 1)[0].removeprefix("codex-")
    name = "default" if account in ("", "codex", "default") else account
    home = "~/.codex" if name == "default" else f"~/.codex-{name}"
    return name, home


def _codex_account_is_ignored(name, home, ignored_accounts):
    if isinstance(ignored_accounts, str):
        ignored_accounts = [ignored_accounts]
    elif not isinstance(ignored_accounts, list):
        return False

    label = "codex" if name == "default" else f"codex-{name}"
    candidates = {
        str(name).lower(),
        label.lower(),
        os.path.basename(home).lower(),
        os.path.expanduser(home).lower(),
    }
    for ignored in ignored_accounts:
        value = os.path.expanduser(str(ignored).strip()).lower()
        if value in candidates:
            return True
    return False


def _snapshot_key_is_config_ignored(key, config=None):
    account = _codex_account_from_snapshot_key(key)
    if not account or not isinstance(config, dict):
        return False

    cfg = config.get("codex") or {}
    if not isinstance(cfg, dict):
        return False

    if str(cfg.get("enabled", True)).lower() in ("false", "0", "no"):
        return True

    name, home = account
    return _codex_account_is_ignored(name, home, cfg.get("ignored_accounts") or [])


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
            return float(value)
        except ValueError:
            pass
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
    pct_jump = float(curr.get("pct_left") or 0.0) - float(prev.get("pct_left") or 0.0)
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

    amp = result.get("amp") or {}
    if "error" not in amp:
        for tier in amp.get("tiers", []):
            remaining = tier.get("remaining")
            if remaining is None:
                pct_left = tier.get("pct_left")
                if pct_left is not None:
                    rows.append({
                        "ts": ts,
                        "tool": "amp",
                        "key": f"amp::{tier.get('label') or 'quota'}",
                        "pct_left": float(pct_left),
                        "reset_at": tier.get("reset_time"),
                        "unit": "percent",
                    })
                continue
            row = {
                "ts": ts,
                "tool": "amp",
                "key": f"amp::{tier.get('label') or 'credits'}",
                "remaining": float(remaining),
                "unit": "usd",
            }
            total = tier.get("total")
            if total is not None:
                row["total"] = float(total)
                row["used"] = float(tier.get("used") if tier.get("used") is not None else max(0.0, float(total) - float(remaining)))
            pct_left = tier.get("pct_left")
            if pct_left is not None:
                row["pct_left"] = float(pct_left)
            if tier.get("replenish_rate") is not None:
                row["replenish_rate"] = float(tier["replenish_rate"])
            rows.append(row)

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


def _prune_marker_path():
    return f"{SNAPSHOT_PATH}.pruned"


def _prune_old_snapshots_locked():
    """Bug 7: run prune assuming the file lock is already held by the caller."""
    if not os.path.exists(SNAPSHOT_PATH):
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_PRUNE_DAYS)
    rows = _load_snapshots(since=cutoff)
    import tempfile
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(SNAPSHOT_PATH),
            prefix="snapshots_",
            suffix=".tmp",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in rows:
                row.pop("_ts", None)
                f.write(json.dumps(row) + "\n")
        os.replace(tmp_path, SNAPSHOT_PATH)
        tmp_path = None
    except OSError:
        pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _maybe_prune_old_snapshots():
    """Run retention cleanup at most once per interval (call inside held lock)."""
    marker = _prune_marker_path()
    try:
        now = datetime.now(timezone.utc).timestamp()
        last = os.path.getmtime(marker) if os.path.exists(marker) else 0
        if now - last < SNAPSHOT_PRUNE_INTERVAL_HOURS * 3600:
            return
        # Bug 7: delegate to locked variant — our caller already holds the lock.
        _prune_old_snapshots_locked()
        # Bug 5: create marker atomically with correct permissions (no race).
        marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(marker_fd)
        os.utime(marker, None)
    except OSError:
        pass


def record_snapshot(result):
    """Append snapshot to JSONL. Silent on any failure — never break limitlens."""
    rows = _flatten_snapshot(result)
    if not rows:
        return
    from limitlens.core import file_lock
    try:
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), mode=0o700, exist_ok=True)
        # Bug 6: heartbeat=False — append is fast, no need to spin a touch-thread.
        with file_lock(SNAPSHOT_PATH + ".lock", heartbeat=False):
            _maybe_prune_old_snapshots()
            # Bug 9: append only — do not read the full file on every record.
            # Bug 5: open atomically with correct permissions (no open+chmod race).
            snap_fd = os.open(
                SNAPSHOT_PATH,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            with os.fdopen(snap_fd, "a", encoding="utf-8") as f:
                f.write("".join(json.dumps(row) + "\n" for row in rows))
    except OSError:
        pass


def _parse_ts(s):
    try:
        if isinstance(s, (int, float)):
            return datetime.fromtimestamp(s, timezone.utc)
        if isinstance(s, str):
            s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _load_snapshots(since=None):
    if not os.path.exists(SNAPSHOT_PATH):
        return []
    from limitlens.core import file_lock
    try:
        with file_lock(SNAPSHOT_PATH + ".lock"):
            rows = []
            with open(SNAPSHOT_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    ts = _parse_ts(row.get("ts"))
                    if ts is None:
                        continue
                    if since and ts < since:
                        continue
                    row["_ts"] = ts
                    rows.append(row)
            return rows
    except OSError:
        return []


def estimate_amp_daily_reset(label, pct_left, observed_at=None, now=None):
    """Estimate Amp's next daily reset from the narrowest observed refill interval."""
    observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now = (now or observed_at).astimezone(timezone.utc)
    key = f"amp::{label or 'quota'}"
    since = observed_at - timedelta(days=SNAPSHOT_PRUNE_DAYS)
    samples = []
    for row in _load_snapshots(since=since):
        if row.get("tool") != "amp" or row.get("key") != key:
            continue
        if row.get("pct_left") is None:
            continue
        ts = row.get("_ts") or _parse_ts(row.get("ts"))
        if ts is not None:
            samples.append((ts.astimezone(timezone.utc), float(row["pct_left"])))
    samples.append((observed_at, float(pct_left)))
    samples.sort(key=lambda sample: sample[0])

    reset_intervals = []
    for previous, current in zip(samples, samples[1:]):
        previous_at, previous_pct = previous
        current_at, current_pct = current
        interval = current_at - previous_at
        if not timedelta(0) < interval <= timedelta(hours=24):
            continue
        if current_pct >= 90.0 and current_pct - previous_pct >= 5.0:
            reset_intervals.append((interval, previous_at + interval / 2))

    if not reset_intervals:
        return None

    _, learned_reset = min(reset_intervals, key=lambda item: item[0])
    next_reset = learned_reset
    while next_reset <= now:
        next_reset += timedelta(days=1)
    while next_reset > now + timedelta(days=1):
        next_reset -= timedelta(days=1)
    return next_reset


def _load_snapshots_with_anchor(since=None):
    from limitlens.core import file_lock
    try:
        with file_lock(SNAPSHOT_PATH + ".lock"):
            rows = _load_snapshots()
            if not since:
                return rows

            rows.sort(key=lambda r: r["_ts"])

            anchors = {}
            valid_rows = []

            for row in rows:
                ts = row.get("_ts")
                if not ts:
                    continue
                key = row.get("key")
                if ts < since:
                    anchors[key] = row
                else:
                    valid_rows.append(row)

            final_rows = list(anchors.values()) + valid_rows
            final_rows.sort(key=lambda r: r["_ts"])
            return final_rows
    except OSError:
        return []


def merge_snapshots(new_rows):
    if not isinstance(new_rows, list):
        return False

    from limitlens.core import file_lock
    try:
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), mode=0o700, exist_ok=True)
        with file_lock(SNAPSHOT_PATH + ".lock"):
            existing = _load_snapshots()

            parsed_new = []
            for r in new_rows:
                if not isinstance(r, dict):
                    continue
                row_copy = r.copy()
                if "_ts" not in row_copy:
                    row_copy["_ts"] = _parse_ts(row_copy.get("ts"))
                if row_copy["_ts"] is not None:
                    parsed_new.append(row_copy)

            all_rows = existing + parsed_new

            seen = set()
            deduped = []

            for row in all_rows:
                ts_str = row.get("ts")
                key = row.get("key")
                tool = row.get("tool")
                pct_left = row.get("pct_left")
                remaining = row.get("remaining")
                reset_at = row.get("reset_at")

                def _fmt(v):
                    if isinstance(v, (int, float)):
                        return str(float(v))
                    return str(v)

                sig = tuple(_fmt(v) for v in (ts_str, key, tool, pct_left, remaining, reset_at))
                if sig not in seen:
                    seen.add(sig)
                    deduped.append(row)

            deduped.sort(key=lambda r: r["_ts"])

            import tempfile
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(SNAPSHOT_PATH), prefix="snapshots_", suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    for row in deduped:
                        out = row.copy()
                        out.pop("_ts", None)
                        f.write(json.dumps(out) + "\n")
                os.replace(tmp_path, SNAPSHOT_PATH)
                tmp_path = None
                return True
            except OSError:
                return False
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
    except OSError:
        return False


def reset_snapshots():
    """Delete the entire snapshot history. Returns True on success."""
    if not os.path.exists(SNAPSHOT_PATH):
        return True
    from limitlens.core import file_lock
    try:
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), mode=0o700, exist_ok=True)
        with file_lock(SNAPSHOT_PATH + ".lock"):
            if os.path.exists(SNAPSHOT_PATH):
                os.remove(SNAPSHOT_PATH)
            return True
    except OSError:
        return False


def prune_old_snapshots():
    """Optional: drop rows older than SNAPSHOT_PRUNE_DAYS to keep file small."""
    if not os.path.exists(SNAPSHOT_PATH):
        return
    from limitlens.core import file_lock
    try:
        # Bug 7: acquire lock once, then delegate to the locked variant.
        with file_lock(SNAPSHOT_PATH + ".lock"):
            _prune_old_snapshots_locked()
    except OSError:
        pass


def _amp_float(row, field):
    try:
        value = row.get(field)
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_amp_replenish_waste(series, since):
    """Estimate Amp refill dollars missed while a capped pool was full."""
    events = []
    totals = []
    for prev, curr in zip(series, series[1:]):
        curr_ts = curr.get("_ts")
        prev_ts = prev.get("_ts")
        if curr_ts is None or prev_ts is None or curr_ts < since:
            continue
        hours = (curr_ts - prev_ts).total_seconds() / 3600.0
        if hours <= 0:
            continue
        total = _amp_float(curr, "total") or _amp_float(prev, "total")
        rate = _amp_float(curr, "replenish_rate") or _amp_float(prev, "replenish_rate")
        prev_remaining = _amp_float(prev, "remaining")
        curr_remaining = _amp_float(curr, "remaining")
        if not total or not rate or prev_remaining is None or curr_remaining is None:
            continue
        if curr_remaining < total - 0.01:
            continue

        # Snapshot data cannot know exact usage timing inside the interval.
        # This estimates missed refill when the interval ends at the cap.
        cap_space_at_start = max(0.0, total - prev_remaining)
        missed = max(0.0, (rate * hours) - cap_space_at_start)
        if missed < 0.01:
            continue
        events.append({
            "at": curr["ts"],
            "wasted_usd": round(missed, 2),
            "hours": round(hours, 2),
            "estimated": True,
        })
        totals.append(total)

    if not events:
        return None
    wastes = [e["wasted_usd"] for e in events]
    total_wasted = round(sum(wastes), 2)
    avg_wasted = round(total_wasted / len(wastes), 2)
    cap = max(totals) if totals else None
    avg_pct = (avg_wasted / cap * 100.0) if cap else 0.0
    return {
        "waste_type": "replenish_cap",
        "waste_unit": "usd",
        "reset_count": len(events),
        "avg_wasted_usd": avg_wasted,
        "max_wasted_usd": max(wastes),
        "total_wasted_usd": total_wasted,
        "avg_wasted_pct": avg_pct,
        "max_wasted_pct": (max(wastes) / cap * 100.0) if cap else 0.0,
        "last_seen_remaining": series[-1].get("remaining"),
        "last_seen_at": series[-1]["ts"],
        "events": events,
        "estimated": True,
    }


def compute_ttx(snapshots, key):
    """Compute time-to-exhaustion in hours for a given snapshot key.

    Uses the last 6 snapshots for ``key`` and fits a linear velocity.
    Returns None if there are fewer than 2 data points, velocity is
    non-negative (not decreasing), or the timestamps are unusable.
    """
    key_snaps = [
        s for s in snapshots
        if s.get("key") == key and s.get("pct_left") is not None and s.get("ts")
    ]
    key_snaps.sort(key=lambda s: s.get("ts", ""))

    valid_snaps = []
    for snap in reversed(key_snaps):
        if not valid_snaps:
            valid_snaps.append(snap)
        else:
            if float(valid_snaps[-1]["pct_left"]) - float(snap["pct_left"]) >= RESET_DETECT_PCT:
                break  # Reset boundary detected
            valid_snaps.append(snap)
            if len(valid_snaps) == 6:
                break

    valid_snaps.reverse()
    if len(valid_snaps) < 2:
        return None

    first, last = valid_snaps[0], valid_snaps[-1]
    t_first = _parse_ts(first["ts"])
    t_last = _parse_ts(last["ts"])
    if t_first is None or t_last is None:
        return None
    elapsed_hours = (t_last - t_first).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return None
    pct_change = float(last["pct_left"]) - float(first["pct_left"])
    if pct_change >= 0:
        return None  # quota is not decreasing
    velocity = pct_change / elapsed_hours  # negative, % per hour
    pct_current = float(last["pct_left"])
    if pct_current <= 0:
        return 0.0
    return pct_current / abs(velocity)


def format_ttx(ttx_hours):
    """Human-readable time-to-exhaustion string, or None if ttx_hours is None."""
    if ttx_hours is None:
        return None
    if ttx_hours < 1:
        return "< 1h"
    if ttx_hours < 48:
        hours = int(ttx_hours)
        minutes = int((ttx_hours - hours) * 60)
        if minutes:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    days = int(ttx_hours / 24)
    return f"{days} days"


def compute_waste(days=7, config=None):
    """
    Return dict[key] -> waste data.

    Reset quotas report pct left unused at reset. Amp reports estimated missed
    replenish dollars when a capped pool appears full across snapshot intervals.
    Only keys with at least one detected waste event are included.
    Unlimited-model keys (Flash etc.) are filtered out — they may exist in
    historical snapshots from before the exclusion was added.
    """
    from limitlens.core import file_lock
    try:
        with file_lock(SNAPSHOT_PATH + ".lock"):
            since = datetime.now(timezone.utc) - timedelta(days=days)
            rows = _load_snapshots_with_anchor(since)
            if not rows:
                return {}

            by_key = {}
            for row in rows:
                # Filter legacy rows for unlimited models (e.g. recorded before exclusion).
                key = row["key"]
                label = key.split("::", 1)[-1] if "::" in key else ""
                if _is_unlimited_model(label):
                    continue
                # Respect current Codex account ignores for historical rows too. The
                # snapshot log is append-only, so ignored accounts may still exist in
                # old data even though live collection no longer returns them.
                if _snapshot_key_is_config_ignored(key, config):
                    continue
                by_key.setdefault(key, []).append(row)

            out = {}
            for key, series in by_key.items():
                series.sort(key=lambda r: r["_ts"])
                if any(r.get("tool") == "amp" for r in series):
                    amp_waste = _compute_amp_replenish_waste(series, since)
                    if amp_waste:
                        ttx = compute_ttx(rows, key)
                        amp_waste["ttx_hours"] = ttx
                        amp_waste["ttx_label"] = format_ttx(ttx)
                        out[key] = amp_waste
                    continue

                is_antigravity = any(r.get("tool") == "antigravity" for r in series)
                events = []
                for prev, curr in zip(series, series[1:]):
                    # Rows before `since` are anchors only: they provide prior context
                    # for detecting the first in-window reset, but are never counted as
                    # events themselves.
                    curr_ts = curr.get("_ts")
                    if curr_ts is None or curr_ts < since:
                        continue
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
                ttx = compute_ttx(rows, key)
                out[key] = {
                    "reset_count": len(events),
                    "avg_wasted_pct": sum(wastes) / len(wastes),
                    "max_wasted_pct": max(wastes),
                    "last_seen_pct": series[-1]["pct_left"],
                    "last_seen_at": series[-1]["ts"],
                    "events": events,
                    "ttx_hours": ttx,
                    "ttx_label": format_ttx(ttx),
                }
            return out
    except OSError:
        return {}


def _verdict(avg_pct):
    if avg_pct >= 60:
        return "heavy waste"
    if avg_pct >= 30:
        return "wasting some"
    if avg_pct >= 10:
        return "ok"
    return "well used"


def _color(text, color, no_color):
    return text if no_color else f"{color}{text}\033[0m"


def _friendly_key(key):
    key = str(key)
    if key.startswith("codex-"):
        return "Codex " + key.replace("codex-", "", 1).replace("::", " / ")
    if key.startswith("antigravity:"):
        return "Antigravity " + key.replace("antigravity:", "", 1).replace("::", " / ")
    return key.replace("::", " / ")


def _shorten(text, width):
    text = str(text)
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _waste_tip(avg_pct):
    if avg_pct >= 60:
        return "try this earlier"
    if avg_pct >= 30:
        return "room to use more"
    if avg_pct >= 10:
        return "pretty balanced"
    return "great usage"


def _bar(value, width=10):
    value = max(0.0, min(100.0, float(value or 0.0)))
    filled = int(round((value / 100.0) * width))
    return "█" * filled + "░" * (width - filled)


def _box(title, lines):
    width = max([len(title) + 4] + [len(line) for line in lines])
    top = f"╭─ {title} " + "─" * max(0, width - len(title) - 3) + "╮"
    bottom = "╰" + "─" * (len(top) - 2) + "╯"
    body = [f"│ {line:<{len(top) - 4}} │" for line in lines]
    return "\n".join([top] + body + [bottom])


def _waste_group(avg_pct):
    if avg_pct >= 30:
        return "Needs attention"
    if avg_pct >= 10:
        return "Okay"
    return "Healthy"


def display_waste_report(report, days, args, print_c):
    print_c(f"\n  ═══ Waste Report · last {days} days ═══", "\033[1;35m", args.no_color)

    if not report:
        print_c(
            "\n    (no reset events recorded yet — keep running `limitlens` for a few days,",
            "\033[33m", args.no_color,
        )
        print_c(
            "     or set up a cron/launchd job to record snapshots automatically.)",
            "\033[33m", args.no_color,
        )
        print_c(
            "    Friendly meaning: waste appears after a quota resets while unused quota was left over.",
            "\033[90m", args.no_color,
        )
        _print_setup_hint(args, print_c)
        return

    items = sorted(report.items(), key=lambda kv: -kv[1]["avg_wasted_pct"])
    total_resets = sum(int(data.get("reset_count") or 0) for _, data in items)
    weighted_avg = sum(
        float(data.get("avg_wasted_pct") or 0.0) * int(data.get("reset_count") or 0)
        for _, data in items
    ) / max(1, total_resets)
    needs_attention = sum(1 for _, data in items if float(data.get("avg_wasted_pct") or 0.0) >= 30)
    worst_key, worst_data = items[0]

    print("\n  " + _box(
        f"LimitLens Waste · last {days} days",
        [
            "Waste means unused quota at reset or missed refill while capped",
            f"Events measured     {total_resets}",
            f"Avg waste           {_bar(weighted_avg)}  {weighted_avg:.1f}%",
            f"Needs attention     {needs_attention} quota{'s' if needs_attention != 1 else ''}",
            f"Worst quota         {_shorten(_friendly_key(worst_key), 34)}",
        ],
    ).replace("\n", "\n  "))

    grouped = {"Needs attention": [], "Okay": [], "Healthy": []}
    for key, data in items:
        grouped[_waste_group(float(data.get("avg_wasted_pct") or 0.0))].append((key, data))

    for title in ("Needs attention", "Okay", "Healthy"):
        rows = grouped[title]
        if not rows:
            continue
        print_c(f"\n  {title}", "\033[1;36m", args.no_color)
        max_rows = len(rows) if getattr(args, "verbose", False) else 5
        for key, data in rows[:max_rows]:
            avg = float(data["avg_wasted_pct"])
            mx = float(data["max_wasted_pct"])
            n = int(data["reset_count"])
            color = "\033[31m" if avg >= 60 else "\033[33m" if avg >= 30 else "\033[32m"
            if data.get("waste_unit") == "usd":
                avg_usd = float(data.get("avg_wasted_usd") or 0.0)
                mx_usd = float(data.get("max_wasted_usd") or 0.0)
                line = f"{_bar(avg)}  ${avg_usd:5.2f} missed refill avg ({avg:4.1f}% cap)"
                action = "use Amp credits before the pool stays capped"
                status = f"{_verdict(avg)} · estimated"
            else:
                mx_usd = None
                line = f"{_bar(avg)}  {avg:5.1f}% unused"
                action = "use this quota earlier before reset"
                status = _verdict(avg)
            print(f"    {_shorten(_friendly_key(key), 30):<30} {_color(line, color, args.no_color)}")
            if title == "Needs attention":
                print(f"      Status: {status} · Action: {action}")
            else:
                print(f"      Status: {status}")
            if getattr(args, "verbose", False):
                print_c(f"      raw: {key}", "\033[90m", args.no_color)
                if mx_usd is not None:
                    print_c(f"      events: {n}, worst: ${mx_usd:.2f} ({mx:.1f}% cap), last seen: {data.get('last_seen_at', 'unknown')}", "\033[90m", args.no_color)
                    for event in data.get("events", []):
                        print_c(f"      event: {event.get('at')} · ${float(event.get('wasted_usd') or 0.0):.2f} missed refill", "\033[90m", args.no_color)
                else:
                    print_c(f"      resets: {n}, worst: {mx:.1f}%, last seen: {data.get('last_seen_at', 'unknown')}", "\033[90m", args.no_color)
                    for event in data.get("events", []):
                        print_c(f"      event: {event.get('at')} · {event.get('wasted_pct')}% unused", "\033[90m", args.no_color)
        hidden = len(rows) - max_rows
        if hidden > 0:
            print_c(f"    +{hidden} more (use --verbose)", "\033[90m", args.no_color)

    print_c(
        "\n  Tip: default is 7 days because waste is reset-based. Use --days 1 for today or --days 30 for trends.",
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
