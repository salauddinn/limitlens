"""
Recommendation engine: picks which AI coding tool to use next.

Design (from brainstorm):
  • Quotas are perishable inventory. Unused = wasted money/value.
  • Different tasks deserve different tools. Don't burn Opus on a typo;
    don't waste Sonnet weekly bucket because you forgot to open Antigravity.
  • Output 3 parallel recommendations (hard / quick / cli) so the user
    picks the row that matches what they're about to do.
  • Surface "WASTE WATCH" — buckets actively evaporating soon — at top.
  • Surface "SKIP TODAY" — buckets with plenty of runway you don't need
    to touch — at bottom.
  • Copilot is excluded entirely (flat-fee unmetered, doesn't compete).

Each candidate has:
  • headroom_pct, reset_at, reset_label, name, command, note
  • quality:    "premium" | "standard" | "cheap"
  • cost_class: "prepaid" (perishable) | "metered" (replenishing)
  • surface:    "cli" | "ide"
  • stale:      bool (Antigravity cached data only)
  • waste_severity: "urgent" | "slow" | None
  • hours_to_reset: float | None
"""

from datetime import datetime, timezone
import re
from .providers.observed import compact_reco_name

# ── Classification ──────────────────────────────────────────────────────────

# Substring keywords on model labels (case-insensitive).
PREMIUM_MODEL_KEYWORDS = ("opus", "sonnet", "gpt-5", "120b")
PRO_MODEL_KEYWORDS     = ("pro",)  # e.g. "Gemini 3.1 Pro" — premium-ish
CHEAP_MODEL_KEYWORDS   = ("flash", "haiku", "mini")
HARD_AND_WASTE_EXCLUDED_MODELS = ("gpt-oss 120b",)

# Waste-watch thresholds.
WASTE_URGENT_HOURS = 6     # resets in <6h
WASTE_URGENT_PCT   = 30    # and has >30% unused
WASTE_SLOW_HOURS   = 48    # resets in <2 days
WASTE_SLOW_PCT     = 50    # and has >50% unused

# Skip-today thresholds.
SKIP_HOURS = 48            # >2 days runway
SKIP_PCT   = 70            # >70% left

# CLI tier preference threshold.
AMP_CLI_HEALTHY_PCT = 30   # if Amp pool above this, prefer it for CLI
GENERAL_RECO_MIN_HEADROOM_PCT = 10
WASTE_REDUCTION_MIN_HEADROOM_PCT = 10

# Antigravity-displayed headroom is unreliable near the bottom (Claude/Gemini
# both show phantom quota that's actually exhausted). Only recommend Antigravity
# candidates at or above this threshold.
ANTIGRAVITY_MIN_HEADROOM_PCT = 20


def _classify_model_label(label):
    if not label:
        return "standard"
    lower = label.lower()
    # Use regex word boundaries for 'mini' to prevent matching 'gemini'
    if any(k in lower for k in ("flash", "haiku")) or re.search(r'\bmini\b', lower):
        return "cheap"
    if any(k in lower for k in PREMIUM_MODEL_KEYWORDS):
        return "premium"
    if any(k in lower for k in PRO_MODEL_KEYWORDS):
        return "premium"
    return "standard"


def _is_excluded_from_hard_and_waste(candidate):
    label = (candidate.get("model_label") or "").strip().lower()
    return any(k in label for k in HARD_AND_WASTE_EXCLUDED_MODELS)


def _antigravity_below_threshold(candidate):
    """Antigravity candidates only shown at 20%+ headroom —
    its displayed quota is unreliable near the bottom."""
    return (
        candidate.get("tool") == "antigravity"
        and candidate.get("headroom_pct", 0) < ANTIGRAVITY_MIN_HEADROOM_PCT
    )


def _hours_until_reset(reset_at, parse_to_utc):
    if not reset_at:
        return None
    try:
        target = parse_to_utc(reset_at)
        now = datetime.now(timezone.utc)
        return max(0.0, (target - now).total_seconds() / 3600.0)
    except (TypeError, ValueError, OSError):
        return None


