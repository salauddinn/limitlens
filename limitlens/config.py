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
    "pioneer": {
        "enabled": False,
        "team_id": "",
        "team_name": "",
    },
    "agentrouter": {
        "enabled": False,
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
                expected_type = type(DEFAULT_CONFIG[section][key])
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
    return {
        "auto_hide_enabled": auto_hide_enabled,
        "auto_hide_days": auto_hide_days,
        "amp_usable_pct": amp_usable_pct,
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
