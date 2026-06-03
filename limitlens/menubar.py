#!/usr/bin/env python3
"""
LimitLens Menubar Application.

This module provides a lightweight macOS menubar application using `rumps`.
It polls the LimitLens CLI for quota data and updates the menubar with real-time
remaining quota percentages, helping users avoid rate limits. It also triggers
desktop notifications when quotas run critically low.
"""
import json
import os
import subprocess  # nosec B404
import threading
import sys
import rumps

class LimitLensApp(rumps.App):
    def __init__(self):
        super(LimitLensApp, self).__init__("💡 AI: Loading...")
        self.menu = ["Refresh Now", rumps.separator, "Quit"]
        self._is_fetching = False
        self._pending_title = None
        self._pending_menu_items = None
        self._notified_set = set()

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
                            
                        display_items.append(f"{display_name}:{pct:.0f}%")
                    
                    if display_items:
                        self._pending_title = "💡 " + " | ".join(display_items)
                    else:
                        self._pending_title = "🤖 No quotas available"

                    # Extract all active quotas for rich dropdown display
                    menu_items = []
                    active_keys = set()
                    
                    def check_low_quota(id_str, label, pct, details=""):
                        if pct is None:
                            return
                        active_keys.add(id_str)
                        if pct < 10.0:
                            if id_str not in self._notified_set:
                                self.notify("LimitLens Quota Warning", f"{label} is running low ({pct:.1f}% left). {details}".strip())
                                self._notified_set.add(id_str)
                        elif pct >= 15.0:
                            if id_str in self._notified_set:
                                self._notified_set.remove(id_str)
                    
                    # Codex
                    codex = data.get("codex") or {}
                    for acc in codex.get("accounts", []):
                        if "error" in acc:
                            continue
                        acc_name = acc.get("name", "Codex")
                        for lim in acc.get("limits", []):
                            label = lim.get("label", "limit")
                            pct = float(lim.get("left_percent")) if lim.get("left_percent") is not None else None
                            if pct is not None:
                                menu_items.append(f"Codex ({acc_name}) - {label}: {pct:.0f}% left")
                                check_low_quota(f"codex-{acc_name}-{label}", f"Codex ({acc_name}) {label}", pct)

                    # Amp
                    amp = data.get("amp") or {}
                    for tier in amp.get("tiers", []):
                        label = tier.get("label", "Amp")
                        pct = float(tier.get("pct_left")) if tier.get("pct_left") is not None else None
                        rem = tier.get("remaining")
                        tot = tier.get("total")
                        if pct is not None:
                            menu_items.append(f"{label}: {pct:.1f}% left (${rem:.2f}/${tot:.2f})")
                            check_low_quota(f"amp-{label}", label, pct, f"(${rem:.2f} remaining)")

                    # Antigravity
                    ag = data.get("antigravity") or {}
                    for prof in ag.get("profiles", []):
                        prof_name = prof.get("name", "default")
                        status = prof.get("status", "running")
                        status_suffix = f" [{status}]" if status != "running" else ""
                        for m in prof.get("models", []):
                            label = m.get("label", "model")
                            pct = float(m.get("pct_left")) if m.get("pct_left") is not None else None
                            if pct is not None:
                                menu_items.append(f"Antigravity ({prof_name}) - {label}: {pct:.0f}% left{status_suffix}")
                                if status == "running":
                                    check_low_quota(f"ag-{prof_name}-{label}", f"Antigravity ({prof_name}) {label}", pct)

                    # OpenCode / Credits
                    op_data = data.get("opencode") or {}
                    for lim in op_data.get("credit_limits", []):
                        name = lim.get("name", "credits")
                        pct = float(lim.get("pct_left")) if lim.get("pct_left") is not None else None
                        rem = lim.get("remaining")
                        tot = lim.get("total")
                        unit = lim.get("unit") or "credits"
                        if pct is not None:
                            unit_sym = "$" if unit.lower() in ("usd", "$") else ""
                            unit_suf = "" if unit_sym else f" {unit}"
                            menu_items.append(f"OpenCode ({name}): {pct:.1f}% left ({unit_sym}{rem:.2f}/{unit_sym}{tot:.2f}{unit_suf})")
                            check_low_quota(f"opencode-{name}", f"OpenCode ({name})", pct, f"({unit_sym}{rem:.2f} remaining)")

                    # Pioneer
                    pioneer = data.get("pioneer") or {}
                    for tier in pioneer.get("tiers", []):
                        label = tier.get("label", "Pioneer")
                        pct = float(tier.get("pct_left")) if tier.get("pct_left") is not None else None
                        if pct is not None:
                            menu_items.append(f"{label}: {pct:.1f}% left")
                            check_low_quota(f"pioneer-{label}", label, pct)

                    # Cursor
                    cursor = data.get("cursor") or {}
                    for tier in cursor.get("tiers", []):
                        label = tier.get("label", "Cursor")
                        pct = float(tier.get("pct_left")) if tier.get("pct_left") is not None else None
                        used = tier.get("used", 0)
                        if pct is not None:
                            menu_items.append(f"Cursor ({label}): {pct:.1f}% left")
                            check_low_quota(f"cursor-{label}", f"Cursor {label}", pct)
                        else:
                            menu_items.append(f"Cursor ({label}): {int(used)} used (Unlimited)")

                    # Clean up tracked tools that have disappeared

                    self._pending_menu_items = menu_items
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
    # Fetch data immediately before starting the loop
    app.fetch_data()
    app.run()

if __name__ == "__main__":
    main()

