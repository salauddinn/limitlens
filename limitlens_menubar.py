#!/usr/bin/env python3
import json
import os
import subprocess
import rumps

LIMITLENS_DIR = os.path.dirname(os.path.realpath(__file__))
PYTHON_BIN = os.path.join(LIMITLENS_DIR, ".venv", "bin", "python3")
if not os.path.exists(PYTHON_BIN):
    PYTHON_BIN = "python3"
SCRIPT_PATH = os.path.join(LIMITLENS_DIR, "limitlens.py")

class LimitLensApp(rumps.App):
    def __init__(self):
        super(LimitLensApp, self).__init__("💡 AI: Loading...")
        self.menu = ["Refresh Now", rumps.separator, "Quit"]

    @rumps.timer(300)  # Refresh every 5 minutes
    def refresh(self, _=None):
        self.fetch_data()

    @rumps.clicked("Refresh Now")
    def on_refresh(self, _):
        self.fetch_data()

    def fetch_data(self):
        try:
            if not os.path.exists(SCRIPT_PATH):
                self.title = "🤖 Err: limitlens.py not found"
                return
                
            cmd = [PYTHON_BIN, SCRIPT_PATH, "--json"]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            
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
                    self.title = "💡 " + " | ".join(display_items)
                else:
                    self.title = "🤖 No quotas available"
            else:
                self.title = "🤖 LimitLens: Err"
        except Exception as e:
            self.title = f"🤖 Err: {str(e)[:15]}"

if __name__ == "__main__":
    app = LimitLensApp()
    # Fetch data immediately before starting the loop
    app.fetch_data()
    app.run()
