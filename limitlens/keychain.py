import subprocess  # nosec B404
import sys

SERVICE_NAME = "limitlens"

_keyring_available = False
try:
    import keyring
    import keyring.errors
    # If the active keyring is the FailKeyring, no viable backend is available.
    backend = keyring.get_keyring()
    if backend and backend.__class__.__name__ != 'FailKeyring':
        _keyring_available = True
except Exception:
    pass

def set_keychain_token(account: str, token: str) -> bool:
    """Store a token in the OS keychain securely."""
    if _keyring_available:
        try:
            keyring.set_password(SERVICE_NAME, account, token)
            return True
        except Exception:
            # Fall back to subprocess
            pass

    if sys.platform == "darwin":
        # macOS: passing token via CLI argument exposes it in `ps`. Require keyring.
        print("\n[LimitLens] ERROR: Setting tokens securely requires the 'keyring' package. Please run 'pip install keyring'.\n", file=sys.stderr)
        return False
    elif sys.platform.startswith("linux"):
        # Linux
        cmd = ["secret-tool", "store", "--label", f"LimitLens {account} Token", "service", SERVICE_NAME, "account", account]
        try:
            subprocess.run(cmd, input=token.encode(), check=True, capture_output=True)  # nosec B603
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    return False

def get_keychain_token(account: str) -> str:
    """Retrieve a token from the OS keychain securely."""
    if _keyring_available:
        try:
            val = keyring.get_password(SERVICE_NAME, account)
            if val is not None:
                return val
        except Exception:
            # Fall back to subprocess
            pass

    if sys.platform == "darwin":
        # macOS
        cmd = ["security", "find-generic-password", "-s", SERVICE_NAME, "-a", account, "-w"]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)  # nosec B603
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None
    elif sys.platform.startswith("linux"):
        # Linux
        cmd = ["secret-tool", "lookup", "service", SERVICE_NAME, "account", account]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)  # nosec B603
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
    return None
