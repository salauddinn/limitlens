#!/usr/bin/env python3
"""
LimitLens Menubar Application.

This module provides a lightweight macOS menubar application using `rumps`.
It polls the LimitLens CLI for quota data and updates the menubar with real-time
remaining quota percentages, helping users avoid rate limits. It also triggers
desktop notifications when quotas run critically low.
"""
import json
import subprocess  # nosec B404
import sys
import threading
import time

import rumps


class LimitLensApp(rumps.App):
    def __init__(self):
        super(LimitLensApp, self).__init__("⏳ Loading...")
        self.menu = ["Refresh Now", rumps.separator, "Quit"]
        self._is_fetching = False
        self._pending_title = None
        self._pending_menu_items = None
        self._notified_set = set()
        self._has_loaded_once = False
        # Keep the macOS menubar compact. Full details live in the dropdown.
        self._max_title_items = 2

    @rumps.timer(300)  # Refresh every 5 minutes
    def refresh(self, _=None):
        self.fetch_data()

    @rumps.clicked("Refresh Now")
    def on_refresh(self, _):
        self.fetch_data()

    @rumps.timer(1)
    def check_updates(self, _):
        if self._pending_title is not None:
            self.title = self._pending_title
            self._pending_title = None
        if self._pending_menu_items is not None:
            self.menu.clear()
            self.menu.add("Refresh Now")
            self.menu.add(rumps.separator)
            if self._pending_menu_items:
                for item in self._pending_menu_items:
                    self.menu.add(item)
            else:
                self.menu.add("No active quotas found")
            self.menu.add(rumps.separator)
            self.menu.add("Quit")
            self._pending_menu_items = None

    def notify(self, title, message):
        script = 'on run argv\n display notification (item 1 of argv) with title (item 2 of argv)\n end run'
        subprocess.Popen(["osascript", "-e", script, message, title])  # nosec B603 B607

    @staticmethod
    def _safe_float(value):
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # Icons for each real LimitLens-tracked tool (consistent across menubar + iTerm)
    TOOL_ICONS = {
        "antigravity": "🪐", "antigrav": "🪐",   # Antigravity
        "codex":        "⚡",                      # OpenAI Codex
        "amp":          "🔥",                      # Amp
        "pioneer":      "🧭",                      # Pioneer
        "agentrouter":  "🔶",  "kilo": "🔶",      # Kilo Code / AgentRouter
        "commandcode":  "🖥️",                     # Command Code
        "copilot":      "✈️",                     # GitHub Copilot
        "cursor":       "🖱️",                     # Cursor IDE
        "custom":       "🔧",                      # Custom configured tools
    }

    @staticmethod
    def _emoji(pct):
        if pct is None:
            return "⚪"
        if pct >= 50:
            return "🟢"
        if pct >= 15:
            return "🟡"
        return "🔴"

    @classmethod
    def _tool_icon(cls, tool_key="", name="", section=""):
        """Return a unique emoji for the tool, falling back to a colored health dot."""
        for source in (tool_key, name, section):
            key = (source or "").lower()
            for k, icon in cls.TOOL_ICONS.items():
                if k in key:
                    return icon
        return "🔷"

    @staticmethod
    def _bar(pct, width=10):
        if pct is None:
            return "─" * width
        pct = max(0.0, min(100.0, float(pct)))
        filled = int((pct / 100.0) * width + 0.5)
        if pct > 0 and filled == 0:
            filled = 1
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _compact(text, max_len=18):
        text = str(text or "")
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    @staticmethod
    def _format_number(value):
        if value is None:
            return "?"
        try:
            value = float(value)
        except (TypeError, ValueError):
            return str(value)
        abs_value = abs(value)
        if abs_value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if abs_value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if abs_value >= 10_000:
            return f"{value / 1_000:.1f}K"
        if value.is_integer():
            return f"{int(value)}"
        return f"{value:.2f}".rstrip("0").rstrip(".")

    @classmethod
    def _format_amount(cls, value, unit=None):
        unit = str(unit or "").strip()
        if value is None:
            return "?"
        if unit.lower() in ("$", "usd"):
            try:
                return f"${float(value):.2f}"
            except (TypeError, ValueError):
                return f"${value}"
        return cls._format_number(value)

    @classmethod
    def _format_ratio(cls, remaining, total, unit=None):
        unit = str(unit or "").strip()
        if unit.lower() in ("%", "% left"):
            return "?" if remaining is None else f"{cls._format_number(remaining)}% left"
        left = cls._format_amount(remaining, unit)
        right = cls._format_amount(total, unit)
        suffix = "" if unit.lower() in ("", "$", "usd") else f" {unit}"
        if total is None:
            return f"{left}{suffix}"
        return f"{left}/{right}{suffix}"

    @classmethod
    def _row(cls, section, name, label, pct_left, remaining=None, total=None, unit=None,
             status=None, used=None, pct_used=None, notify_id=None, notify_label=None):
        pct_left = cls._safe_float(pct_left)
        pct_used = cls._safe_float(pct_used)
        if pct_used is None and pct_left is not None:
            pct_used = max(0.0, 100.0 - pct_left)
        return {
            "section": section,
            "name": name,
            "label": label,
            "pct_left": pct_left,
            "pct_used": pct_used,
            "remaining": remaining,
            "total": total,
            "unit": unit,
            "status": status,
            "used": used,
            "notify_id": notify_id,
            "notify_label": notify_label or f"{section} {label}",
        }

    def _format_title(self, display_items):
        visible = display_items[:self._max_title_items]
        extra = len(display_items) - len(visible)
        suffix = f" +{extra}" if extra > 0 else ""
        return " · ".join(visible) + suffix

    def _recommendation_title_items(self, data, rows):
        recs = (data.get("recommendations") or {}).get("hard") or []
        candidates = [r for r in recs if self._safe_float(r.get("headroom_pct")) is not None]
        fresh = [r for r in candidates if not r.get("stale")]
        source = fresh or candidates

        items = []
        for item in source:
            pct = self._safe_float(item.get("headroom_pct"))
            tool = item.get("tool") or ""
            name = item.get("name") or item.get("tool") or "quota"
            icon = self._tool_icon(tool, name)
            items.append(f"{icon}{pct:.0f}%")

        if items:
            return items

        sortable_rows = [r for r in rows if r.get("pct_left") is not None]
        sortable_rows.sort(key=lambda r: r["pct_left"], reverse=True)
        return [
            f"{self._tool_icon(section=row['section'])}{row['pct_left']:.0f}%"
            for row in sortable_rows
        ]

    def _format_recommendation_row(self, item):
        pct = self._safe_float(item.get("headroom_pct"))
        if pct is None:
            return None
        name = item.get("name") or item.get("tool") or "quota"
        if " (" in name:
            name = name.split(" (", 1)[0]
        if " → " in name:
            profile, model = name.split(" → ", 1)
            profile = profile.split(":", 1)[-1]
            name = f"{profile} / {model}"
        name = name.replace("codex-", "").strip()
        name = {"amp": "Amp", "pioneer": "Pioneer"}.get(name.lower(), name)
        note = item.get("note") or item.get("reset_label") or item.get("command") or ""
        note = f"  · {self._compact(note, 22)}" if note else ""
        return f"{self._tool_icon(item.get('tool', ''), name)}{self._emoji(pct)} {self._compact(name, 22):<22} {pct:5.1f}%  {self._bar(pct)}{note}"

    def _format_usage_row(self, row):
        pct = row.get("pct_left")
        pct_used = row.get("pct_used")
        status = row.get("status")
        left = "  n/a" if pct is None else f"{pct:5.1f}%"
        if pct_used is not None:
            used = f"{pct_used:5.1f}%"
        elif row.get("used") is not None:
            used = f"{self._format_number(row['used'])} used"
        else:
            used = "   n/a"
        ratio = self._format_ratio(row.get("remaining"), row.get("total"), row.get("unit"))
        status_suffix = f"  [{status}]" if status and status != "running" else ""
        return (
            f"{self._tool_icon(section=row['section'])}{self._emoji(pct)} {self._compact(row['section'], 10):<10} "
            f"{self._compact(row['name'], 13):<13} "
            f"{self._compact(row['label'], 18):<18} "
            f"{left:>6}  {used:>7}  {self._bar(pct)}  {ratio}{status_suffix}"
        )

    def _collect_rows(self, data, check_low_quota):
        rows = []

        # Custom configured tools (for example manual Kilo Code quotas)
        custom = data.get("custom") or {}
        for tool in custom.get("tools", []):
            tool_name = tool.get("name") or tool.get("id") or "Custom"
            for tier in tool.get("tiers", []):
                if tier.get("visible", True) is False:
                    continue
                label = tier.get("label", tool_name)
                pct = self._safe_float(tier.get("pct_left"))
                rows.append(self._row(
                    "Custom", tool_name, label, pct,
                    remaining=tier.get("remaining"), total=tier.get("total"), unit=tier.get("unit"),
                    used=tier.get("used"), pct_used=tier.get("pct_used"), status=tool.get("status"),
                    notify_id=f"custom-{tool.get('id', tool_name)}-{label}", notify_label=tool_name,
                ))
                check_low_quota(f"custom-{tool.get('id', tool_name)}-{label}", tool_name, pct)

        # AgentRouter / Kilo Code API quotas
        agentrouter = data.get("agentrouter") or {}
        if agentrouter and "error" not in agentrouter:
            ar_name = agentrouter.get("display_name") or agentrouter.get("username") or agentrouter.get("group") or "Kilo Code"
            for tier in agentrouter.get("tiers", []):
                if tier.get("visible", True) is False:
                    continue
                label = tier.get("label", "quota")
                pct = self._safe_float(tier.get("pct_left"))
                rows.append(self._row(
                    "Kilo", ar_name, label, pct,
                    remaining=tier.get("remaining"), total=tier.get("total"), unit=tier.get("unit") or agentrouter.get("unit"),
                    used=tier.get("used"), pct_used=tier.get("pct_used"),
                    notify_id=f"agentrouter-{label}", notify_label=f"Kilo Code {label}",
                ))
                check_low_quota(f"agentrouter-{label}", f"Kilo Code {label}", pct)

        # Codex
        codex = data.get("codex") or {}
        for acc in codex.get("accounts", []):
            if "error" in acc:
                continue
            acc_name = acc.get("name", "Codex")
            for lim in acc.get("limits", []):
                label = lim.get("label", "limit")
                pct = self._safe_float(lim.get("left_percent"))
                rows.append(self._row(
                    "Codex", acc_name, label, pct,
                    remaining=lim.get("remaining"), total=lim.get("total"), unit=lim.get("unit"),
                    notify_id=f"codex-{acc_name}-{label}", notify_label=f"Codex ({acc_name}) {label}",
                ))
                check_low_quota(f"codex-{acc_name}-{label}", f"Codex ({acc_name}) {label}", pct)

        # Amp
        amp = data.get("amp") or {}
        for tier in amp.get("tiers", []):
            if tier.get("visible", True) is False:
                continue
            label = tier.get("label", "Amp")
            pct = self._safe_float(tier.get("pct_left"))
            rows.append(self._row(
                "Amp", "Amp", label, pct,
                remaining=tier.get("remaining"), total=tier.get("total"), unit="$",
                used=tier.get("used"), pct_used=tier.get("pct_used"),
                notify_id=f"amp-{label}", notify_label=label,
            ))
            remaining = tier.get("remaining")
            detail = f"(${remaining:.2f} remaining)" if isinstance(remaining, (int, float)) else ""
            check_low_quota(f"amp-{label}", label, pct, detail)

        # Antigravity
        ag = data.get("antigravity") or {}
        for prof in ag.get("profiles", []):
            prof_name = prof.get("name", "default")
            status = prof.get("status", "running")
            for model in prof.get("models", []):
                if model.get("visible", True) is False:
                    continue
                label = model.get("label", "model")
                pct = self._safe_float(model.get("pct_left"))
                rows.append(self._row(
                    "Antigrav", prof_name, label, pct,
                    remaining=pct, total=100, unit="% left", status=status,
                    notify_id=f"ag-{prof_name}-{label}", notify_label=f"Antigravity ({prof_name}) {label}",
                ))
                if status == "running":
                    check_low_quota(f"ag-{prof_name}-{label}", f"Antigravity ({prof_name}) {label}", pct)

        # OpenCode / credits
        op_data = data.get("opencode") or {}
        for lim in op_data.get("credit_limits", []):
            name = lim.get("name", "credits")
            pct = self._safe_float(lim.get("pct_left"))
            unit = lim.get("unit") or "credits"
            rows.append(self._row(
                "OpenCode", "OpenCode", name, pct,
                remaining=lim.get("remaining"), total=lim.get("total"), unit=unit,
                used=lim.get("used"), pct_used=lim.get("pct_used"),
                notify_id=f"opencode-{name}", notify_label=f"OpenCode ({name})",
            ))
            rem = lim.get("remaining")
            unit_sym = "$" if str(unit).lower() in ("usd", "$") else ""
            detail = f"({unit_sym}{rem:.2f} remaining)" if isinstance(rem, (int, float)) else ""
            check_low_quota(f"opencode-{name}", f"OpenCode ({name})", pct, detail)

        # Pioneer
        pioneer = data.get("pioneer") or {}
        for tier in pioneer.get("tiers", []):
            if tier.get("visible", True) is False:
                continue
            label = tier.get("label", "Pioneer")
            pct = self._safe_float(tier.get("pct_left"))
            rows.append(self._row(
                "Pioneer", "Pioneer", label, pct,
                remaining=tier.get("remaining"), total=tier.get("total"), unit=tier.get("unit") or pioneer.get("unit"),
                used=tier.get("used"), pct_used=tier.get("pct_used"),
                notify_id=f"pioneer-{label}", notify_label=label,
            ))
            check_low_quota(f"pioneer-{label}", label, pct)

        # Cursor
        cursor = data.get("cursor") or {}
        for tier in cursor.get("tiers", []):
            label = tier.get("label", "Cursor")
            pct = self._safe_float(tier.get("pct_left"))
            rows.append(self._row(
                "Cursor", "Cursor", label, pct,
                remaining=tier.get("remaining"), total=tier.get("total"), unit=tier.get("unit"),
                used=tier.get("used"), pct_used=tier.get("pct_used"),
                notify_id=f"cursor-{label}", notify_label=f"Cursor {label}",
            ))
            check_low_quota(f"cursor-{label}", f"Cursor {label}", pct)

        return rows

    def _build_menu_items(self, data, rows):
        menu_items = []

        def add_header(title):
            if menu_items:
                menu_items.append(rumps.separator)
            menu_items.append(f"[{title}]")

        recs = (data.get("recommendations") or {}).get("hard") or []
        fresh_recs = [r for r in recs if not r.get("stale")]
        rec_source = fresh_recs or recs
        formatted_recs = [self._format_recommendation_row(r) for r in rec_source[:2]]
        formatted_recs = [r for r in formatted_recs if r]
        if formatted_recs:
            add_header("Best available")
            menu_items.extend(formatted_recs)

        if rows:
            add_header("Usage overview")
            menu_items.append("Status Tool       Account       Quota/Model         Left     Used   Bar         Remaining/Total")
            rows = sorted(rows, key=lambda r: (r.get("pct_left") is None, -(r.get("pct_left") or -1)))
            for row in rows:
                menu_items.append(self._format_usage_row(row))

        return menu_items

    def fetch_data(self):
        if self._is_fetching:
            return
        self._is_fetching = True

        def worker():
            try:
                cmd = [sys.executable, "-m", "limitlens", "--json"]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)  # nosec B603

                if proc.returncode == 0:
                    data = json.loads(proc.stdout)
                    active_keys = set()
                    suppress_notifications = not self._has_loaded_once

                    def check_low_quota(id_str, label, pct, details=""):
                        pct = self._safe_float(pct)
                        if pct is None:
                            return
                        active_keys.add(id_str)
                        if pct < 10.0:
                            if not suppress_notifications and id_str not in self._notified_set:
                                self.notify(
                                    "LimitLens Quota Warning",
                                    f"{label} is running low ({pct:.1f}% left). {details}".strip(),
                                )
                                self._notified_set.add(id_str)
                        elif pct >= 15.0 and id_str in self._notified_set:
                            self._notified_set.remove(id_str)

                    rows = self._collect_rows(data, check_low_quota)
                    self._notified_set.intersection_update(active_keys)

                    title_items = self._recommendation_title_items(data, rows)
                    self._pending_title = self._format_title(title_items) if title_items else "⚪ No quota"
                    self._pending_menu_items = self._build_menu_items(data, rows)
                    self._has_loaded_once = True
                else:
                    err_msg = proc.stderr.strip().split("\n")[-1] if proc.stderr else "Unknown error"
                    self._pending_title = f"⚠️ {err_msg[:20]}"
            except subprocess.TimeoutExpired:
                self._pending_title = "⚠️ Timeout"
            except Exception as e:
                self._pending_title = f"⚠️ {str(e)[:15]}"
            finally:
                self._is_fetching = False

        t = threading.Thread(target=worker, daemon=True)
        t.start()


def main():
    app = LimitLensApp()
    # Fetch once before starting the AppKit loop so the UI does not remain
    # stuck at "Loading..." if the first timer tick is delayed.
    app.fetch_data()
    deadline = time.monotonic() + 16
    while app._is_fetching is True and time.monotonic() < deadline:
        time.sleep(0.05)
    app.check_updates(None)
    app.run()


if __name__ == "__main__":
    main()