def _waste_severity(headroom_pct, hrs_to_reset):
    if hrs_to_reset is None:
        return None
    if hrs_to_reset <= WASTE_URGENT_HOURS and headroom_pct >= WASTE_URGENT_PCT:
        return "urgent"
    if hrs_to_reset <= WASTE_SLOW_HOURS and headroom_pct >= WASTE_SLOW_PCT:
        return "slow"
    return None


def _copilot_fallback_candidate():
    return {
        "tool": "copilot",
        "name": "copilot",
        "command": "use Copilot in editor",
        "headroom_pct": 100.0,
        "reset_at": None,
        "reset_label": "flat-fee",
        "quality": "standard",
        "cost_class": "flat_fee",
        "surface": "editor",
        "stale": False,
        "note": "fallback when paid CLI tools are exhausted",
    }


# ── Candidate builders ──────────────────────────────────────────────────────

def _codex_candidates(codex_data):
    """One candidate per account using its bottleneck window."""
    if not codex_data or "error" in codex_data:
        return []
    out = []
    for acc in codex_data.get("accounts", []):
        if "error" in acc or not acc.get("limits"):
            continue
        bottleneck = min(acc["limits"], key=lambda lim: float(lim.get("left_percent", 0)))
        left = float(bottleneck.get("left_percent", 0))
        if left <= 0.5:
            continue
        stale = bool(bottleneck.get("is_stale"))
        name = f"codex ({bottleneck.get('label', 'limit')})" if acc.get('name', 'Unknown') == 'default' else f"codex-{acc.get('name', 'Unknown')} ({bottleneck.get('label', 'limit')})"
        if stale:
            name += " (stale)"
        out.append({
            "tool": "codex",
            "name": name,
            "command": "codex" if acc.get('name', 'Unknown') == 'default' else f"CODEX_HOME=~/.codex-{acc.get('name', 'Unknown')} codex",
            "headroom_pct": left,
            "reset_at": bottleneck.get("reset_time"),
            "reset_label": bottleneck.get("reset_time_fmt"),
            "quality": "premium",
            "cost_class": "prepaid",
            "surface": "cli",
            "stale": stale,
            "note": f"bottleneck: {bottleneck['label']}",
        })
    return out


def _amp_candidates(amp_data):
    """One pooled candidate. $-metered, replenishing, CLI surface."""
    if not amp_data or "error" in amp_data:
        return []
    tiers = amp_data.get("tiers") or []
    if not tiers:
        return []
    total = sum((t.get("total") or 0) for t in tiers)
    left  = sum(t.get("remaining", 0) for t in tiers)
    if total <= 0 or left <= 0.01:
        return []
    headroom = (left / total) * 100.0
    replenishing = any(t.get("replenish_rate", 0) > 0 for t in tiers)
    note = f"${left:.2f} pool"
    if replenishing:
        note += ", replenishing"
    return [{
        "tool": "amp",
        "name": f"amp ({amp_data.get('email') or 'signed in'})",
        "command": "amp",
        "headroom_pct": headroom,
        "reset_at": None,
        "reset_label": "replenishing" if replenishing else None,
        "quality": "premium",
        "cost_class": "metered",
        "surface": "cli",
        "stale": False,
        "note": note,
    }]


def _antigravity_candidates(ag_data, fmt_reset):
    """One candidate per (profile x model). IDE or CLI surface, prepaid perishable."""
    if not ag_data or "error" in ag_data:
        return []
    out = []
    for prof in ag_data.get("profiles", []):
        if "error" in prof or not prof.get("models"):
            continue
        stale = prof.get("status") == "stale"
        source = prof.get("source") or "ide"
        surface = "cli" if source == "cli" else "ide"
        if source == "cli":
            home_dir = prof.get("home_dir")
            if home_dir and prof["name"] != "agy-cli":
                command = f"HOME={home_dir} agy"
            else:
                command = "agy"
        else:
            command = f'open Antigravity (profile: "{prof["name"]}")'
        for m in prof["models"]:
            left = float(m.get("pct_left", 0))
            if left <= 0.5:
                continue
            label = m.get("label", "Unknown")
            name  = f"antigravity:{prof['name']} → {label}"
            if stale:
                name += " (stale)"
            out.append({
                "tool": "antigravity",
                "name": name,
                "command": command,
                "model_label": label,
                "headroom_pct": left,
                "reset_at": m.get("reset_time"),
                "reset_label": fmt_reset(m.get("reset_time"), is_stale=stale) if m.get("reset_time") else None,
                "quality": _classify_model_label(label),
                "cost_class": "prepaid",
                "surface": surface,
                "stale": stale,
                "note": f"model: {label}",
            })
    return out


