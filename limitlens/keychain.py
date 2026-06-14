import subprocess
import sys

SERVICE_NAME = "limitlens"

def set_keychain_token(account: str, token: str) -> bool:
    """Store a token in the OS keychain securely."""
    if sys.platform == "darwin":
        # macOS
        cmd = ["security", "add-generic-password", "-U", "-s", SERVICE_NAME, "-a", account, "-w", token]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False
    elif sys.platform.startswith("linux"):
        # Linux
        cmd = ["secret-tool", "store", "--label", f"LimitLens {account} Token", "service", SERVICE_NAME, "account", account]
        try:
            subprocess.run(cmd, input=token.encode(), check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    return False

def get_keychain_token(account: str) -> str:
    """Retrieve a token from the OS keychain securely."""
    if sys.platform == "darwin":
        # macOS
        cmd = ["security", "find-generic-password", "-s", SERVICE_NAME, "-a", account, "-w"]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None
    elif sys.platform.startswith("linux"):
        # Linux
        cmd = ["secret-tool", "lookup", "service", SERVICE_NAME, "account", account]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
    return None
