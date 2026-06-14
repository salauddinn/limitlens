import json
import os
import copy

class ConfigValidationError(Exception):
    pass

DEFAULT_CONFIG = {
    "codex": {
        "enabled": True,
        "auto_refresh": True,
        "ignored_accounts": [],
    },
    "cursor": {
        "enabled": True,
    },
    "amp": {
        "enabled": True,
    },
    "antigravity": {
        "enabled": True,
        "ignored_accounts": [],
    },
    "opencode": {
        "enabled": True,
        "db_path": "~/.local/share/opencode/opencode.db",
        "days": [1, 7],
        "providers": [],
        "ignored_models": [],
        "model_parents": {},
        "credit_limits": [],
    },
    "pi": {
        "enabled": True,
        "sessions_dir": "~/.pi/agent/sessions",
        "days": [1, 7],
        "providers": [],
        "ignored_models": [],
        "model_parents": {},
    },
    "copilot_cli": {
        "enabled": True,
        "otel_jsonl_path": "~/.cache/limitlens/copilot-otel.jsonl",
        "days": [1, 7],
    },
    "claude": {
        "enabled": True,
        "sessions_dir": "~/.claude/projects",
        "days": [1, 7],
        "providers": [],
        "ignored_models": [],
        "model_parents": {},
    },
    "pioneer": {
        "enabled": False,
        "team_id": "",
        "team_name": "",
    },
    "agentrouter": {
        "enabled": False,
        "provider": "agentrouter",
        "quota_url": "https://agentrouter.org/api/user/self",
        "unit_label": "units",
    },
    "commandcode": {
        "enabled": False,
        "credits_url": "https://api.commandcode.ai/internal/billing/credits?",
    },
    "custom_tools": {
        "enabled": False,
        "tools": {},
    },
    "display": {
        "auto_hide_enabled": True,
        "auto_hide_days": 1,
        "amp_usable_pct": 30.0,
        "menubar_refresh_seconds": 300,
        "notify_warn_pct": 30.0,
        "notify_critical_pct": 10.0,
    },
}

def deep_merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out

def limitlens_config_path():
    return os.environ.get("LIMITLENS_CONFIG") or os.path.expanduser("~/.config/limitlens/config.json")

def validate_config_types(config, schema_ref=DEFAULT_CONFIG, path=""):
    for key, value in config.items():
        if path == "custom_tools.tools." or path == "pioneer." or path == "agentrouter.":
            continue
        
        if key not in schema_ref:
            raise ConfigValidationError(f"Unknown configuration key: '{path}{key}'")
        
        expected_type = type(schema_ref[key])
        if expected_type is dict:
            if not isinstance(value, dict):
                raise ConfigValidationError(f"Invalid type for '{path}{key}': expected dict, got {type(value).__name__}")
            validate_config_types(value, schema_ref[key], path=f"{path}{key}.")
        elif expected_type is list:
            if not isinstance(value, list):
                raise ConfigValidationError(f"Invalid type for '{path}{key}': expected list, got {type(value).__name__}")
        elif expected_type is bool:
            if not isinstance(value, bool):
                raise ConfigValidationError(f"Invalid type for '{path}{key}': expected bool, got {type(value).__name__}")
        elif expected_type is float:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigValidationError(f"Invalid type for '{path}{key}': expected float, got {type(value).__name__}")
        elif expected_type is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConfigValidationError(f"Invalid type for '{path}{key}': expected int, got {type(value).__name__}")
        elif expected_type is str:
            if not isinstance(value, str):
                raise ConfigValidationError(f"Invalid type for '{path}{key}': expected str, got {type(value).__name__}")

