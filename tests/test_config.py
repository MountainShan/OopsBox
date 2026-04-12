import os, sys, tempfile, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_defaults():
    os.environ.pop("OOPSBOX_USERNAME", None)
    os.environ.pop("OOPSBOX_PASSWORD", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    import importlib
    import dashboard.config as cfg
    importlib.reload(cfg)
    c = cfg.load_config(config_file=Path("/nonexistent.yaml"))
    assert c.username == "admin"
    assert c.api_key is None
    assert c.ssl_cert is None

def test_env_override():
    os.environ["OOPSBOX_USERNAME"] = "testuser"
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    import importlib
    import dashboard.config as cfg
    importlib.reload(cfg)
    c = cfg.load_config(config_file=Path("/nonexistent.yaml"))
    assert c.username == "testuser"
    assert c.api_key == "sk-test"
    os.environ.pop("OOPSBOX_USERNAME")
    os.environ.pop("ANTHROPIC_API_KEY")

def test_yaml_override(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({
        "auth": {"username": "yamluser", "password": "yamlpass"},
        "agent": {"api_key": "sk-yaml", "base_url": "http://proxy:4000"},
        "ssl": {"cert": "/certs/cert.pem", "key": "/certs/key.pem"}
    }))
    import importlib
    import dashboard.config as cfg
    importlib.reload(cfg)
    c = cfg.load_config(config_file=config_file)
    assert c.username == "yamluser"
    assert c.password == "yamlpass"
    assert c.api_key == "sk-yaml"
    assert c.base_url == "http://proxy:4000"
    assert c.ssl_cert == "/certs/cert.pem"

def test_yaml_env_priority(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({"auth": {"password": "yaml-pass"}}))
    os.environ["OOPSBOX_PASSWORD"] = "env-pass"
    import importlib
    import dashboard.config as cfg
    importlib.reload(cfg)
    c = cfg.load_config(config_file=config_file)
    assert c.password == "env-pass"
    os.environ.pop("OOPSBOX_PASSWORD")
