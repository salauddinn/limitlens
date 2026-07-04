# Contributing to LimitLens

Thank you for your interest in contributing to `limitlens`! We welcome community contributions, including bug fixes, documentation improvements, and adding support for new AI quota providers.

---

## Design Philosophy

*   **Minimal Dependencies:** The core CLI must run using the Python standard library and minimal dependencies (e.g. `keyring`). No heavy third-party package dependencies (like `requests`, `pandas`, or `click`) should be added to the runtime code.
*   **Error Tolerance:** If a provider is not installed, fails to respond, or is offline, the tool must fail gracefully and continue showing the status of other providers rather than raising an unhandled exception.
*   **Privacy First:** Sensitive identifiers like email addresses, API tokens, home directories, and full absolute paths must be redacted or normalized before display in CLI output or widgets. Use the helper functions in `limitlens/core.py`.

---

## How to Add a New Quota Provider

To add a new AI provider (e.g. `supermaven` or `cursor`), follow these steps:

### 1. Create a Provider Module
Create a new file in `limitlens/providers/your_provider.py`. It should expose two primary functions:
*   `get_your_provider_data(args) -> dict`: Queries the local binary, cache file, or API, parses the data, and returns a dictionary.
*   `display_your_provider_text(data, args)`: Receives the data and formats/prints it to the terminal.

Example structure (`limitlens/providers/your_provider.py`):
```python
from limitlens.core import section, identity_line, print_error, bar

def get_your_provider_data(args):
    # Fetch, parse, and handle exceptions cleanly
    try:
        # e.g., read local configuration files or execute a subprocess
        return {"user": "developer", "pct_used": 45.0, "remaining": 55, "total": 100}
    except Exception as e:
        return {"error": f"Failed to retrieve data: {e}"}

def display_your_provider_text(data, args):
    if "error" in data:
        section("Your Provider", args)
        print_error(data["error"], args)
        return

    section("Your Provider", args)
    identity_line("your_provider", data.get("user"), args)
    
    # Render progress bar
    b = bar(data["pct_used"], no_color=args.no_color)
    print(f"    usage  {b}  {100.0 - data['pct_used']:.1f}% left")
```

### 2. Register the Provider
Open `limitlens/providers/__init__.py` and import your functions, then add them to the `PROVIDERS` dictionary:
```python
from .your_provider import get_your_provider_data, display_your_provider_text

PROVIDERS = {
    ...
    "your_provider": (get_your_provider_data, display_your_provider_text),
}
```

---

## Testing Your Changes

We use `pytest` for testing. After implementing a new provider or changing core helper functions:

1. Add tests in the `tests/` directory (e.g., `tests/test_your_provider.py`).
2. Run the test suite:
   ```sh
   python3 -m pytest tests/
   ```

---

## Submitting Your Changes

1. Fork the repository on GitHub.
2. Create a feature branch for your changes:
   ```sh
   git checkout -b feature/add-my-provider
   ```
3. Commit your changes with descriptive messages:
   ```sh
   git commit -m "feat: add support for your_provider"
   ```
4. Push to your branch and create a Pull Request on GitHub.