def _pioneer_candidates(pioneer_data):
    """One pooled candidate. metered/prepaid, CLI surface."""
    if not pioneer_data or "error" in pioneer_data:
        return []
    tiers = pioneer_data.get("tiers") or []
    if not tiers:
        return []
    total = sum(t.get("total", 0) for t in tiers)
    left  = sum(t.get("remaining", 0) for t in tiers)
    if total <= 0 or left <= 0.01:
        return []
    headroom = (left / total) * 100.0
    note = f"{left:.2f} credits"
    return [{
        "tool": "pioneer",
        "name": f"pioneer ({pioneer_data.get('email') or 'signed in'})",
        "command": "pioneer",
        "headroom_pct": headroom,
        "reset_at": None,
        "reset_label": None,
        "quality": "premium",
        "cost_class": "metered",
        "surface": "cli",
        "stale": False,
        "note": note,
    }]


def _agentrouter_candidates(agentrouter_data):
    """One pooled AgentRouter/Kilo Code candidate."""
    if not agentrouter_data or "error" in agentrouter_data:
        return []
    tiers = agentrouter_data.get("tiers") or []
    if not tiers:
        return []
    total = sum((t.get("total") or 0) for t in tiers)
    left = sum((t.get("remaining") or 0) for t in tiers)
    if total <= 0 or left <= 0.5:
        return []
    headroom = (left / total) * 100.0
    requests = agentrouter_data.get("request_count") or 0
    note = f"{left:.0f}/{total:.0f} units left"
    if requests:
        note += f", {requests} requests"
    return [{
        "tool": "agentrouter",
        "name": f"kilo ({agentrouter_data.get('display_name') or agentrouter_data.get('username') or 'agentrouter'})",
        "command": "use Kilo Code",
        "headroom_pct": headroom,
        "reset_at": None,
        "reset_label": None,
        "quality": "premium",
        "cost_class": "prepaid",
        "surface": "ide",
        "stale": False,
        "note": note,
    }]


def _commandcode_candidates(commandcode_data):
    """One pooled Command Code candidate from available credits."""
    if not commandcode_data or "error" in commandcode_data:
        return []
    available = float(commandcode_data.get("available") or 0.0)
    if available <= 0.01:
        return []
    tiers = commandcode_data.get("tiers") or []
    unit = commandcode_data.get("unit_label") or (tiers[0].get("unit") if tiers else None) or "credits"
    pct_left = tiers[0].get("pct_left") if tiers else None
    headroom = float(pct_left) if pct_left is not None else 100.0
    return [{
        "tool": "commandcode",
        "name": "command code",
        "command": commandcode_data.get("command") or "cmd",
        "headroom_pct": headroom,
        "reset_at": None,
        "reset_label": None,
        "quality": "premium",
        "cost_class": "prepaid",
        "surface": "cli",
        "stale": False,
        "note": f"{available:.4f} {unit} available",
    }]


def _custom_candidates(custom_data):
    """One candidate per configured custom tool using its bottleneck tier."""
    if not custom_data or "error" in custom_data:
        return []
    out = []
    for tool in custom_data.get("tools") or []:
        tiers = tool.get("tiers") or []
        if not tiers:
            continue
        bottleneck = min(tiers, key=lambda tier: float(tier.get("pct_left", 0)))
        left = float(bottleneck.get("pct_left", 0))
        if left <= 0.5:
            continue
        note = tool.get("note") or f"bottleneck: {bottleneck.get('label', 'quota')}"
        out.append({
            "tool": "custom",
            "name": tool.get("name") or tool.get("id") or "custom tool",
            "command": tool.get("command") or tool.get("id") or "custom tool",
            "headroom_pct": left,
            "reset_at": None,
            "reset_label": None,
            "quality": tool.get("quality") or "premium",
            "cost_class": tool.get("cost_class") or "prepaid",
            "surface": tool.get("surface") or "cli",
            "stale": False,
            "note": note,
        })
    return out


