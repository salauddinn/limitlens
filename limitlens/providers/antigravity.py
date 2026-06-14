"""Antigravity provider — discovers IDE/CLI language servers, queries model quotas via local HTTPS."""

import glob
import json
import os
import platform
import re
import socket
import ssl
import subprocess  # nosec B404
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from limitlens.core import (
    is_reset_passed,
    fmt_reset,
    parse_to_utc,
    format_timestamp,
    bar,
    print_c,
    section,
    identity_line,
    print_warning,
    print_error,
    is_verbose,
    should_show_warning,
    should_show_detail,
    load_display_config,
)

# ── Antigravity helpers ─────────────────────────────────────────────────────

AG_PROBE_TCP_TIMEOUT = 0.2
AG_PROBE_HTTP_TIMEOUT = 1.2
AG_MODEL_HTTP_TIMEOUT = 2.0
AGY_CLI_CONFIG_DIR = os.environ.get("AGY_CONFIG_DIR") or os.path.expanduser("~/.gemini/antigravity-cli")
AGY_CLI_CSRF_TOKEN = "no-token"  # nosec B105

def get_antigravity_named_profiles(sys_name):
    profiles = set()

    profile_roots = [
        os.path.expanduser("~/Library/Application Support/Antigravity IDE/CachedProfilesData"),
        os.path.expanduser("~/Library/Application Support/Antigravity/CachedProfilesData"),
        os.path.expanduser("~/.config/Antigravity IDE/CachedProfilesData"),
        os.path.expanduser("~/.config/Antigravity/CachedProfilesData"),
    ]
    for root in profile_roots:
        for path in glob.glob(os.path.join(root, "*")):
            name = os.path.basename(path)
            if os.path.isdir(path) and name != "__default__profile__":
                profiles.add(name)

    try:
        result = subprocess.run(
            ["ps", "-e", "-ww", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=10, errors="replace"
        )  # nosec B603 B607
    except (subprocess.SubprocessError, OSError) as e:
        return sorted(profiles), f"profile process lookup failed: {e}"

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            for match in re.finditer(r"AntigravityProfiles/([^/\s]+)/", line):
                profiles.add(match.group(1))
    return sorted(profiles), None

def collect_listening_ports(pids, sys_name):
    process_errors = []
    ports = set()
    for pid in pids:
        if sys_name == "Linux":
            try:
                ss_result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=10, errors="replace")  # nosec B603 B607
                if ss_result.returncode != 0:
                    process_errors.append(f"ss failed for pid {pid}: {ss_result.stderr.strip()}")
                    continue
                for line in ss_result.stdout.splitlines():
                    if re.search(rf"\bpid={pid}\b", line):
                        match = re.search(r":(\d+)\s+", line)
                        if match:
                            ports.add(int(match.group(1)))
            except (subprocess.SubprocessError, OSError) as e:
                process_errors.append(f"Failed ss lookup for pid {pid}: {e}")
                continue
        else:
            try:
                lsof_result = subprocess.run(
                    ["lsof", "-a", "-iTCP", "-sTCP:LISTEN", "-P", "-n", "-p", str(pid)],
                    capture_output=True, text=True, timeout=10, errors="replace",
                )  # nosec B603 B607
                if lsof_result.returncode != 0:
                    process_errors.append(f"lsof failed for pid {pid}: {lsof_result.stderr.strip()}")
                    continue
                for line in lsof_result.stdout.splitlines():
                    port_match = re.search(r":(\d+)\s+\(LISTEN\)", line)
                    if port_match:
                        ports.add(int(port_match.group(1)))
            except (subprocess.SubprocessError, OSError) as e:
                process_errors.append(f"Failed lsof lookup for pid {pid}: {e}")
                continue
    detail = "; ".join(process_errors) if process_errors else None
    return sorted(ports), detail

def extract_server_ports_from_command(line):
    https_match = re.search(r"--https_server_port[=\s]+(\d+)", line)
    if https_match:
        return {int(https_match.group(1))}
    return set()

def extract_https_port_token(line):
    port_match = re.search(r"--https_server_port[=\s]+(\d+)", line)
    token_match = re.search(r"--csrf_token[=\s]+(\S+)", line)
    if port_match and token_match:
        return int(port_match.group(1)), token_match.group(1)
    return None, None