def apply_env_overrides(config):
    for section, section_data in config.items():
        if not isinstance(section_data, dict):
            continue
        for key, expected_value in section_data.items():
            env_key = f"LIMITLENS_{section.upper()}_{key.upper()}"
            if env_key in os.environ:
                raw_val = os.environ[env_key]
                if key in DEFAULT_CONFIG[section]:
                    expected_type = type(DEFAULT_CONFIG[section][key])
                else:
                    expected_type = type(expected_value)
                if expected_type is bool:
                    config[section][key] = raw_val.lower() in ("true", "1", "yes", "t", "y")
                elif expected_type is int:
                    try:
                        config[section][key] = int(raw_val)
                    except ValueError:
                        raise ConfigValidationError(f"Invalid int for {env_key}: {raw_val}")
                elif expected_type is float:
                    try:
                        config[section][key] = float(raw_val)
                    except ValueError:
                        raise ConfigValidationError(f"Invalid float for {env_key}: {raw_val}")
                elif expected_type is str:
                    config[section][key] = raw_val
                elif expected_type is list:
                    config[section][key] = [v.strip() for v in raw_val.split(",") if v.strip()]
                elif expected_type is dict:
                    try:
                        parsed = json.loads(raw_val)
                        if isinstance(parsed, dict):
                            config[section][key] = parsed
                        else:
                            raise ConfigValidationError(f"Invalid dict JSON for {env_key}")
                    except json.JSONDecodeError:
                        raise ConfigValidationError(f"Invalid JSON for {env_key}: {raw_val}")

def load_limitlens_config():
    path = limitlens_config_path()
    config = copy.deepcopy(DEFAULT_CONFIG)
    
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user_config = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigValidationError(f"Invalid JSON in config file {path}: {e}")
        except OSError as e:
            raise ConfigValidationError(f"Failed to read config file {path}: {e}")
            
        if not isinstance(user_config, dict):
            raise ConfigValidationError(f"Config file {path} must contain a JSON object.")
            
        validate_config_types(user_config)
        config = deep_merge(config, user_config)
    else:
        auto_config = auto_detect_providers(path)
        if auto_config:
            config = deep_merge(config, auto_config)
        
    apply_env_overrides(config)
    return config

def load_display_config():
    config = load_limitlens_config()
    disp = config.get("display") or {}
    try:
        auto_hide_enabled = bool(disp.get("auto_hide_enabled", True))
    except (TypeError, ValueError):
        auto_hide_enabled = True
    try:
        auto_hide_days = int(disp.get("auto_hide_days", 1))
    except (TypeError, ValueError):
        auto_hide_days = 1
    try:
        amp_usable_pct = float(disp.get("amp_usable_pct", 30.0))
    except (TypeError, ValueError):
        amp_usable_pct = 30.0
    try:
        menubar_refresh_seconds = int(disp.get("menubar_refresh_seconds", 300))
    except (TypeError, ValueError):
        menubar_refresh_seconds = 300
    try:
        notify_warn_pct = float(disp.get("notify_warn_pct", 30.0))
    except (TypeError, ValueError):
        notify_warn_pct = 30.0
    try:
        notify_critical_pct = float(disp.get("notify_critical_pct", 10.0))
    except (TypeError, ValueError):
        notify_critical_pct = 10.0
    return {
        "auto_hide_enabled": auto_hide_enabled,
        "auto_hide_days": auto_hide_days,
        "amp_usable_pct": amp_usable_pct,
        "menubar_refresh_seconds": menubar_refresh_seconds,
        "notify_warn_pct": notify_warn_pct,
        "notify_critical_pct": notify_critical_pct,
    }

def configured_days(config_section):
    days = config_section.get("days", [1, 7]) if isinstance(config_section, dict) else [1, 7]
    out = []
    for value in days:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if day > 0 and day not in out:
            out.append(day)
    return out or [1, 7]


def is_provider_enabled(config, key, default=True):
    """Return True if the named provider section is enabled.

    Handles boolean, string ("false"/"0"/"no"), and missing values.
    ``default`` controls what to return when the key or "enabled" field
    is absent from the config.
    """
    section = config.get(key)
    if not isinstance(section, dict):
        return default
    raw = section.get("enabled", default)
    if isinstance(raw, bool):
        return raw
    return str(raw).lower() not in ("false", "0", "no")


