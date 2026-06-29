import yaml
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "client.yaml"


def load_client_config(path: Path = _CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw["client"]
