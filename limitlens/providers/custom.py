"""Custom provider — config-only quota tracking for user-defined tools."""

from limitlens.core import bar, identity_line, load_display_config, print_c, section
from ..logging import get_logger

log = get_logger("limitlens.providers.custom")


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_amount(value, unit):
    if value is None:
        return "?"
    if unit is None:
        unit = "units"
    normalized = str(unit).lower()
    if normalized in ("$", "usd", "dollars"):
        return f"${value:.2f}"
    if normalized in ("₹", "inr", "rupees"):
        return f"₹{value:.2f}"
    if abs(value) >= 1_000_000:
        amount = f"{value / 1_000_000:.2f}M"
    elif abs(value) >= 1_000:
        amount = f"{value / 1_000:.2f}K"
    elif float(value).is_integer():
        amount = str(int(value))
    else:
        amount = f"{value:.2f}"
    if normalized in ("", "none"):
        return amount
    return f"{amount} {unit}"


def _normalize_tier(raw, default_label, default_unit):
    if not isinstance(raw, dict):
        return None

    label = str(raw.get("label") or raw.get("name") or default_label)
    unit_val = raw.get("unit")
    if unit_val is not None:
        unit = str(unit_val)
    elif default_unit is not None:
        unit = str(default_unit)
    else:
        unit = "units"
    total = _float(raw.get("total") or raw.get("limit"), 0.0)
    remaining_raw = raw.get("remaining") if "remaining" in raw else raw.get("left")
    used_raw = raw.get("used") if "used" in raw else raw.get("spent")
    remaining = _float(remaining_raw, 0.0)
    used = _float(used_raw, 0.0)

    if total <= 0 and remaining > 0 and used > 0:
        total = remaining + used
    elif total <= 0 and remaining > 0:
        total = None
    elif total > 0 and remaining_raw is None and used_raw is None:
        remaining = total
    elif total > 0 and remaining_raw is None:
        remaining = max(0.0, total - used)
    elif total > 0 and used_raw is None:
        used = max(0.0, total - remaining)

    if (total is not None and total <= 0) and remaining <= 0 and used <= 0:
        if not any(k in raw for k in ("total", "limit", "remaining", "left", "used", "spent")):
            return None
        return {
            "label": label,
            "unit": unit,
            "remaining": 0.0,
            "total": 0.0,
            "used": 0.0,
            "pct_left": 0.0,
            "pct_used": 100.0,
        }

    pct_left = (remaining / total * 100.0) if (total is not None and total > 0) else None
    return {
        "label": label,
        "unit": unit,
        "remaining": remaining,
        "total": total,
        "used": used,
        "pct_left": pct_left,
        "pct_used": 100.0 - pct_left if pct_left is not None else None,
    }


def _normalize_tool(tool_id, raw):
    if not isinstance(raw, dict) or not raw.get("enabled", True):
        return None

    unit_val = raw.get("unit")
    unit = str(unit_val) if unit_val is not None else "units"
    tiers = []
    raw_tiers = raw.get("tiers")
    if isinstance(raw_tiers, list):
        for index, tier in enumerate(raw_tiers, start=1):
            normalized = _normalize_tier(tier, f"tier {index}", unit)
            if normalized:
                tiers.append(normalized)
    else:
        normalized = _normalize_tier(raw, raw.get("name") or tool_id, unit)
        if normalized:
            tiers.append(normalized)

    status = raw.get("status") or raw.get("state")
    if not tiers and not status and not raw.get("note"):
        return None

    return {
        "id": str(tool_id),
        "name": str(raw.get("name") or tool_id),
        "command": raw.get("command") or str(tool_id),
        "surface": raw.get("surface") or "cli",
        "quality": raw.get("quality") or "premium",
        "cost_class": raw.get("cost_class") or "prepaid",
        "status": status,
        "note": raw.get("note"),
        "request_count": int(_float(raw.get("request_count"), 0.0)),
        "tiers": tiers,
    }


def get_custom_data(args, config):
    cfg = config.get("custom_tools", {}) if isinstance(config, dict) else {}
    raw_tools = cfg.get("tools") or {}
    if isinstance(raw_tools, list):
        items = [(tool.get("id") or tool.get("name") or f"tool-{idx}", tool) for idx, tool in enumerate(raw_tools, start=1)]
    elif isinstance(raw_tools, dict):
        items = list(raw_tools.items())
    else:
        items = []

    tools = []
    for tool_id, raw in items:
        normalized = _normalize_tool(tool_id, raw)
        if normalized:
            tools.append(normalized)

    disp_cfg = load_display_config()
    for tool in tools:
        for tier in tool["tiers"]:
            tier["visible"] = True
            if disp_cfg["auto_hide_enabled"] and tier["pct_left"] is not None and tier["pct_left"] <= 5.0:
                tier["visible"] = False

    return {"tools": tools}


def display_custom_text(data, args):
    tools = data.get("tools") or []
    visible_tools = []
    show_status_only = getattr(args, "tool", None) == "custom" or getattr(args, "verbose", False) or getattr(args, "all", False)
    for tool in tools:
        visible_tiers = [
            tier for tier in tool.get("tiers", [])
            if tier.get("visible", True) or getattr(args, "verbose", False) or getattr(args, "all", False)
        ]
        if visible_tiers or (show_status_only and (tool.get("status") or tool.get("note"))):
            copy = dict(tool)
            copy["tiers"] = visible_tiers
            visible_tools.append(copy)

    if not visible_tools and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        return

    section("Custom Tools", args)
    no_color = getattr(args, "no_color", False)
    for tool in visible_tools:
        identity_line(tool["id"], tool["name"], args)
        if tool.get("status"):
            print_c(f"    status           {tool['status']}", "\033[90m", no_color)
        for tier in tool.get("tiers", []):
            pct_used = tier.get("pct_used")
            pct_left = tier.get("pct_left")
            b = bar(pct_used if pct_used is not None else 0.0, no_color=no_color)
            remaining = _format_amount(tier["remaining"], tier["unit"])
            total = _format_amount(tier["total"], tier["unit"])
            used = _format_amount(tier["used"], tier["unit"])
            if pct_left is not None:
                print(f"    {tier['label']:<16} {b}  {pct_left:5.1f}% left  {remaining}/{total}  used {used}")
            else:
                print(f"    {tier['label']:<16} {b}    ?% left  {remaining}/{total}  used {used}")
        if tool.get("request_count"):
            print_c(f"    requests         {tool['request_count']}", "\033[90m", no_color)
        tool_filter = getattr(args, "tool", "custom")
        if tool.get("note") and (tool_filter == "custom" or getattr(args, "verbose", False) or getattr(args, "all", False)):
            print_c(f"    note             {tool['note']}", "\033[90m", no_color)