def _claude_candidates(claude_data):
    if not claude_data or not claude_data.get("models"):
        return []
    models = claude_data["models"]
    cands = []
    for m in models:
        cands.append({
            "tool": "claude",
            "name": f"claude ({m.get('id', 'unknown')})",
            "command": "claude",
            "headroom_pct": float(m.get("pct_left", 100.0)),
            "reset_at": m.get("reset_at"),
            "reset_label": m.get("reset_label"),
            "quality": "premium",
            "cost_class": "prepaid" if m.get("limit") else "metered",
            "surface": "cli",
            "stale": claude_data.get("stale", False),
            "note": "Claude CLI provider",
        })
    return cands


def _all_candidates(result, parse_to_utc, fmt_reset):
    result = result or {}
    cands = []
    cands += _codex_candidates(result.get("codex"))
    cands += _claude_candidates(result.get("claude"))
    cands += _amp_candidates(result.get("amp"))
    cands += _pioneer_candidates(result.get("pioneer"))
    cands += _agentrouter_candidates(result.get("agentrouter"))
    cands += _commandcode_candidates(result.get("commandcode"))
    cands += _custom_candidates(result.get("custom"))
    cands += _antigravity_candidates(result.get("antigravity"), fmt_reset)
    for c in cands:
        hrs = _hours_until_reset(c["reset_at"], parse_to_utc)
        c["hours_to_reset"] = hrs
        if not c.get("stale") and c.get("quality") != "cheap":
            c["waste_severity"] = _waste_severity(c["headroom_pct"], hrs)
        else:
            c["waste_severity"] = None
    return cands


# ── Tier pickers ────────────────────────────────────────────────────────────

def _pick_hard(cands):
    """Hard tasks: prepaid premium first, fresh-data first, most-headroom first."""
    eligible = [
        c for c in cands
        if c["quality"] == "premium"
        and c["headroom_pct"] >= GENERAL_RECO_MIN_HEADROOM_PCT
        and not _is_excluded_from_hard_and_waste(c)
        and not _antigravity_below_threshold(c)
    ]
    if not eligible:
        eligible = [
            c for c in cands
            if c["quality"] == "premium"
            and c["headroom_pct"] >= 2.0
            and not _is_excluded_from_hard_and_waste(c)
            and not _antigravity_below_threshold(c)
        ]
    eligible.sort(key=lambda c: (
        0 if not c["stale"] else 1,
        0 if c["cost_class"] == "prepaid" else 1,
        -c["headroom_pct"],
    ))
    return eligible


def _pick_quick(cands):
    """Quick/grunt tasks: most-about-to-be-wasted prepaid quota."""
    eligible = [
        c for c in cands
        if c["cost_class"] == "prepaid"
        and c["headroom_pct"] >= GENERAL_RECO_MIN_HEADROOM_PCT
        and (c["quality"] == "cheap" or c["waste_severity"] == "urgent")
        and not _antigravity_below_threshold(c)
    ]
    if not eligible:
        eligible = [
            c for c in cands
            if c["cost_class"] == "prepaid"
            and c["headroom_pct"] >= GENERAL_RECO_MIN_HEADROOM_PCT
            and not _antigravity_below_threshold(c)
        ]
    eligible.sort(key=lambda c: (
        0 if not c["stale"] else 1,
        0 if c["waste_severity"] == "urgent" else 1,
        0 if c["quality"] == "cheap" else 1,
        c.get("hours_to_reset") if c.get("hours_to_reset") is not None else 1e9,
        -c["headroom_pct"],
    ))
    return eligible


