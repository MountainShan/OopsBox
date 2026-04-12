import os
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_FILE = Path("/oopsbox/config.yaml")


@dataclass
class Config:
    username: str = "admin"
    password: str = ""
    api_key: Optional[str] = None
    base_url: str = "https://api.anthropic.com"
    git_name: Optional[str] = None
    git_email: Optional[str] = None
    ssl_cert: Optional[str] = None
    ssl_key: Optional[str] = None


def load_config(config_file: Path = DEFAULT_CONFIG_FILE) -> Config:
    cfg = Config()

    if config_file.exists():
        with open(config_file) as f:
            data = yaml.safe_load(f) or {}

        auth = data.get("auth", {})
        cfg.username = auth.get("username", cfg.username)
        cfg.password = auth.get("password", cfg.password)

        agent = data.get("agent", {})
        cfg.api_key = agent.get("api_key", cfg.api_key)
        cfg.base_url = agent.get("base_url", cfg.base_url)

        git = data.get("git", {})
        cfg.git_name = git.get("name", cfg.git_name)
        cfg.git_email = git.get("email", cfg.git_email)

        ssl = data.get("ssl", {})
        cfg.ssl_cert = ssl.get("cert", cfg.ssl_cert)
        cfg.ssl_key = ssl.get("key", cfg.ssl_key)

    if os.getenv("OOPSBOX_USERNAME"):
        cfg.username = os.environ["OOPSBOX_USERNAME"]
    if os.getenv("OOPSBOX_PASSWORD"):
        cfg.password = os.environ["OOPSBOX_PASSWORD"]
    if os.getenv("ANTHROPIC_API_KEY"):
        cfg.api_key = os.environ["ANTHROPIC_API_KEY"]
    if os.getenv("ANTHROPIC_BASE_URL"):
        cfg.base_url = os.environ["ANTHROPIC_BASE_URL"]
    if os.getenv("GIT_NAME"):
        cfg.git_name = os.environ["GIT_NAME"]
    if os.getenv("GIT_EMAIL"):
        cfg.git_email = os.environ["GIT_EMAIL"]

    return cfg


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    global _config
    _config = None
