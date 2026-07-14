import subprocess  # nosec B404
import sys

from .logging import get_logger
log = get_logger("limitlens.keychain")

SERVICE_NAME = "limitlens"

_keyring_available = False
try:
    import keyring
    import keyring.errors
    # If the active keyring is the FailKeyring, no viable backend is available.
    backend = keyring.get_keyring()
    if backend and backend.__class__.__name__ != 'FailKeyring':
        _keyring_available = True
except Exception as e:
    log.debug("keyring initialization failed: %s", e)

def set_keychain_token(account: str, token: str) -> bool:
    """Store a token in the OS keychain securely."""
    if _keyring_available:
        try:
            keyring.set_password(SERVICE_NAME, account, token)
            return True
        except Exception as e:
            # Fall back to subprocess
            log.debug("keyring.set_password failed, falling back to subprocess: %s", e)

    if sys.platform == "darwin":
        # macOS: passing token via CLI argument exposes it in `ps`. Require keyring.
        log.error("Setting tokens securely requires the 'keyring' package. Please run 'pip install keyring'.")
        return False
    elif sys.platform.startswith("linux"):
        # Linux
        cmd = ["secret-tool", "store", "--label", f"LimitLens {account} Token", "service", SERVICE_NAME, "account", account]
        try:
            subprocess.run(cmd, input=token.encode(), check=True, capture_output=True)  # nosec B603
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    elif sys.platform == "win32":
        # Bug 12: Windows fallback via cmdkey (built-in credential manager CLI).
        target = f"{SERVICE_NAME}:{account}"
        cmd = ["cmdkey", f"/add:{target}", f"/user:{account}", f"/pass:{token}"]
        try:
            subprocess.run(cmd, check=True, capture_output=True)  # nosec B603
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
        except Exception as e:
            # Fall back to subprocess
            log.debug("keyring.get_password failed, falling back to subprocess: %s", e)

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
    elif sys.platform == "win32":
        # Bug 12: Windows fallback — read via cmdkey /list and parse, or use a
        # temp credential file as cmdkey cannot print the stored password directly.
        # For retrieval, we rely on the keyring package; log a helpful message.
        log.warning(
            "Token retrieval on Windows without keyring is not supported. "
            "Install 'keyring': pip install keyring"
        )
        return None
    return None