def reset_custom_tool_spend(config_path):
    """Zero out 'used' and 'request_count' for all custom_tools in config.json.

    Returns True if the file was updated, False if no update was needed or the
    file does not exist. Raises ConfigValidationError on parse/write failure.
    """
    import tempfile
    if not os.path.exists(config_path):
        return False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigValidationError(f"Invalid JSON in {config_path}: {e}")
    except OSError as e:
        raise ConfigValidationError(f"Cannot read {config_path}: {e}")

    if not isinstance(user_config, dict):
        return False

    updated = False
    tools_cfg = (user_config.get("custom_tools") or {}).get("tools", {})
    if isinstance(tools_cfg, dict):
        tools_list = list(tools_cfg.values())
    elif isinstance(tools_cfg, list):
        tools_list = tools_cfg
    else:
        tools_list = []

    for tool_data in tools_list:
        if not isinstance(tool_data, dict):
            continue
        for field in ("used", "request_count"):
            val = tool_data.get(field, 0)
            try:
                if float(val) > 0:
                    tool_data[field] = 0
                    updated = True
            except (TypeError, ValueError):
                pass

    if not updated:
        return False

    dir_path = os.path.dirname(config_path)
    os.makedirs(dir_path, exist_ok=True)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix="config_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(user_config, f, indent=2)
        os.replace(tmp_path, config_path)
        tmp_path = None
    except OSError as e:
        raise ConfigValidationError(f"Cannot write {config_path}: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return True


def auto_detect_providers(path):
    """Scan the local system for installed providers and write an initial config."""
    import sys
    import shutil
    
    detected = copy.deepcopy(DEFAULT_CONFIG)
    for k in detected:
        if isinstance(detected[k], dict) and "enabled" in detected[k]:
            detected[k]["enabled"] = False
            
    found_names = []
    available = {}

    def check(key, found):
        available[key] = bool(found)
        if found:
            found_names.append(key)

    def safe_exists(p):
        try:
            return os.path.exists(os.path.expanduser(p))
        except Exception:
            return False

    def safe_which(cmd):
        try:
            return bool(shutil.which(cmd))
        except Exception:
            return False

    check("cursor", safe_exists("~/Library/Application Support/Cursor") or safe_exists("~/.cursor"))
    check("codex", safe_exists("~/.config/codex") or safe_exists("~/.codex"))
    check("amp", safe_which("amp") or safe_exists("~/.amp"))
    check("antigravity", safe_exists("~/.agy-p1-home") or safe_exists("~/.config/agy"))
    check("pi", safe_exists("~/.pi/agent/sessions"))
    check("pioneer", "PIONEER_API_TOKEN" in os.environ)
    check("agentrouter", safe_exists("~/.config/agentrouter"))
    check("opencode", safe_exists("~/.local/share/opencode"))
    check("copilot_cli", safe_exists("~/.cache/limitlens/copilot-otel.jsonl") or safe_exists("~/.config/github-copilot"))
    check("claude", safe_exists("~/.claude") or safe_exists("~/.config/claude"))
    check("commandcode", False)

    is_interactive = "--json" not in sys.argv and sys.stdout.isatty() and sys.stdin.isatty()

    if is_interactive:
        sys.stderr.write("\033[36m[LimitLens]\033[0m First run setup.\n")
        if found_names:
            sys.stderr.write("We detected the following tools on your system:\n")
            for name in found_names:
                sys.stderr.write(f"  - {name}\n")
        else:
            sys.stderr.write("We didn't detect any tools automatically.\n")
            
        sys.stderr.write("\nLet's configure which tools you want to enable in your dashboard:\n")
        
        for key in list(DEFAULT_CONFIG.keys()):
            if not isinstance(DEFAULT_CONFIG[key], dict) or "enabled" not in DEFAULT_CONFIG[key]:
                continue
                
            is_avail = available.get(key, False)
            default_y = "Y/n" if is_avail else "y/N"
            is_detected = " (detected)" if is_avail else ""
            
            try:
                ans = input(f"Enable {key}{is_detected}? [{default_y}]: ").strip().lower()
                if not ans:
                    detected[key]["enabled"] = is_avail
                else:
                    detected[key]["enabled"] = ans in ("y", "yes", "true", "1")
            except (EOFError, KeyboardInterrupt):
                sys.stderr.write("\nSetup aborted. Using auto-detected configuration.\n")
                # fallback to auto-detect
                for k in available:
                    if k in detected:
                        detected[k]["enabled"] = available[k]
                break
        sys.stderr.write("\n")
    else:
        for key in available:
            if key in detected:
                detected[key]["enabled"] = available[key]

    try:
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(detected, f, indent=2)
            
        if "--json" not in sys.argv:
            sys.stderr.write(f"\033[36m[LimitLens]\033[0m Config written to: {path}\n\n")
    except OSError:
        pass
        
    return detected
