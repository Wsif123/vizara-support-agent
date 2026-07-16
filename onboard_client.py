import sys
import shutil
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"
SERVER_PY = REPO_ROOT / "src" / "server.py"


def _parse_args():
    args = {}
    for arg in sys.argv[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            args[k] = v
    return args


def _add_to_server_py(client_id: str, domain: str):
    content = SERVER_PY.read_text(encoding="utf-8")

    # Add to VALID_CLIENT_IDS
    if client_id not in content:
        content = content.replace(
            'VALID_CLIENT_IDS = {"vizara", "test"}',
            f'VALID_CLIENT_IDS = {{"vizara", "test", "{client_id}"}}',
        )

    # Add to CLIENT_DOMAINS
    domain_line = f'    "{client_id}": "{domain}",'
    if domain_line not in content:
        content = content.replace(
            '    "test": None,',
            f'    "test": None,\n{domain_line}',
        )

    SERVER_PY.write_text(content, encoding="utf-8")
    print(f"  [OK]   Added '{client_id}' to server.py (VALID_CLIENT_IDS + CLIENT_DOMAINS)")


def _create_client_config(client_id: str, display_name: str, content_folder: Path, persona: str = None):
    config_path = CONFIG_DIR / f"client_{client_id}.yaml"
    if config_path.exists():
        print(f"  [SKIP] Config already exists at {config_path}")
        return

    # List files in content folder
    files = sorted(f for f in content_folder.iterdir() if f.suffix in {".md", ".txt"})
    source_files = [f.name for f in files]

    if not persona:
        persona = (
            f"You are a friendly and helpful support agent for {display_name}. "
            "Answer questions only based on the retrieved context. "
            "If the information is not in the context, do not guess — escalate to a human."
        )

    config = {
        "client": {
            "name": client_id,
            "display_name": display_name,
            "source_files": source_files,
            "persona": persona,
            "escalation": {
                "email": f"support@{client_id}.com",
                "message": f"Sorry, I can't answer that with confidence. Let me get a human from the {display_name} team to help you out."
            },
            "retrieval": {
                "top_k": 6,
                "score_threshold": 0.5,
            },
        }
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"  [OK]   Created config/client_{client_id}.yaml")


def _copy_content(client_id: str, content_folder: Path):
    raw_dir = DATA_DIR / client_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in content_folder.iterdir() if f.suffix in {".md", ".txt"})
    for f in files:
        shutil.copy2(f, raw_dir / f.name)
        print(f"  [OK]   Copied {f.name}")
    if not files:
        print("  [WARN] No .md or .txt files found in content folder")


def _generate_widget_snippet(client_id: str, backend_url: str, domain: str):
    snippet = f'''<!-- Vizara Support Widget for {client_id} -->
<script
  src="{backend_url.rstrip('/')}/static/widget/chat-widget.js"
  data-api-url="{backend_url.rstrip('/')}/chat"
  data-client-id="{client_id}"
></script>'''
    return snippet


def main():
    global args
    args = _parse_args()
    client_id = args.get("client_id") or args.get("id")
    domain = args.get("domain") or args.get("allowed_domain")
    content_folder = args.get("content") or args.get("folder")
    display_name = args.get("name") or client_id.title()
    persona = args.get("persona")
    backend_url = args.get("backend") or "http://localhost:8000"

    if not client_id or not domain or not content_folder:
        print("Usage: python onboard_client.py client_id=<id> domain=<domain> content=<folder> [name=<Display Name>] [persona=<Custom persona>] [backend=<URL>]")
        sys.exit(1)

    content_path = Path(content_folder)
    if not content_path.is_dir():
        print(f"Error: content folder '{content_folder}' not found")
        sys.exit(1)

    print(f"Onboarding client '{client_id}'...")
    print(f"  Display name: {display_name}")
    print(f"  Domain: {domain}")
    print(f"  Content: {content_path}")
    if persona:
        print(f"  Persona: {persona}")
    print()

    _add_to_server_py(client_id, domain)
    _create_client_config(client_id, display_name, content_path, persona)
    _copy_content(client_id, content_path)

    print()
    print("  Ingesting content into Chroma...")
    from src.ingest import ingest
    ingest(client_id)

    print()
    print("  Client onboarded successfully!")
    print()
    print("  Note: The server must be restarted to pick up the new client.")
    print()
    print("  Widget embed snippet:")
    print()
    print(_generate_widget_snippet(client_id, backend_url, domain))
    print()


if __name__ == "__main__":
    main()