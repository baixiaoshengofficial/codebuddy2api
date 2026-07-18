"""
Configuration management for CodeBuddy2API

Implements a multi-layered configuration system with hot-reloading.
Priority order:
1. In-memory config (for hot-settings from the UI)
2. config.json file (for persisted user overrides)
3. Environment variables (for deployment, e.g., Docker)
4. Hard-coded defaults
"""
import os
import json
import logging
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --- Private State ---
_config_cache: Dict[str, Any] = {}
_persisted_config: Dict[str, Any] = {}
_CONFIG_JSON_PATH = 'config/config.json'  # Use a path inside a directory

_CODEBUDDY_SITE_ENDPOINTS = {
    "international": "https://www.codebuddy.ai",
    "china": "https://copilot.tencent.com",
}

_CODEBUDDY_SITE_ALIASES = {
    "intl": "international",
    "global": "international",
    "overseas": "international",
    "cn": "china",
    "domestic": "china",
    "mainland": "china",
}

_DEFAULT_CONFIG = {
    "CODEBUDDY_HOST": "127.0.0.1",
    "CODEBUDDY_PORT": 8001,
    "CODEBUDDY_PASSWORD": None,
    "CODEBUDDY_SITE": "china",
    "CODEBUDDY_CREDS_DIR": ".codebuddy_creds",
    "WORKBUDDY_CREDS_DIR": ".workbuddy_creds",
    "CODEBUDDY_LOG_LEVEL": "INFO",
    "CODEBUDDY_ROTATION_COUNT": 1,
    "CODEBUDDY_AUTO_CHECKIN": "true",
    "CODEBUDDY_CHECKIN_TIME": "11:00",
    "CODEBUDDY_BARK_URL": "",
}

_ENV_ONLY_CONFIG_KEYS = {
    "CODEBUDDY_HOST",
    "CODEBUDDY_PORT",
    "CODEBUDDY_CREDS_DIR",
    "WORKBUDDY_CREDS_DIR",
    "CODEBUDDY_LOG_LEVEL",
}
_PERSISTED_CONFIG_KEYS = set(_DEFAULT_CONFIG) - _ENV_ONLY_CONFIG_KEYS

# --- Core Functions ---

def _load_dotenv_fallback(path: str = ".env") -> None:
    """Load simple KEY=VALUE pairs when python-dotenv is unavailable."""
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key or key in os.environ:
                    continue

                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in ("'", '"')
                ):
                    value = value[1:-1]

                os.environ[key] = value
        logger.info("Loaded environment variables from .env file using fallback parser.")
    except Exception as e:
        logger.error(f"Error loading .env file with fallback parser: {e}")