def _pick_cli(cands):
    """CLI/scripting/pair-prog: prefer Amp when healthy, else Codex by headroom."""
    eligible = [
        c for c in cands
        if c["surface"] == "cli"
        and c["headroom_pct"] >= GENERAL_RECO_MIN_HEADROOM_PCT
        and not _antigravity_below_threshold(c)
    ]
    if not eligible:
        return [_copilot_fallback_candidate()]
    eligible.sort(key=lambda c: (
        0 if c["tool"] == "amp" and c["headroom_pct"] >= AMP_CLI_HEALTHY_PCT else 1,
        0 if not c["stale"] else 1,
        -c["headroom_pct"],
    ))
    return eligible


def _waste_watch(cands):
    """Anything actively evaporating. Excludes 'cheap' models (Flash etc.)
    because they're effectively unlimited — flagging them as wasted is noise."""
    items = [
        c for c in cands
        if c.get("waste_severity")
        and not c.get("stale")
        and not _is_excluded_from_hard_and_waste(c)
        and not _antigravity_below_threshold(c)
    ]
    items.sort(key=lambda c: (
        0 if c["waste_severity"] == "urgent" else 1,
        c.get("hours_to_reset") if c.get("hours_to_reset") is not None else 1e9,
    ))
    return items


def _pick_waste_reduction(cands):
    """Best candidate to spend down when the goal is lowering waste."""
    eligible = [
        c for c in cands
        if (
            c["cost_class"] == "prepaid"
            and c["quality"] != "cheap"
            and not c["stale"]
            and c["headroom_pct"] >= WASTE_REDUCTION_MIN_HEADROOM_PCT
            and not _is_excluded_from_hard_and_waste(c)
            and not _antigravity_below_threshold(c)
        )
    ]
    if not eligible:
        return []
    eligible.sort(key=lambda c: (
        0 if c.get("waste_severity") == "urgent" else 1,
        0 if c.get("waste_severity") == "slow" else 1,
        c.get("hours_to_reset") if c.get("hours_to_reset") is not None else 1e9,
        -c["headroom_pct"],
    ))
    return eligible


def _skip_today(cands):
    """Prepaid buckets with plenty of runway — bank them."""
    out = []
    for c in cands:
        hrs = c.get("hours_to_reset")
        if (
            c["cost_class"] == "prepaid"
            and hrs is not None
            and hrs > SKIP_HOURS
            and c["headroom_pct"] > SKIP_PCT
        ):
            out.append(c)
    out.sort(key=lambda c: -c["headroom_pct"])
    return out


# ── Public API ──────────────────────────────────────────────────────────────

def compute_recommendations(result, parse_to_utc, fmt_reset):
    cands = _all_candidates(result, parse_to_utc, fmt_reset)
    return {
        "hard": _pick_hard(cands),
        "quick": _pick_quick(cands),
        "cli": _pick_cli(cands),
        "waste_reduction": _pick_waste_reduction(cands),
        "waste_watch": _waste_watch(cands),
        "skip_today": _skip_today(cands),
        "all_candidates": cands,
    }


# ── Display ─────────────────────────────────────────────────────────────────

TIER_HEADERS = {
    "hard":  "🧠 Right now (hard task / multi-file refactor):",
    "quick": "⚡️ Right now (quick edit / grunt work):",
    "cli":   "💻 Right now (CLI / scripting / pair-prog):",
}

SUGGESTION_LABELS = (
    ("hard", "Hard task"),
    ("quick", "Quick edit"),
    ("cli", "CLI work"),
)

def _suggestion_reason(top):
    reason = f"{top.get('headroom_pct', 0):.0f}% left"
    reset = top.get("reset_label")
    note = top.get("note")
    if reset:
        reason += f", {reset}"
    if note:
        reason += f", {note}"
    if top.get("stale"):
        reason += ", stale data"
    return reason

def display_suggestions(recs, args, print_c):
    title = "AI suggestion" if getattr(args, "plain", False) else "🎯 AI suggestion"
    print_c(f"\n  {title}", "\033[1;35m", args.no_color)
    for key, label in SUGGESTION_LABELS:
        picks = recs.get(key) or []
        if not picks:
            print_c(f"  {label:<10}: no usable option", "\033[33m", args.no_color)
            continue
        top = picks[0]
        name = compact_reco_name(top.get("name", "unknown"))
        print(f"  {label:<10}: {name} — {_suggestion_reason(top)}")

