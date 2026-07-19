"""Amp provider — queries `amp usage` CLI for spend/credit data."""

import re
import subprocess  # nosec B404
from datetime import datetime, timedelta

from limitlens.core import (
    redact_email,
    redact_text,
    format_date_pretty,
    bar,
    print_c,
    section,
    identity_line,
    print_error,
    is_verbose,
    load_display_config,
    load_limitlens_config,
)
from ..logging import get_logger

log = get_logger("limitlens.providers.amp")


# ── Amp helpers ─────────────────────────────────────────────────────────────

def get_amp_data(args):
    try:
        result = subprocess.run(
            ["amp", "usage"],
            capture_output=True, text=True, timeout=15, errors="replace"
        )  # nosec B603 B607
    except FileNotFoundError:
        return {"error": "amp not installed"}
    except (subprocess.SubprocessError, OSError) as e:
        return {"error": f"failed to run amp: {e}"}

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        error = clean_amp_output(output)
        if getattr(args, "redact", True):
            error = redact_text(error)
        return {"error": error or f"exit code {result.returncode}"}

    raw_output = clean_amp_output(output)
    if getattr(args, 'redact', True):
        raw_output = redact_text(raw_output)
    info = {"email": None, "tiers": [], "raw_output": raw_output}

    email_match = re.search(r"Signed in as (\S+)", output)
    if email_match:
        email = email_match.group(1)
        info["email"] = redact_email(email) if getattr(args, 'redact', True) else email

    config = load_limitlens_config()
    show_individual = config.get("amp", {}).get("individual_credits", True)

    for line in output.splitlines():
        clean_line = re.sub(r"\s+-\s+https?://\S+\s*$", "", line.strip())
        quota_match = re.match(
            r"^(.+?):\s+([0-9]+(?:\.[0-9]+)?)%\s+remaining(?:\s+(.+))?$",
            clean_line,
        )
        if quota_match:
            pct_left = min(100.0, max(0.0, float(quota_match.group(2))))
            info["tiers"].append({
                "label": quota_match.group(1).strip(),
                "remaining": None,
                "total": None,
                "used": None,
                "pct_left": pct_left,
                "pct_used": 100.0 - pct_left,
                "reset": (quota_match.group(3) or "").strip() or None,
            })
            continue

        tier_match = re.match(
            r'^(.+):\s+\$([0-9]+(?:\.[0-9]+)?)/\$([0-9]+(?:\.[0-9]+)?)\s+remaining'
            r'(?:\s+\(replenishes\s+\+\$([0-9]+(?:\.[0-9]+)?)/hour\))?',
            line.strip(),
        )
        if tier_match:
            label = tier_match.group(1).strip()
            try:
                remaining = float(tier_match.group(2))
                total = float(tier_match.group(3))
            except ValueError as e:
                log.warning(f"Failed to parse tier values: {e}")
                continue
            replenish = tier_match.group(4)
            pct_left = (remaining / total * 100) if total > 0 else 0
            used = max(0.0, total - remaining) if total is not None else None
            tier = {
                "label": label,
                "remaining": remaining,
                "total": total,
                "used": used,
                "pct_left": pct_left,
                "pct_used": 100.0 - pct_left
            }
            if replenish:
                tier["replenish"] = f"+${replenish}/hour"
                tier["replenish_rate"] = float(replenish)
            info["tiers"].append(tier)
            continue

        credit_match = re.match(
            r'^(.+):\s+\$([0-9]+(?:\.[0-9]+)?)\s+remaining(?!\s*/)',
            line.strip(),
        )
        if credit_match:
            if not show_individual:
                continue
            label = credit_match.group(1).strip()
            try:
                remaining = float(credit_match.group(2))
            except ValueError as e:
                log.warning(f"Failed to parse remaining credit: {e}")
                continue
            info["tiers"].append({
                "label": label,
                "remaining": remaining,
                "total": None,
                "used": None,
                "pct_left": None,
                "pct_used": None,
            })

    disp_cfg = load_display_config()
    for tier in info.get("tiers", []):
        pct_left = tier["pct_left"]
        visible = True
        if disp_cfg["auto_hide_enabled"]:
            if pct_left is not None and pct_left < 10.0:
                rate = tier.get("replenish_rate", 0)
                if rate <= 0:
                    visible = False
                else:
                    target_usable = tier["total"] * (disp_cfg["amp_usable_pct"] / 100.0)
                    hours_to_usable = max(0, (target_usable - tier["remaining"]) / rate)
                    if hours_to_usable > 24:
                        visible = False
        tier["visible"] = visible

    return info

def clean_amp_output(text):
    text = text or ""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return text.strip()

def display_amp_text(data, args):
    if "error" in data:
        section("Amp", args)
        print_error(data["error"], args)
        return

    visible_tiers = []
    for tier in data.get("tiers", []):
        if not tier.get("visible", True) and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
            continue
        visible_tiers.append(tier)

    if not visible_tiers and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        return

    section("Amp", args)
    display_email = data.get("email") or "unknown"
    identity_line("amp", display_email, args)

    if not visible_tiers:
        print_c(f"    {data.get('raw_output', '')}", "\033[90m", getattr(args, 'no_color', False))
        return

    for tier in visible_tiers:
        pct_left = tier["pct_left"]
        pct_used = tier["pct_used"]
        replenish = f"  replenishes {tier['replenish']}" if tier.get("replenish") and is_verbose(args) else ""
        label = tier["label"]
        short = label.split(":")[-1].strip() if ":" in label else label
        short = short.lower().replace(" ", "-")
        if len(short) > 12:
            short = short[:12]

        full_at = ""
        rate = tier.get("replenish_rate", 0)
        if is_verbose(args) and rate > 0 and tier["total"] is not None and tier["remaining"] < tier["total"]:
            hours_left = max(0.0, tier["total"] - tier["remaining"]) / rate
            full_time = datetime.now().astimezone() + timedelta(hours=hours_left)
            full_at = f"  full at {format_date_pretty(full_time)}"

        if pct_left is None:
            # Credit-only tier: no total known, show just the dollar amount
            if getattr(args, 'no_color', False):
                print(f"    {short:<14}   ${tier['remaining']:.2f} remaining{replenish}{full_at}")
            else:
                print(f"    {short:<14}   \033[90m${tier['remaining']:.2f} remaining{replenish}{full_at}\033[0m")
        else:
            b = bar(pct_used, no_color=getattr(args, 'no_color', False))
            used_text = f"  used ${tier.get('used', 0.0):.2f}" if tier.get("used") is not None else ""
            reset = f"  {tier['reset']}" if tier.get("reset") else ""
            if tier.get("remaining") is None or tier.get("total") is None:
                print(f"    {short:<14} {b}  {pct_left:5.1f}% left{reset}")
            elif getattr(args, 'no_color', False):
                print(f"    {short:<14} {b}  {pct_left:5.1f}% left  ${tier['remaining']:.2f}/${tier['total']:.2f}{used_text}{replenish}{full_at}")
            else:
                print(f"    {short:<14} {b}  {pct_left:5.1f}% left  \033[90m${tier['remaining']:.2f}/${tier['total']:.2f}{used_text}{replenish}{full_at}\033[0m")