def load_config():
    """
    Loads configuration from all sources into the in-memory cache.
    This should be called once at application startup.
    """
    global _config_cache, _persisted_config
    
    config = _DEFAULT_CONFIG.copy()
    _persisted_config = {}
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
        logger.info("Loaded environment variables from .env file.")
    except ImportError:
        logger.warning("python-dotenv not installed, using fallback .env parser.")
        _load_dotenv_fallback()

    for key in config:
        env_value = os.getenv(key)
        if env_value is not None:
            config[key] = env_value
            
    if os.path.exists(_CONFIG_JSON_PATH):
        try:
            with open(_CONFIG_JSON_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
                if content:
                    persisted_config = json.loads(content)
                    _persisted_config = {
                        key: value
                        for key, value in persisted_config.items()
                        if key in _PERSISTED_CONFIG_KEYS
                    }
                    config.update(_persisted_config)
                    logger.info(f"Loaded and merged persisted settings from {_CONFIG_JSON_PATH}.")
        except Exception as e:
            logger.error(f"Error loading {_CONFIG_JSON_PATH}: {e}")

    _config_cache = config
    logger.info("Configuration loaded successfully.")


def _get_config_value(key: str) -> Any:
    return _config_cache.get(key, _DEFAULT_CONFIG.get(key))

def _update_config_value(key: str, value: Any):
    global _config_cache
    _config_cache[key] = value
    # Downgrade to debug to avoid verbose logging in production
    logger.debug(f"Hot-reloaded setting '{key}' to new value.")


def save_config_to_json():
    """Persist only settings explicitly overridden through the management UI."""
    try:
        # Ensure the directory exists before writing the file
        config_dir = os.path.dirname(_CONFIG_JSON_PATH)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
            logger.info(f"Created config directory at {config_dir}")

        with open(_CONFIG_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(_persisted_config, f, indent=4)
        logger.info(f"Settings successfully persisted to {_CONFIG_JSON_PATH}.")
    except Exception as e:
        logger.error(f"Failed to save config to {_CONFIG_JSON_PATH}: {e}")
        raise

# --- Public Getter Functions ---

def get_active_config() -> Dict[str, Any]:
    config = {key: _config_cache.get(key) for key in _DEFAULT_CONFIG}
    config["CODEBUDDY_SITE"] = get_codebuddy_site()
    return config

def get_server_host() -> str:
    return str(_get_config_value("CODEBUDDY_HOST"))

def get_server_port() -> int:
    return int(_get_config_value("CODEBUDDY_PORT"))

def get_server_password() -> Optional[str]:
    return _get_config_value("CODEBUDDY_PASSWORD")

def get_codebuddy_site() -> str:
    site = str(_get_config_value("CODEBUDDY_SITE") or "china").strip().lower()
    return _CODEBUDDY_SITE_ALIASES.get(site, site)

def get_codebuddy_api_endpoint() -> str:
    site = get_codebuddy_site()
    return _CODEBUDDY_SITE_ENDPOINTS.get(site, _CODEBUDDY_SITE_ENDPOINTS["china"])

def get_codebuddy_api_host() -> str:
    parsed = urlparse(get_codebuddy_api_endpoint())
    return parsed.netloc or parsed.path

def get_codebuddy_creds_dir() -> str:
    return str(_get_config_value("CODEBUDDY_CREDS_DIR"))

def get_workbuddy_creds_dir() -> str:
    return str(_get_config_value("WORKBUDDY_CREDS_DIR") or ".workbuddy_creds")

def get_log_level() -> str:
    return str(_get_config_value("CODEBUDDY_LOG_LEVEL")).upper()

def get_rotation_count() -> int:
    return int(_get_config_value("CODEBUDDY_ROTATION_COUNT"))

def get_auto_checkin_enabled() -> bool:
    value = str(_get_config_value("CODEBUDDY_AUTO_CHECKIN")).strip().lower()
    return value in ("true", "1", "yes", "y", "on")

def get_checkin_time() -> str:
    """Return daily check-in time as HH:MM in China timezone."""
    raw = str(_get_config_value("CODEBUDDY_CHECKIN_TIME") or "11:00").strip()
    try:
        parts = raw.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("out of range")
        return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError, IndexError):
        return "11:00"

def get_checkin_hour_minute() -> Tuple[int, int]:
    hour_text, minute_text = get_checkin_time().split(":", 1)
    return int(hour_text), int(minute_text)

def get_bark_url() -> str:
    return str(_get_config_value("CODEBUDDY_BARK_URL") or "").strip()

# --- Public Setter for Hot-Reload ---

def update_settings(new_settings: Dict[str, Any]):
    """Updates the live config and persists it to config.json."""
    global _persisted_config

    changed = False
    for key, value in new_settings.items():
        if key in _PERSISTED_CONFIG_KEYS:
            original_type = type(_DEFAULT_CONFIG.get(key, value))
            try:
                if original_type is bool:
                    typed_value = str(value).lower() in ('true', '1', 't', 'y', 'yes')
                else:
                    typed_value = original_type(value)
            except (ValueError, TypeError):
                logger.warning(f"Could not cast new value for '{key}' to {original_type}. Using as string.")
                typed_value = value

            if typed_value != _config_cache.get(key):
                _update_config_value(key, typed_value)
                _persisted_config[key] = typed_value
                changed = True

    if changed:
        save_config_to_json()

# --- Initial Load ---
load_config()
