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
import threading
import sys
import time
import rumps

class LimitLensApp(rumps.App):
    def __init__(self):
        super(LimitLensApp, self).__init__("💡 AI: Loading...")
        self.menu = ["Refresh Now", rumps.separator, "Quit"]
        self._is_fetching = False
        self._pending_title = None
        self._pending_menu_items = None
        self._notified_set = set()
        self._has_loaded_once = False
        self._max_title_items = 3

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

    def _format_title(self, display_items):
        visible = display_items[:self._max_title_items]
        extra = len(display_items) - len(visible)
        suffix = f" +{extra}" if extra > 0 else ""
        return "💡 " + " | ".join(visible) + suffix

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
                    
                    def _emoji(pct):
                        if pct is None: return "⚪"
                        if pct >= 50: return "🟢"
                        if pct >= 15: return "🟡"
                        return "🔴"

                    recs = data.get("recommendations", {})
                    candidates = recs.get("hard", [])
                    
                    display_items = []
                    for item in candidates:
                        tool = item.get("tool", "")
                        pct = item.get("headroom_pct", 0)
                        
                        if tool == "antigravity" and pct < 20:
                            continue
                        if tool != "antigravity" and pct < 10:
                            continue
                            
                        full_name = item.get("name", "Unknown")
                        if " (" in full_name:
                            full_name = full_name.split(" (")[0]
                            
                        if " → " in full_name:
                            prof, model = full_name.split(" → ", 1)
                            prof = prof.split(":", 1)[-1]
                            display_name = f"{prof}:{model.split()[0]}"
                        else:
                            display_name = full_name.replace("codex-", "")
                            
                        display_items.append(f"{_emoji(pct)}{display_name}:{pct:.0f}%")
                    
                    if display_items:
                        self._pending_title = self._format_title(display_items)
                    else:
                        self._pending_title = "🤖 No quotas available"

                    menu_items = []
                    active_keys = set()
                    
                    suppress_notifications = not self._has_loaded_once

                    def check_low_quota(id_str, label, pct, details=""):
                        if pct is None: return
                        active_keys.add(id_str)
                        if pct < 10.0:
                            if not suppress_notifications and id_str not in self._notified_set:
                                self.notify("LimitLens Quota Warning", f"{label} is running low ({pct:.1f}% left). {details}".strip())
                                self._notified_set.add(id_str)
                        elif pct >= 15.0:
                            if id_str in self._notified_set:
                                self._notified_set.remove(id_str)

                    def add_header(title):
                        if menu_items:
                            menu_items.append(rumps.separator)
                        menu_items.append(f"[{title}]")
                    
                    # Codex
                    codex = data.get("codex") or {}
                    has_codex = False
                    for acc in codex.get("accounts", []):
                        if "error" in acc: continue
                        acc_name = acc.get("name", "Codex")
                        for lim in acc.get("limits", []):
                            pct = float(lim.get("left_percent")) if lim.get("left_percent") is not None else None
                            if pct is not None:
                                if not has_codex: add_header("Codex"); has_codex = True
                                label = lim.get("label", "limit")
                                menu_items.append(f"{_emoji(pct)} {acc_name} - {label}: {pct:.0f}% left")
                                check_low_quota(f"codex-{acc_name}-{label}", f"Codex ({acc_name}) {label}", pct)

                    # Amp
                    amp = data.get("amp") or {}
                    has_amp = False
                    for tier in amp.get("tiers", []):
                        pct = float(tier.get("pct_left")) if tier.get("pct_left") is not None else None
                        if pct is not None:
                            if not has_amp: add_header("Amp"); has_amp = True
                            label = tier.get("label", "Amp")
                            rem, tot = tier.get("remaining"), tier.get("total")
                            menu_items.append(f"{_emoji(pct)} {label}: {pct:.1f}% left (${rem:.2f}/${tot:.2f})")
                            check_low_quota(f"amp-{label}", label, pct, f"(${rem:.2f} remaining)")

                    # Antigravity
                    ag = data.get("antigravity") or {}
                    has_ag = False
                    for prof in ag.get("profiles", []):
                        prof_name = prof.get("name", "default")
                        status = prof.get("status", "running")
                        status_suffix = f" [{status}]" if status != "running" else ""
                        for m in prof.get("models", []):
                            pct = float(m.get("pct_left")) if m.get("pct_left") is not None else None
                            if pct is not None:
                                if not has_ag: add_header("Antigravity"); has_ag = True
                                label = m.get("label", "model")
                                menu_items.append(f"{_emoji(pct)} {prof_name} - {label}: {pct:.0f}% left{status_suffix}")
                                if status == "running":
                                    check_low_quota(f"ag-{prof_name}-{label}", f"Antigravity ({prof_name}) {label}", pct)

                    # OpenCode / Credits
                    op_data = data.get("opencode") or {}
                    has_op = False
                    for lim in op_data.get("credit_limits", []):
                        pct = float(lim.get("pct_left")) if lim.get("pct_left") is not None else None
                        if pct is not None:
                            if not has_op: add_header("OpenCode"); has_op = True
                            name = lim.get("name", "credits")
                            rem, tot = lim.get("remaining"), lim.get("total")
                            unit = lim.get("unit") or "credits"
                            unit_sym = "$" if unit.lower() in ("usd", "$") else ""
                            unit_suf = "" if unit_sym else f" {unit}"
                            menu_items.append(f"{_emoji(pct)} {name}: {pct:.1f}% left ({unit_sym}{rem:.2f}/{unit_sym}{tot:.2f}{unit_suf})")
                            check_low_quota(f"opencode-{name}", f"OpenCode ({name})", pct, f"({unit_sym}{rem:.2f} remaining)")

                    # Pioneer
                    pioneer = data.get("pioneer") or {}
                    has_pio = False
                    for tier in pioneer.get("tiers", []):
                        pct = float(tier.get("pct_left")) if tier.get("pct_left") is not None else None
                        if pct is not None:
                            if not has_pio: add_header("Pioneer"); has_pio = True
                            label = tier.get("label", "Pioneer")
                            menu_items.append(f"{_emoji(pct)} {label}: {pct:.1f}% left")
                            check_low_quota(f"pioneer-{label}", label, pct)

                    # Cursor
                    cursor = data.get("cursor") or {}
                    has_cursor = False
                    for tier in cursor.get("tiers", []):
                        pct = float(tier.get("pct_left")) if tier.get("pct_left") is not None else None
                        used = tier.get("used", 0)
                        label = tier.get("label", "Cursor")
                        if not has_cursor: add_header("Cursor"); has_cursor = True
                        if pct is not None:
                            menu_items.append(f"{_emoji(pct)} {label}: {pct:.1f}% left")
                            check_low_quota(f"cursor-{label}", f"Cursor {label}", pct)
                        else:
                            menu_items.append(f"⚪ {label}: {int(used)} used (Unlimited)")

                    self._pending_menu_items = menu_items
                    self._has_loaded_once = True
                else:
                    err_msg = proc.stderr.strip().split("\n")[-1] if proc.stderr else "Unknown error"
                    self._pending_title = f"🤖 Err: {err_msg[:20]}"
            except subprocess.TimeoutExpired:
                self._pending_title = "🤖 Timeout"
            except Exception as e:
                self._pending_title = f"🤖 Err: {str(e)[:15]}"
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

