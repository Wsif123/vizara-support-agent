import yaml
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def load_client_config(client_id: str = None) -> dict:
    if client_id:
        path = _CONFIG_DIR / f"client_{client_id}.yaml"
    else:
        path = _CONFIG_DIR / "client.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw["client"]
