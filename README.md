<div align="center">
  <img src="assets/limitlens_logo.png" alt="LimitLens Logo" width="150" style="border-radius: 20px;"/>
  <h1>LimitLens 💡</h1>
  <p><strong>The ultimate unified quota monitor for AI coding tools.</strong></p>

  [![CI](https://github.com/salauddinn/limitlens/actions/workflows/ci.yml/badge.svg)](https://github.com/salauddinn/limitlens/actions/workflows/ci.yml)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
  [![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
  [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Donate-orange.svg)](https://buymeacoffee.com/salauddin.n)

  <br />
</div>

If you juggle multiple AI subscriptions, tools, and accounts, and frequently run into rate limits, **LimitLens** gives you a **zero-dependency, local CLI tool**—along with a macOS menubar app and an iTerm2 widget—to monitor all of your available quotas in one unified dashboard.

<p align="center">
  <img src="assets/limitlens_status.png" alt="LimitLens Status Dashboard" width="45%" style="border-radius: 8px;">
  &nbsp;&nbsp;&nbsp;
  <img src="assets/limitlens_reco.png" alt="LimitLens Smart Recommendation" width="45%" style="border-radius: 8px;">
</p>

---

## ✨ Features

- **📊 Unified Dashboard:** Instantly view remaining headroom, reset times, and limits across all your installed accounts and profiles.
- **🧠 Smart Recommendations:** Automatically suggests the best tool for the job based on remaining quotas to prevent wasting premium fast requests.
- **⚡ Zero-Dependency CLI:** Written purely in standard Python with absolutely no runtime package dependencies required.
- **🍎 macOS Menubar App:** A sleek native menubar app that lives in your system tray and warns you when you run low.
- **📟 iTerm2 Widget:** Native background script that powers a live, real-time widget directly in your terminal's status bar.
- **🔒 Privacy First:** Never transmits or stores API keys or session cookies. Local outputs automatically redact sensitive paths and emails.

---

## 🚀 Installation

### Quick Install (macOS & Linux)
One command to install everything — automatically detects your OS, installs `pipx` if needed, and optionally registers the menubar app to start at login:

```sh
curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/install.sh | bash
```

### Manual Install (pipx)
Modern systems (like macOS with Homebrew or recent Linux distributions) block global `pip` installs to protect system stability (PEP-668). The best way to install LimitLens globally is using `pipx`.

1. Install `pipx` if you haven't already (e.g., `brew install pipx` or `apt install pipx`).
2. Install LimitLens globally:
   * **For macOS (includes menubar app):**
     ```sh
     pipx install "git+https://github.com/salauddinn/limitlens.git[mac]"
     ```
   * **For Linux / Windows:**
     ```sh
     pipx install "git+https://github.com/salauddinn/limitlens.git"
     ```

### Uninstall
To completely remove LimitLens, the menubar LaunchAgent, and optionally your config:

```sh
curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/uninstall.sh | bash
```



---

## 🔌 Supported Integrations

LimitLens natively parses configs, SQLite databases, and APIs for leading tools. 

| Provider | Supported OS | Notes / Details |
|:---|:---|:---|
| **Codex** | macOS, Linux | Parses local configurations from `~/.codex-*` |
| **Amp** | macOS, Linux | Executes local `amp` binary to fetch quota |
| **Antigravity** | macOS, Linux | Limited to Darwin/Linux configurations |
| **Cursor** | macOS, Linux, Win | Fetches active limits across Cursor tiers |
| **OpenCode** | macOS, Linux, Win | Reads directly from the local OpenCode SQLite database |
| **Pi** | macOS, Linux, Win | Reads local `~/.pi/agent/sessions` JSONL usage data |
| **Pioneer** | Any OS | Reads `PIONEER_API_TOKEN` environment variable and queries API |
| **AgentRouter / Kilo Code** | Any OS | Reads AgentRouter quota via env-authenticated API or custom config |
| **Command Code**| Any OS | Web billing queried using `COMMANDCODE_COOKIE` |

---

## 💻 Usage

Monitor everything with a single command:

```sh
limitlens            # Show full status across all tracked AI tools
limitlens --tool codex  # Filter output to a specific tool
limitlens --watch    # Keep alive and refresh every 5 seconds
limitlens --reco     # Only print the smart AI tool recommendation
limitlens --waste    # Show waste report (% of quota wasted over 7 days)
```

> **Tip:** Codex session data is refreshed automatically before output. You can use `--sync-codex` to forcefully refresh every discovered account, even if current data looks fresh.

---

## 🍎 macOS Menubar App

LimitLens includes a native menubar app so you don't even have to open a terminal to check your limits.

If you installed via `pipx install ...[mac]` (recommended), the `rumps` dependency is already included. Simply run:
```sh
limitlens-menubar
```
It will add a bulb icon to your tray and notify you if your preferred AI quotas run critically low.

---

## 📟 iTerm2 Widget

Bring real-time quota visibility to your terminal window.

1. Open **iTerm2**.
2. Go to **Scripts > Manage > New Python Script**.
3. Choose **Basic** → **Long-Running Daemon** and name it `iterm_widget.py`.
4. Copy the contents of `iterm_widget.py` from this repository into the new file.
5. If `limitlens` is on your PATH (installed via `pipx`), the `USER_LIMITLENS_DIR` variable can be left empty — the widget auto-detects it. Otherwise set it to your clone path.
6. Enable it via **iTerm2 Preferences → Profiles → Session → Configure Status Bar**.

---

## ⚙️ Configuration & Privacy

No configuration is required by default, but LimitLens is highly customizable.
Create a config file at `~/.config/limitlens/config.json`:

```json
{
  "display": {
    "auto_hide_enabled": true,
    "auto_hide_days": 1,
    "amp_usable_pct": 30.0
  },
  "custom_tools": {
    "enabled": true,
    "tools": {
      "kilo": {
        "name": "Kilo Code",
        "total": 84917038,
        "used": 2582962
      }
    }
  }
}
```

### 🔒 Privacy Guarantee
* `limitlens` does **not** store or transmit any sensitive information (such as API keys, secrets, or session cookies).
* All operations are local or direct to the provider's API.
* Any sensitive identifiers in output/logs are automatically redacted on-the-fly (e.g., email addresses are masked as `us***@domain.com`, and absolute home directory paths are replaced with `~`).

---

## 🤝 Contributing & Support

We welcome contributions! To test locally, use the project's virtual environment:
```sh
git clone https://github.com/salauddinn/limitlens.git
cd limitlens
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mac]"
python -m pytest tests/
```

If **LimitLens** has saved you from rate limits, consider buying me a coffee to support continued development!

<a href="https://buymeacoffee.com/salauddin.n" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 40px !important;width: 145px !important;" ></a>
