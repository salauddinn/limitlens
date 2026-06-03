# LimitLens: Product-Market Fit (PMF) Analysis

This document outlines the competitive landscape of AI quota trackers and proxy gateways, identifying the unique Product-Market Fit for **LimitLens** based on deep-dive market research.

---

## 1. The Competitive Landscape

The current market for tracking AI limits, costs, and token consumption is highly fragmented into several distinct categories. Below is a breakdown of the existing tools LimitLens competes against or complements.

### Enterprise AI Gateways & Proxies
*Tools: LiteLLM, Helicone, Portkey*
- **What they do:** These are heavy-duty, observability-first platforms. They act as centralized middleware routing your API requests. They excel at enforcing hard budgets (e.g., LiteLLM's `max_budget`), detailed cost attribution (team vs. user), and advanced caching.
- **The Gap:** They require infrastructure (PostgreSQL, Redis), modifying your app's base URL, and sending your API keys to a middleman server (or self-hosting a proxy). This is complete overkill for an individual developer or power user.

### Desktop Native AI Trackers & Clients
*Tools: CodexBar, Raycast AI, MindMac*
- **What they do:** CodexBar is a direct competitor in this space—a macOS 14+ menubar app that reuses local session data to track quotas for Cursor, Claude Code, and Copilot. Raycast AI and MindMac are local clients but lack centralized dashboarding across other apps.
- **The Gap:** While CodexBar is excellent, it is restricted to macOS 14+ and requires installing a Swift-based binary via Homebrew. It lacks an iTerm2 widget and does not provide programmatic "smart routing" recommendations.

### Web-Based Unified Interfaces
*Tools: TypingMind, LibreChat*
- **What they do:** Unified UI chatting tools.
- **The Gap:** The standard versions of these apps lack centralized quota tracking. They rely on "in-app token estimations." To get real tracking, you need their Enterprise/Teams versions, which again target corporate environments rather than the individual multi-tool developer.

### Developer IDE Tools
*Tools: Cursor, GitHub Copilot*
- **What they do:** IDEs with built-in AI.
- **The Gap:** Tracking "fast requests" in Cursor or "AI Credits" in Copilot requires navigating to web billing dashboards (e.g., `cursor.com/dashboard`). While third-party extensions exist, they are isolated to that specific IDE.

### Local CLI Quota Trackers
*Tools: OpenUsage, caut, aitoken-cli*
- **What they do:** Terminal utilities that parse logs or poll APIs to track usage locally. OpenUsage is a major competitor here, offering a Go-based terminal dashboard that auto-detects local AI agents.
- **The Gap:** OpenUsage is strictly a terminal UI. It does not integrate natively into the macOS Menubar or offer a passive iTerm2 widget. It also requires a compiled Go binary installation.

---

## 2. LimitLens: Unique Differentiators

Based on the market analysis, LimitLens sits in a highly strategic, underserved niche.

> [!TIP]
> **LimitLens is not an AI Gateway.** It is a unified, local-first quota monitor designed specifically for the individual developer juggling multiple subscriptions.

**Key Advantages over CodexBar and OpenUsage:**
1. **Zero-Dependency Python:** Unlike CodexBar (Swift) or OpenUsage (Go), LimitLens is written in pure Python. It runs seamlessly on Linux, older macOS versions, and Windows without needing compiled binaries. It is highly hackable for data scientists and Python devs.
2. **Omni-Channel Presence:** It bridges the gap between CLI and GUI. It provides the terminal reporting of OpenUsage AND the macOS Menubar presence of CodexBar, plus a unique iTerm2 status bar widget.
3. **Smart Tool Recommendations:** LimitLens doesn't just show data; it acts on it. The `--reco` flag analyzes current quotas and suggests the most cost-effective tool currently available based on remaining headroom.
4. **SaaS Waste & Sunk Cost Analytics:** The `--waste` command plays back snapshots to compute how much subscription quota you let go to waste before resets. No competitor (CodexBar, OpenUsage, or gateways) tracks your "sunk subscription cost," helping you decide whether to downgrade or adjust your workflows.
5. **Absolute Privacy:** It parses local SQLite databases, config files, and standard APIs. It reads usage *metadata* locally without transmitting keys.


---

## 3. Product-Market Fit (Target Audience)

### The Ideal Customer Profile (ICP)
**The "AI-Augmented Developer"**
- A senior engineer or indie hacker who pays for multiple AI subscriptions simultaneously (e.g., Cursor Pro, GitHub Copilot, Claude API, and Raycast Pro).
- Frequently hits "fast request" limits and wants to intelligently route their tasks (e.g., "I am out of Cursor fast requests, I should switch to my local OpenCode setup").
- Cares deeply about privacy and refuses to route their codebase through a third-party startup proxy just to track costs.
- Desires a frictionless installation (Python pipx) over managing Homebrew casks or Go binaries.

### Use Case
*A developer is coding heavily in Cursor. They notice the LimitLens iTerm2 widget turn yellow (indicating only 100 fast requests left). LimitLens recommends switching to their local `Amp` binary for boilerplate generation, saving their premium Cursor requests for complex logic.*

---

## 4. Strategic Recommendations

To solidify this PMF against direct competitors like CodexBar and OpenUsage, LimitLens should prioritize the following detailed roadmap features:

### 1. Zero-Config Onboarding (Auto-Detection & Installation Pipeline)
- **Objective:** Match OpenUsage's near-frictionless onboarding experience.
- **Details:** The moment a user runs `limitlens` for the first time, it should automatically:
  - Scan default system paths for active installations and configs (Cursor, Claude Code, GitHub Copilot, env tokens).
  - Automate the setup of the iTerm2 widget. During `install.sh` execution, detect if iTerm2 is installed and automatically copy/symlink `iterm_widget.py` directly into `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/` while configuring `USER_LIMITLENS_DIR` dynamically.
- **Outcome:** A working dashboard and terminal widget set up in under 2 seconds without requiring manual JSON configuration or copy-paste setup steps.

### 2. Enhanced Smart Routing Engine (`--reco`)
- **Objective:** Evolve from a passive monitoring tool to an active workflow routing utility.
- **Details:** Expand the `--reco` command logic to evaluate multiple routing parameters:
  - **Time-to-Reset:** Prioritize using a tool whose quota is close to resetting (e.g., *"Cursor fast requests reset in 10 minutes; exhaust those first before using other models"*).
  - **Model & Task Capability Match:** Align the tool recommendation with the user's current task (e.g., *"Use Amp for boilerplate/repetitive edits, save your premium Codex/Cursor quota for complex logical refactoring"*).
  - **Unit Economics (Dollar-Cost vs. Subscription Status):** Compare the cost-efficiency of using BYOK credits vs. subscription quotas.

### 3. Proactive OS Notifications & Alerts
- **Objective:** Alert users before they hit a rate limit, preventing workflow disruption.
- **Details:** 
  - Provide a threshold-based alert system (e.g., triggering a system notification when a quota drops below 10%).
  - Implement a **"Time to Exhaustion"** metric (similar to `onWatch`) that projects how much time/requests remain at the current rate of consumption.
  - Trigger desktop notifications natively (integrating with macOS Notification Center or using standard desktop notify tools) in addition to the menubar color changes.

### 4. Secure OS Keychain Integration
- **Objective:** Securely store API keys and session tokens.
- **Details:** Instead of requiring users to define plaintext keys in profile files (`PIONEER_API_TOKEN` in `.zshrc`), provide the ability to securely write to and read from the OS Keychain.
- **Outcome:** Zero-dependency wrapper using standard CLI commands (like `/usr/bin/security` on macOS or `secret-tool` on Linux) to retrieve keys securely on demand.

