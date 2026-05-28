"""Amp provider — queries `amp usage` CLI for spend/credit data."""

import re
import subprocess
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
)


# ── Amp helpers ─────────────────────────────────────────────────────────────

def get_amp_data(args):
    try:
        result = subprocess.run(
            ["amp", "usage"],
            capture_output=True, text=True, timeout=15, errors="replace"
        )
    except FileNotFoundError:
        return {"error": "amp not installed"}
    except (subprocess.SubprocessError, OSError) as e:
        return {"error": f"failed to run amp: {e}"}

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return {"error": output.strip() or f"exit code {result.returncode}"}

    raw_output = output.strip()
    if args.redact:
        raw_output = redact_text(raw_output)
    info = {"email": None, "tiers": [], "raw_output": raw_output}

    email_match = re.search(r"Signed in as (\S+)", output)
    if email_match:
        email = email_match.group(1)
        info["email"] = redact_email(email) if args.redact else email

    for line in output.splitlines():
        tier_match = re.match(
            r"(.+?):\s+\$([0-9.]+)/\$([0-9.]+)\s+remaining"
            r"(?:\s+\(replenishes\s+\+\$([0-9.]+)/hour\))?",
            line.strip(),
        )
        if tier_match:
            label = tier_match.group(1).strip()
            remaining = float(tier_match.group(2))
            total = float(tier_match.group(3))
            replenish = tier_match.group(4)
            pct_left = (remaining / total * 100) if total > 0 else 0
            tier = {
                "label": label,
                "remaining": remaining,
                "total": total,
                "pct_left": pct_left,
                "pct_used": 100.0 - pct_left
            }
            if replenish:
                tier["replenish"] = f"+${replenish}/hour"
                tier["replenish_rate"] = float(replenish)
            info["tiers"].append(tier)
            continue

        credit_match = re.match(
            r"(.+?):\s+\$([0-9.]+)\s+remaining",
            line.strip(),
        )
        if credit_match:
            label = credit_match.group(1).strip()
            remaining = float(credit_match.group(2))
            if remaining > 0:
                info["tiers"].append({
                    "label": label,
                    "remaining": remaining,
                    "total": remaining,
                    "pct_left": 100.0,
                    "pct_used": 0.0
                })

    disp_cfg = load_display_config()
    for tier in info.get("tiers", []):
        pct_left = tier["pct_left"]
        visible = True
        if disp_cfg["auto_hide_enabled"]:
            if pct_left < 10.0:
                rate = tier.get("replenish_rate", 0)
                if rate <= 0:
                    visible = False
                else:
                    target_usable = tier["total"] * (disp_cfg["amp_usable_pct"] / 100.0)
                    hours_to_usable = (target_usable - tier["remaining"]) / rate
                    if hours_to_usable > 24:
                        visible = False
        tier["visible"] = visible

    return info

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
        print_c(f"    {data.get('raw_output', '')}", "\033[90m", args.no_color)
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
        if is_verbose(args) and rate > 0 and tier["remaining"] < tier["total"]:
            hours_left = (tier["total"] - tier["remaining"]) / rate
            full_time = datetime.now().astimezone() + timedelta(hours=hours_left)
            full_at = f"  full at {format_date_pretty(full_time)}"

        b = bar(pct_used, no_color=args.no_color)
        if args.no_color:
            print(f"    {short:<14} {b}  {pct_left:5.1f}% left  ${tier['remaining']:.2f}/${tier['total']:.2f}{replenish}{full_at}")
        else:
            print(f"    {short:<14} {b}  {pct_left:5.1f}% left  \033[90m${tier['remaining']:.2f}/${tier['total']:.2f}{replenish}{full_at}\033[0m")