def _print_tier(key, picks, args, print_c):
    header = TIER_HEADERS[key]
    print_c(f"\n  ➜ {header}", "\033[1;36m", args.no_color)
    if not picks:
        print_c("      (no usable option)", "\033[33m", args.no_color)
        return
    top = picks[0]
    name = compact_reco_name(top['name'])
    pct = top['headroom_pct']
    if args.no_color:
        print(f"      {name:<32} {pct:5.1f}% left")
    else:
        print(f"      \033[1;32m{name:<32}\033[0m \033[1m{pct:5.1f}% left\033[0m")
    print_c(f"      cmd:   {top['command']}", "\033[90m", args.no_color)
    if top.get("note"):
        print_c(f"      why:   {top['note']}", "\033[90m", args.no_color)
    if top.get("reset_label"):
        print_c(f"      reset: {top['reset_label']}", "\033[90m", args.no_color)


def display_recommendations(recs, args, print_c):
    display_suggestions(recs, args, print_c)
    title = "Smart Recommendations" if getattr(args, "plain", False) else "🎯 ═══ Smart Recommendations ═══"
    print_c(f"\n  {title}", "\033[1;35m", args.no_color)

    waste_reduction = recs.get("waste_reduction") or []
    if waste_reduction:
        top = waste_reduction[0]
        print_c("\n  ♻️  Reduce waste most effectively:", "\033[1;33m", args.no_color)
        name = compact_reco_name(top['name'])
        pct = top['headroom_pct']
        if args.no_color:
            print(f"      {name:<32} {pct:5.1f}% left")
        else:
            print(f"      \033[1;33m{name:<32}\033[0m \033[1m{pct:5.1f}% left\033[0m")
        print_c(f"      cmd:   {top['command']}", "\033[90m", args.no_color)
        if top.get("note"):
            print_c(f"      why:   {top['note']}", "\033[90m", args.no_color)
        if top.get("reset_label"):
            print_c(f"      reset: {top['reset_label']}", "\033[90m", args.no_color)

    waste = recs.get("waste_watch") or []
    if waste:
        print_c("\n  🔥 WASTE WATCH (use these soon or lose them)", "\033[1;31m", args.no_color)
        for c in waste[:5]:
            tag = "URGENT" if c["waste_severity"] == "urgent" else "slow"
            reset = c.get("reset_label") or "unknown"
            name = compact_reco_name(c['name'])
            line = f"      • [{tag:<6}] {name:<32} {c['headroom_pct']:5.1f}% left  ({reset})"
            print_c(line, "\033[31m", args.no_color)

    for tier in ("hard", "quick", "cli"):
        _print_tier(tier, recs.get(tier) or [], args, print_c)

    skip = recs.get("skip_today") or []
    if skip:
        print_c("\n  💤 Skip today (plenty of runway, save for later):", "\033[90m", args.no_color)
        for c in skip[:3]:
            reset = c.get("reset_label") or "unknown"
            print_c(f"      • {c['name']:<40} {c['headroom_pct']:5.1f}% left  ({reset})", "\033[90m", args.no_color)

    print_c("\n  💡 Copilot: ignored in planning (flat-fee, just use in editor)", "\033[90m", args.no_color)


def display_one_line(tier, recs, args, print_c):
    """For --hard / --quick / --cli flags: one-line output for shell prompts."""
    picks = recs.get(tier) or []
    if not picks:
        print_c(f"⚠ no {tier} recommendation available", "\033[33m", args.no_color)
        return
    top = picks[0]
    if args.no_color:
        print(f"→ [{tier}] {top['name']}  ({top['headroom_pct']:.0f}% left)  cmd: {top['command']}")
    else:
        print(f"\033[1;32m→ [{tier}] {top['name']}\033[0m  \033[1m({top['headroom_pct']:.0f}% left)\033[0m  \033[90mcmd: {top['command']}\033[0m")