def find_language_server_for_profile(profile, sys_name):
    process_errors = []
    try:
        ps_result = subprocess.run(
            ["ps", "-e", "-ww", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=10, errors="replace"
        )  # nosec B603 B607
    except (subprocess.SubprocessError, OSError) as e:
        return None, None, f"Failed to list processes: {e}", {}

    electron_pids = set()
    for line in ps_result.stdout.splitlines():
        if "Electron" in line and f"AntigravityProfiles/{profile}/" in line:
            pid_match = re.match(r"\s*(\d+)\s", line)
            if pid_match:
                electron_pids.add(pid_match.group(1))

    csrf_token = None
    pids = []
    cmd_ports = set()
    port_tokens = {}
    ls_bin = "language_server_macos" if sys_name == "Darwin" else "language_server_linux"
    
    for line in ps_result.stdout.splitlines():
        if ls_bin not in line:
            continue
        direct_match = f"AntigravityProfiles/{profile}/" in line
        if not direct_match:
            continue
        
        token_match = re.search(r"--csrf_token[=\s]+(\S+)", line)
        if token_match:
            csrf_token = token_match.group(1)
        pid_match = re.match(r"\s*(\d+)\s", line)
        if pid_match:
            pids.append(pid_match.group(1))
        cmd_ports.update(extract_server_ports_from_command(line))
        port, token = extract_https_port_token(line)
        if port and token:
            port_tokens[port] = token

    if not pids and electron_pids:
        try:
            ppid_result = subprocess.run(
                ["ps", "-e", "-ww", "-o", "pid=,ppid=,command="],
                capture_output=True, text=True, timeout=10, errors="replace"
            )  # nosec B603 B607
            # Build full descendant set from Electron roots so we catch
            # language servers spawned via intermediary Helper processes
            # (e.g. Electron → Helper Plugin → language_server).
            profile_tree_pids = set(electron_pids)
            changed = True
            while changed:
                changed = False
                for tline in ppid_result.stdout.splitlines():
                    tparts = tline.strip().split(None, 2)
                    if len(tparts) >= 2 and tparts[1] in profile_tree_pids and tparts[0] not in profile_tree_pids:
                        profile_tree_pids.add(tparts[0])
                        changed = True
            for line in ppid_result.stdout.splitlines():
                if ls_bin not in line:
                    continue
                parts = line.strip().split(None, 2)
                if len(parts) >= 2 and parts[1] in profile_tree_pids:
                    pids.append(parts[0])
                    token_match = re.search(r"--csrf_token[=\s]+(\S+)", line)
                    if token_match:
                        csrf_token = token_match.group(1)
                    cmd_ports.update(extract_server_ports_from_command(line))
                    port, token = extract_https_port_token(line)
                    if port and token:
                        port_tokens[port] = token
        except (subprocess.SubprocessError, OSError) as e:
            process_errors.append(f"Failed to inspect parent processes: {e}")

    if not pids or not csrf_token:
        detail = "; ".join(process_errors) if process_errors else None
        return None, None, detail, {}

    ports = sorted(cmd_ports)
    if not ports:
        ports, port_err = collect_listening_ports(pids, sys_name)
        if port_err:
            process_errors.append(port_err)

    detail = "; ".join(process_errors) if process_errors else None
    return csrf_token, ports, detail, port_tokens

def find_language_server_for_main_profile(sys_name, known_profiles):
    try:
        ps_result = subprocess.run(
            ["ps", "-e", "-ww", "-o", "pid=,ppid=,command="],
            capture_output=True, text=True, timeout=10, errors="replace"
        )  # nosec B603 B607
    except (subprocess.SubprocessError, OSError) as e:
        return None, None, f"Failed to list processes: {e}", {}

    ls_bin = "language_server_macos" if sys_name == "Darwin" else "language_server_linux"
    
    profile_electron_pids = set()
    for line in ps_result.stdout.splitlines():
        if "Electron" in line and "AntigravityProfiles/" in line:
            parts = line.strip().split(None, 2)
            if len(parts) >= 1:
                profile_electron_pids.add(parts[0])

    # Build full descendant set from profile Electron roots so we correctly
    # exclude language servers spawned via intermediary Helper processes
    # (e.g. Electron → Helper Plugin → language_server).
    profile_tree_pids = set(profile_electron_pids)
    changed = True
    while changed:
        changed = False
        for tline in ps_result.stdout.splitlines():
            tparts = tline.strip().split(None, 2)
            if len(tparts) >= 2 and tparts[1] in profile_tree_pids and tparts[0] not in profile_tree_pids:
                profile_tree_pids.add(tparts[0])
                changed = True

    csrf_token = None
    pids = []
    cmd_ports = set()
    port_tokens = {}
    
    for line in ps_result.stdout.splitlines():
        if ls_bin not in line:
            continue
            
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
            
        pid = parts[0]
        ppid = parts[1]
        cmd = parts[2]
        
        if "/extensions/antigravity/bin/" not in cmd and "--app_data_dir antigravity" not in cmd:
            continue
            
        if "AntigravityProfiles/" in cmd:
            continue
            
        if ppid in profile_tree_pids:
            continue

        token_match = re.search(r"--csrf_token[=\s]+(\S+)", cmd)
        if token_match:
            csrf_token = token_match.group(1)
        pids.append(pid)
        cmd_ports.update(extract_server_ports_from_command(cmd))
        port, token = extract_https_port_token(cmd)
        if port and token:
            port_tokens[port] = token

    if not pids or not csrf_token:
        return None, None, None, {}

    ports = sorted(cmd_ports)
    if not ports:
        ports, port_err = collect_listening_ports(pids, sys_name)
    else:
        port_err = None
    return csrf_token, ports, port_err, port_tokens

def is_agy_cli_process_command(cmd):
    if not cmd:
        return False
    first = cmd.strip().split(None, 1)[0]
    return os.path.basename(first) == "agy"

def get_config_dir_for_pid(pid, sys_name):
    if sys_name == "Linux":
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read().split(b"\x00")
            for item in env:
                if item.startswith(b"HOME="):
                    home = item.split(b"=", 1)[1].decode("utf-8", errors="replace")
                    return os.path.join(home, ".gemini/antigravity-cli")
        except OSError:
            pass
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
                if ".gemini/antigravity-cli" in target:
                    idx = target.find(".gemini/antigravity-cli")
                    if idx != -1:
                        return target[:idx + len(".gemini/antigravity-cli")]
        except OSError:
            pass
    else:  # macOS / BSD
        try:
            result = subprocess.run(
                ["lsof", "-F", "n", "-p", str(pid)],
                capture_output=True, text=True, timeout=5, errors="replace"
            )  # nosec B603 B607
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("n") and ".gemini/antigravity-cli" in line:
                        path = line[1:]
                        idx = path.find(".gemini/antigravity-cli")
                        if idx != -1:
                            return path[:idx + len(".gemini/antigravity-cli")]
        except (OSError, subprocess.SubprocessError):
            pass
    return None

def get_profile_name_from_config_dir(config_dir):
    default_dir = os.path.expanduser("~/.gemini/antigravity-cli")
    if os.path.realpath(config_dir) == os.path.realpath(default_dir):
        return "agy-cli"
    parent = os.path.dirname(os.path.dirname(config_dir))
    name = os.path.basename(parent)
    return name if name else "agy-cli-custom"

def discover_active_cli_profiles(sys_name):
    active_profiles = {}
    try:
        ps_result = subprocess.run(
            ["ps", "-e", "-ww", "-o", "pid=,ppid=,command="],
            capture_output=True, text=True, timeout=10, errors="replace"
        )  # nosec B603 B607
    except (subprocess.SubprocessError, OSError):
        return active_profiles

    agy_roots = {}
    for line in ps_result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, cmd = parts[0], parts[1], parts[2]
        if is_agy_cli_process_command(cmd):
            config_dir = get_config_dir_for_pid(pid, sys_name)
            if not config_dir:
                config_dir = os.path.expanduser("~/.gemini/antigravity-cli")
            agy_roots[pid] = config_dir

    if not agy_roots:
        return active_profiles

    tree_pids = {pid: set([pid]) for pid in agy_roots}
    changed = True
    while changed:
        changed = False
        for line in ps_result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid, ppid, cmd = parts[0], parts[1], parts[2]
            for root_pid, descendants in tree_pids.items():
                if ppid in descendants and pid not in descendants:
                    descendants.add(pid)
                    changed = True

    for root_pid, config_dir in agy_roots.items():
        name = get_profile_name_from_config_dir(config_dir)
        descendants = tree_pids[root_pid]

        ports = set()
        for line in ps_result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid, ppid, cmd = parts[0], parts[1], parts[2]
            if pid in descendants:
                ports.update(extract_server_ports_from_command(cmd))

        ports = sorted(list(ports))

        if not ports:
            ports, _ = collect_listening_ports(list(descendants), sys_name)

        if name not in active_profiles:
            active_profiles[name] = {
                "pid": root_pid,
                "ports": ports,
                "config_dir": config_dir
            }
        else:
            existing_ports = set(active_profiles[name]["ports"])
            existing_ports.update(ports)
            active_profiles[name]["ports"] = sorted(list(existing_ports))

    return active_profiles

def find_language_server_for_cli(sys_name):
    try:
        ps_result = subprocess.run(
            ["ps", "-e", "-ww", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=10, errors="replace"
        )  # nosec B603 B607
    except (subprocess.SubprocessError, OSError) as e:
        return None, None, f"Failed to list processes: {e}", {}

    pids = []

    for line in ps_result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        if is_agy_cli_process_command(parts[1]):
            pids.append(parts[0])

    if not pids:
        if os.path.exists(os.path.join(AGY_CLI_CONFIG_DIR, "settings.json")):
            return None, None, "agy CLI not running", {}
        return None, None, "agy CLI not configured", {}

    ports, port_err = collect_listening_ports(pids, sys_name)
    return AGY_CLI_CSRF_TOKEN, ports, port_err, {}

def format_ag_error(err):
    if isinstance(err, (TimeoutError, socket.timeout)):
        return "request timed out"
    if isinstance(err, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed for local Antigravity endpoint"
    if isinstance(err, urllib.error.URLError):
        reason = getattr(err, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return "TLS certificate verification failed for local Antigravity endpoint"
        if reason:
            return str(reason)
    return str(err)

def is_tls_verification_error(err):
    if isinstance(err, ssl.SSLCertVerificationError):
        return True
    if isinstance(err, urllib.error.URLError):
        reason = getattr(err, "reason", None)
        return isinstance(reason, ssl.SSLCertVerificationError)
    return False

def is_localhost(host):
    if not host:
        return False
    host = host.lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        # Resolve host to IP addresses and check if they are loopback addresses
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = info[4][0]
            if ip in ("127.0.0.1", "::1") or ip.startswith("127."):
                return True
    except Exception:
        pass
    return False

def log_security_warning(message):
    import sys
    log_dir = os.path.expanduser("~/.cache/limitlens")
    log_file = os.path.join(log_dir, "limitlens.log")
    timestamp = datetime.now().isoformat()
    log_message = f"[{timestamp}] SECURITY WARNING: {message}\n"
    sys.stderr.write(f"SECURITY WARNING: {message}\n")
    sys.stderr.flush()
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_message)
    except Exception:
        pass

def make_ag_request(port, csrf_token, method, body_dict, verify_tls=True, timeout=3, host="127.0.0.1"):
    if verify_tls:
        ctx = ssl.create_default_context()
    else:
        if not is_localhost(host):
            raise ValueError(f"Cannot disable TLS verification for non-localhost host: {host}")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    url = f"https://{host}:{port}/exa.language_server_pb.LanguageServerService/{method}"
    data = json.dumps(body_dict).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Connect-Protocol-Version", "1")
    req.add_header("x-codeium-csrf-token", csrf_token)

    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))

def make_ag_request_with_tls_fallback(port, csrf_token, method, body_dict, timeout=3, host="127.0.0.1"):
    try:
        return make_ag_request(
            port, csrf_token, method, body_dict, verify_tls=True, timeout=timeout, host=host
        ), False
    except (urllib.error.URLError, ssl.SSLError, TimeoutError, socket.timeout) as e:
        if not is_tls_verification_error(e):
            raise
        if not is_localhost(host):
            raise ssl.SSLError(f"Bypassing TLS verification is restricted to localhost/127.0.0.1. Refusing fallback for host: {host}") from e
        log_security_warning(f"TLS verification failed for {host}:{port}. Bypassing verification for local endpoint.")
        resp = make_ag_request(
            port, csrf_token, method, body_dict, verify_tls=False, timeout=timeout, host=host
        )
        return resp, True

def _tcp_port_open(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False

def probe_ag_port(ports, csrf_token, port_tokens=None):
    port_tokens = port_tokens or {}
    body = {
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "ideVersion": "unknown",
            "locale": "en",
        }
    }
    errors = []
    used_insecure_tls = False
    for port in ports:
        request_token = port_tokens.get(port, csrf_token)
        if not _tcp_port_open(port, timeout=AG_PROBE_TCP_TIMEOUT):
            errors.append(f"Port {port} err: TCP closed")
            continue
        try:
            _, insecure = make_ag_request_with_tls_fallback(
                port, request_token, "GetUnleashData", body, timeout=AG_PROBE_HTTP_TIMEOUT
            )
            used_insecure_tls = used_insecure_tls or insecure
            return port, request_token, None, used_insecure_tls
        except (urllib.error.URLError, ssl.SSLError, json.JSONDecodeError, TimeoutError, socket.timeout) as e:
            errors.append(f"Port {port} err: {format_ag_error(e)}")
            continue
    return None, None, "; ".join(errors), used_insecure_tls

def get_ag_model_quotas(port, csrf_token):
    body = {
        "metadata": {
            "ideName": "antigravity",
            "extensionName": "antigravity",
            "ideVersion": "unknown",
            "locale": "en",
        }
    }
    try:
        resp, used_insecure_tls = make_ag_request_with_tls_fallback(
            port, csrf_token, "GetUserStatus", body, timeout=AG_MODEL_HTTP_TIMEOUT
        )
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else ""
        if "token" in err_body.lower() or "auth" in err_body.lower():
            return None, "not_signed_in", False
        return None, err_body or str(e), False
    except (urllib.error.URLError, ssl.SSLError, json.JSONDecodeError, TimeoutError, socket.timeout) as e:
        return None, format_ag_error(e), False

    if "code" in resp and "message" in resp:
        msg = resp.get("message", "")
        if "token" in msg.lower() or "auth" in msg.lower() or "key not found" in msg.lower():
            return None, "not_signed_in", used_insecure_tls
        return None, msg, used_insecure_tls

    models = []
    try:
        user_status = resp.get("userStatus", resp)
        cascade = user_status.get("cascadeModelConfigData", resp.get("cascadeModelConfigData", {}))
        configs = cascade.get("clientModelConfigs", [])
        for cfg in configs:
            label = cfg.get("label", "Unknown")
            quota = cfg.get("quotaInfo", {})
            remaining_frac = float(quota.get("remainingFraction", 0))
            reset_time = (
                quota.get("resetTime")
                or quota.get("quotaResetTime")
                or quota.get("quotaResetTimestamp")
                or cfg.get("resetTime")
                or cfg.get("quotaResetTime")
            )
            pct_left = remaining_frac * 100.0
            if is_reset_passed(reset_time):
                pct_left = 100.0
            models.append({
                "label": label,
                "pct_left": pct_left,
                "reset_time": reset_time,
            })
    except (TypeError, ValueError, AttributeError) as e:
        return None, f"Error parsing models: {e}", used_insecure_tls

    return models, None, used_insecure_tls

KEY_AG_MODELS = {"gemini", "claude", "gpt", "sonnet", "flash", "opus", "pro"}
HIDDEN_AG_MODELS = ("gpt-oss 120b", "claude sonnet 4.6")

def is_key_model(label):
    lower = label.lower()
    return any(k in lower for k in KEY_AG_MODELS)

def antigravity_cache_path():
    home = os.path.expanduser("~")
    return os.path.join(home, ".cache", "limitlens", "antigravity-last.json")

def load_antigravity_cache():
    path = antigravity_cache_path()
    if not os.path.exists(path):
        return {"profiles": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"profiles": {}}
    if not isinstance(data, dict):
        return {"profiles": {}}
    if not isinstance(data.get("profiles"), dict):
        data["profiles"] = {}
    return data

def save_antigravity_cache(cache):
    path = antigravity_cache_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

def try_save_antigravity_cache(cache):
    try:
        save_antigravity_cache(cache)
        return None
    except Exception as e:
        return f"failed to save antigravity cache: {e}"

def cache_antigravity_profile(cache, profile_name, models, config_dir=None):
    fetched_at = datetime.now(timezone.utc).isoformat()
    cache["profiles"][profile_name] = {
        "models": models,
        "fetched_at": fetched_at,
    }
    if config_dir:
        cache["profiles"][profile_name]["config_dir"] = config_dir
    return fetched_at

def format_stale_message(fetched_at, reason):
    if not fetched_at:
        return f"showing old cached data ({reason})"
    try:
        fetched_dt = parse_to_utc(fetched_at)
    except (TypeError, ValueError, OSError):
        return f"showing old cached data ({reason})"

    local_dt = fetched_dt.astimezone()
    age_seconds = max(0, int((datetime.now(timezone.utc) - fetched_dt).total_seconds()))
    hours, minutes = divmod(age_seconds // 60, 60)
    age_text = f"{hours}h {minutes}m ago" if hours else f"{minutes}m ago"
    return f"showing old cached data from {local_dt.strftime('%Y-%m-%d %I:%M %p %Z')} ({age_text}, {reason})"

def apply_antigravity_cached_fallback(prof_data, cache, reason):
    cached = cache.get("profiles", {}).get(prof_data["name"])
    if not cached:
        return False
    models = cached.get("models")
    if not isinstance(models, list) or not models:
        return False
    prof_data.pop("error", None)
    prof_data["status"] = "stale"
    prof_data["models"] = models
    prof_data["stale"] = True
    prof_data["fetched_at"] = cached.get("fetched_at")
    prof_data["warning"] = format_stale_message(cached.get("fetched_at"), reason)
    return True

def _fetch_single_profile(profile, sys_name, cache, is_main=False, known_profiles=None, source="ide", cli_info=None):
    prof_data = {"name": profile, "status": "unknown", "source": source}
    if source == "cli":
        if cli_info:
            csrf_token = AGY_CLI_CSRF_TOKEN
            ports = cli_info["ports"]
            ls_err = None
            port_tokens = {}
            config_dir = cli_info["config_dir"]
        else:
            cached_config_dir = cache.get("profiles", {}).get(profile, {}).get("config_dir")
            if not cached_config_dir:
                cached_config_dir = os.path.expanduser("~/.gemini/antigravity-cli")
            csrf_token = None
            ports = []
            if os.path.exists(os.path.join(cached_config_dir, "settings.json")):
                ls_err = "agy CLI not running"
            else:
                ls_err = "agy CLI not configured"
            port_tokens = {}
            config_dir = cached_config_dir
        
        if config_dir:
            home = os.path.dirname(os.path.dirname(config_dir))
            user_home = os.path.expanduser("~")
            if home.startswith(user_home):
                home_display = home.replace(user_home, "~", 1)
            else:
                home_display = home
            prof_data["home_dir"] = home_display
    elif is_main:
        csrf_token, ports, ls_err, port_tokens = find_language_server_for_main_profile(sys_name, known_profiles or [])
    else:
        csrf_token, ports, ls_err, port_tokens = find_language_server_for_profile(profile, sys_name)

    if not csrf_token or not ports:
        prof_data["status"] = "stopped"
        prof_data["error"] = ls_err or "IDE not running"
        apply_antigravity_cached_fallback(prof_data, cache, prof_data["error"])
        return prof_data, False

    port, selected_token, probe_err, probe_insecure_tls = probe_ag_port(ports, csrf_token, port_tokens)
    if not port:
        prof_data["status"] = "stopped"
        prof_data["error"] = f"language server not responding ({probe_err})"
        apply_antigravity_cached_fallback(prof_data, cache, prof_data["error"])
        return prof_data, False

    models, model_err, model_insecure_tls = get_ag_model_quotas(port, selected_token)
    if probe_insecure_tls or model_insecure_tls:
        prof_data["warning"] = "used insecure local TLS fallback"
    if model_err == "not_signed_in":
        prof_data["status"] = "running"
        prof_data["error"] = "not signed in"
        prof_data["models"] = []
        return prof_data, True
    if model_err:
        prof_data["status"] = "running"
        prof_data["error"] = model_err
        return prof_data, False

    prof_data["status"] = "running"
    def get_priority(lbl):
        lbl = lbl.lower()
        if "sonnet" in lbl:
            return 1
        if "opus" in lbl:
            return 2
        if "pro" in lbl:
            return 3
        if "gemini" in lbl:
            return 4
        return 5

    groups = {}
    for m in models:
        label = m["label"]
        if any(hidden in label.lower() for hidden in HIDDEN_AG_MODELS):
            continue
        base_name = re.sub(r'\s*\((High|Medium|Low|Thinking)\)', '', label, flags=re.IGNORECASE).strip()
        
        lbl_lower = base_name.lower()
        if "gemini" in lbl_lower:
            family = "gemini"
        elif "claude" in lbl_lower or "sonnet" in lbl_lower or "opus" in lbl_lower:
            family = "claude"
        elif "gpt" in lbl_lower or "o1" in lbl_lower or "o3" in lbl_lower:
            family = "gpt"
        else:
            family = lbl_lower

        sig = (family, m["pct_left"], m.get("reset_time"))
        
        if sig not in groups:
            m_copy = dict(m)
            m_copy["label"] = base_name
            groups[sig] = [m_copy]
        else:
            m_copy = dict(m)
            m_copy["label"] = base_name
            groups[sig].append(m_copy)
            
    filtered_models = []
    for sig, group in groups.items():
        best_model = min(group, key=lambda x: get_priority(x["label"]))
        filtered_models.append(best_model)
        
    models = filtered_models

    if not models:
        prof_data["error"] = "no model quota data"
        return prof_data, False

    key_models = [m for m in models if is_key_model(m["label"])]
    display_models = key_models if key_models else models
    prof_data["models"] = display_models
    prof_data["checked_at"] = datetime.now(timezone.utc).isoformat()
    return prof_data, True

def profile_is_ignored(name, ignored_accounts):
    """Return True if *name* matches any entry in *ignored_accounts*."""
    if isinstance(ignored_accounts, str):
        ignored_accounts = [ignored_accounts]
    elif not isinstance(ignored_accounts, list):
        return False
    name_lower = str(name).lower()
    for ignored in ignored_accounts:
        if str(ignored).strip().lower() == name_lower:
            return True
    return False


def filter_antigravity_profiles(profiles, cli_profiles, config=None):
    """Return (filtered_profiles, filtered_cli_profiles) with ignored names removed.

    Uses ``config.antigravity.ignored_accounts`` — a list of profile name
    strings.  Matching is case-insensitive and applied to both named IDE
    profiles and CLI profile keys.
    """
    cfg = (config or {}).get("antigravity", {}) if isinstance(config, dict) else {}
    ignored = cfg.get("ignored_accounts") or []
    filtered_named = [p for p in profiles if not profile_is_ignored(p, ignored)]
    filtered_cli = {k: v for k, v in cli_profiles.items() if not profile_is_ignored(k, ignored)}
    return filtered_named, filtered_cli


def get_antigravity_data(args, config=None):
    sys_name = platform.system()
    if sys_name not in ("Darwin", "Linux"):
        return {"error": f"Antigravity status unsupported on {sys_name}"}

    profiles, err = get_antigravity_named_profiles(sys_name)
    known_profiles = profiles or []
    cache = load_antigravity_cache()
    cache_updated = False
    cache_warning = None

    active_cli = discover_active_cli_profiles(sys_name)

    known_cli_profiles = set(active_cli.keys())
    for cached_name in cache.get("profiles", {}).keys():
        if cached_name not in known_profiles and cached_name != "ide":
            known_cli_profiles.add(cached_name)
    if not known_cli_profiles:
        known_cli_profiles.add("agy-cli")

    # Apply ignored_accounts filter from config
    known_profiles, known_cli_profiles_dict = filter_antigravity_profiles(
        known_profiles,
        {k: active_cli.get(k) for k in known_cli_profiles},
        config,
    )
    known_cli_profiles = set(known_cli_profiles_dict.keys())

    all_profiles = list(known_profiles) + ["ide"] + list(known_cli_profiles)
    results = {}

    with ThreadPoolExecutor(max_workers=len(all_profiles) or 1) as executor:
        futures = {}
        for profile in known_profiles:
            fut = executor.submit(_fetch_single_profile, profile, sys_name, cache)
            futures[fut] = profile
        cfg = (config or {}).get("antigravity", {}) if isinstance(config, dict) else {}
        ignored = cfg.get("ignored_accounts") or []
        if not profile_is_ignored("ide", ignored):
            fut = executor.submit(_fetch_single_profile, "ide", sys_name, cache, is_main=True, known_profiles=known_profiles)
            futures[fut] = "ide"
        
        for cli_prof in known_cli_profiles:
            info = active_cli.get(cli_prof)
            fut = executor.submit(_fetch_single_profile, cli_prof, sys_name, cache, source="cli", cli_info=info)
            futures[fut] = cli_prof

        for fut in as_completed(futures):
            profile = futures[fut]
            prof_data, should_cache = fut.result()
            results[profile] = prof_data
            if should_cache:
                config_dir = None
                if profile in active_cli:
                    config_dir = active_cli[profile]["config_dir"]
                fetched_at = cache_antigravity_profile(cache, profile, prof_data["models"], config_dir=config_dir)
                prof_data["fetched_at"] = fetched_at
                cache_updated = True

    data = [results[p] for p in known_profiles if p in results]
    if "ide" in results:
        data.append(results["ide"])
    for cli_prof in sorted(known_cli_profiles):
        if cli_prof in results:
            data.append(results[cli_prof])

    if not data:
        return {"error": "no profiles found"}

    disp_cfg = load_display_config()
    for prof in data:
        is_stale = prof.get("status") == "stale"
        for m in prof.get("models", []):
            rst = fmt_reset(m.get("reset_time"), is_stale=is_stale)
            days_match = re.search(r'(\d+)\s+days?', rst)
            visible = True
            if disp_cfg["auto_hide_enabled"]:
                if days_match and int(days_match.group(1)) > disp_cfg["auto_hide_days"] and m.get("pct_left", 0.0) < 10.0:
                    visible = False
            m["visible"] = visible

    if cache_updated:
        cache_warning = try_save_antigravity_cache(cache)

    result = {"profiles": data}
    if cache_warning:
        result["warning"] = cache_warning
    if err:
        result["warning"] = err if not result.get("warning") else f"{result['warning']}; {err}"
    return result

def display_antigravity_text(data, args):
    if "error" in data:
        section("Antigravity", args)
        if "⚠" in data["error"] or "not found" in data["error"]:
            print_warning(data["error"], args)
        else:
            print_error(data["error"], args)
        return

    show_detail = getattr(args, "verbose", False) or getattr(args, "all", False) or getattr(args, 'tool', None) == "antigravity"
    visible_profiles = []
    for prof in data.get("profiles", []):
        if prof.get("status") == "stale" and not show_detail:
            continue
        visible_models = []
        for m in prof.get("models", []):
            if not m.get("visible", True) and not show_detail:
                continue
            visible_models.append(m)
        if visible_models or show_detail:
            visible_profiles.append((prof, visible_models))

    if not visible_profiles and not show_detail:
        has_stale = any(p.get("status") == "stale" for p in data.get("profiles", []))
        if has_stale:
            section("Antigravity", args)
            print_c("    (all instances stopped or stale; run with --verbose to view)", "\033[90m", getattr(args, 'no_color', False))
        return

    section("Antigravity", args)
    if "warning" in data and is_verbose(args):
        print_warning(data["warning"], args)

    for prof, visible_models in visible_profiles:
        name = prof["name"]
        status = prof["status"]
        is_stale = status == "stale"
        identity_line(name, None, args, status=status if status in ("running", "stale") else "stopped")

        if "error" in prof:
            if "⚠" in prof["error"] or "not signed in" in prof["error"] or "not running" in prof["error"] or "not responding" in prof["error"] or "no model" in prof["error"]:
                print_warning(prof["error"], args)
            else:
                print_error(prof["error"], args)
            continue

        checked_at = prof.get("checked_at") or prof.get("fetched_at")
        if checked_at and should_show_detail(args):
            try:
                checked_dt = parse_to_utc(checked_at)
                label = "last checked" if prof.get("status") == "running" else "last cached"
                print_c(
                    f"    {label} {format_timestamp(checked_dt)}",
                    "\033[90m",
                    getattr(args, 'no_color', False),
                )
            except ValueError:
                pass
        if "warning" in prof and should_show_warning(prof["warning"], args):
            print_warning(prof["warning"], args)

        # Group models by family
        families = {}
        for m in visible_models:
            label = m["label"].lower()
            if "gemini" in label:
                fam = "Gemini"
            elif "claude" in label or "sonnet" in label or "opus" in label:
                fam = "Claude"
            elif "gpt" in label or "o1" in label or "o3" in label:
                fam = "GPT"
            else:
                fam = "Other"
            families.setdefault(fam, []).append(m)

        for fam, fam_models in families.items():
            if getattr(args, 'no_color', False):
                print(f"    {fam}")
            else:
                print(f"    \033[1m{fam}\033[0m")
                
            # Sort so 5h limit comes before weekly limit
            def sort_key(x):
                try:
                    rt = parse_to_utc(x.get("reset_time"))
                    now = datetime.now(timezone.utc)
                    return (rt - now).total_seconds()
                except Exception:
                    return 0
            fam_models.sort(key=sort_key)

            for m in fam_models:
                pct_left = m["pct_left"]
                pct_used = 100.0 - pct_left
                rst = fmt_reset(m.get("reset_time"), is_stale=is_stale)
                
                limit_label = "unknown limit"
                try:
                    rt = parse_to_utc(m.get("reset_time"))
                    now = datetime.now(timezone.utc)
                    if (rt - now).total_seconds() > 86400:
                        limit_label = "weekly limit"
                    else:
                        limit_label = "5h limit"
                except Exception:
                    pass  # nosec B110
                    
                b = bar(pct_used, no_color=getattr(args, 'no_color', False))
                pct_fmt = f"{pct_left:5.1f}%" if is_verbose(args) else f"{pct_left:5.0f}%"
                if getattr(args, 'no_color', False):
                    print(f"      {limit_label:<14} {b}  {pct_fmt} left  {rst}")
                else:
                    print(f"      {limit_label:<14} {b}  {pct_fmt} left  \033[90m{rst}\033[0m")

