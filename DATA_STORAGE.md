# LimitLens Data Storage & Privacy

LimitLens is a local-first application. It **never** transmits your data, API keys, or session tokens to external servers. All processing and storage happen locally on your machine.

Here is a comprehensive breakdown of exactly what data LimitLens accesses, stores, and where it is located.

## 1. LimitLens Core Storage

| Component | Path | Description |
|---|---|---|
| **Configuration** | `~/.config/limitlens/config.json` | User configuration overrides, custom tool definitions, and display settings. |
| **Waste Snapshots** | `~/.cache/limitlens/snapshots.jsonl` | An append-only log of your quota snapshots. Used to calculate your "waste report" over time. |
| **Usage Data** | `~/.cache/limitlens/imported_usage.json` | Historical usage data if you choose to import it from an external source. |
| **Antigravity Cache**| `~/.cache/limitlens/antigravity-last.json` | Temporarily caches Antigravity IDE profile statuses to prevent slow UI rendering. |
| **Copilot OTEL** | `~/.cache/limitlens/copilot-otel.jsonl` | Temporarily caches OpenCode/Copilot OTEL traces for observed usage metrics. |

## 2. External Provider Data Accessed (Read-Only)

LimitLens reads data from the following external tools to provide your unified dashboard. It only reads this data; it never modifies it.

| Provider | Path Accessed | Description |
|---|---|---|
| **Codex** | `~/.codex-*` | Reads local Codex profile configurations to check limits. |
| **OpenCode** | `~/.local/share/opencode/opencode.db` | Reads the SQLite database to track observed usage and credits. |
| **Cursor** | `~/Library/Application Support/Cursor/.../state.vscdb` (macOS) <br> `~/.config/Cursor/.../state.vscdb` (Linux) | Reads the SQLite database to fetch active cursor limits. |
| **Pi** | `~/.pi/agent/sessions` | Reads session JSONL data to track Pi usage. |
| **Antigravity** | `~/.gemini/antigravity-cli`<br>`~/Library/Application Support/Antigravity IDE/...`<br>`~/.config/Antigravity/...` | Reads Antigravity configs and CachedProfilesData. |

## 3. Environment Variables

LimitLens can be configured via environment variables (e.g., `LIMITLENS_CONFIG`, `LIMITLENS_SNAPSHOT_PATH`, `LIMITLENS_IMPORTED_USAGE_PATH`) and securely reads authentication tokens for API-based providers via variables like `PIONEER_API_TOKEN` and `COMMANDCODE_COOKIE`.

**Privacy Guarantee:** Any sensitive identifiers found in external databases (like email addresses or absolute home directory paths) are automatically redacted on-the-fly before being displayed or saved to the snapshot logs.
