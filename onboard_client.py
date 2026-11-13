"""CLI onboarding helper (the dashboard is the primary flow).

Creates a config file + raw content folder and runs ingestion. It no longer
edits src/server.py: server.py reads the config directory at runtime.
"""

import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"


def _parse_args():
    args = {}
    for arg in sys.argv[1:]:
        if "=" in arg:
            key, value = arg.split("=", 1)
            args[key] = value
    return args


def _generate_widget_snippet(client_id, backend_url, domain, display_name, contact_name):
    return f'''<!-- Vizara Support Widget for {client_id} (allowed domain: {domain}) -->
<script
  src="{backend_url.rstrip('/')}/widget/chat-widget.js"
  data-api-url="{backend_url.rstrip('/')}/chat"
  data-client-id="{client_id}"
  data-brand-name="{display_name}"
  data-followup-name="{contact_name}"
></script>'''


def main():
    args = _parse_args()
    client_id = args.get("client_id") or args.get("id")
    domain = args.get("domain") or args.get("allowed_domain")
    content_folder = args.get("content") or args.get("folder")
    display_name = args.get("name") or (client_id.title() if client_id else "")
    persona = args.get("persona")
    contact_name = args.get("contact_name") or display_name
    contact_email = args.get("contact_email") or f"support@{domain}" if domain else ""
    backend_url = args.get("backend") or "http://localhost:8000"

    if not client_id or not domain or not content_folder:
        print(
            "Usage: python onboard_client.py client_id=<id> domain=<domain> "
            "content=<folder> [name=<Display Name>] [persona=<Custom persona>] "
            "[contact_name=<Human name>] [contact_email=<email>] [backend=<URL>]"
        )
        print("Tip: you can also do all of this from the local dashboard (start-dashboard.bat).")
        sys.exit(1)

    content_path = Path(content_folder)
    if not content_path.is_dir():
        print(f"Error: content folder '{content_folder}' not found")
        sys.exit(1)

    config_path = CONFIG_DIR / f"client_{client_id}.yaml"
    if config_path.exists():
        print(f"Error: {config_path} already exists. Use the dashboard to edit it.")
        sys.exit(1)

    files = sorted(
        path for path in content_path.iterdir() if path.suffix.lower() in {".md", ".txt"}
    )
    if not files:
        print("Error: no .md or .txt files found in the content folder.")
        sys.exit(1)

    raw_dir = DATA_DIR / client_id / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copy2(path, raw_dir / path.name)
        print(f"  [OK]   Copied {path.name}")

    if not persona:
        persona = (
            f"You are a friendly and helpful support agent for {display_name}. "
            "Answer questions only based on the retrieved context. If the information "
            "is not in the context, do not guess — escalate to a human."
        )

    config = {
        "client": {
            "name": client_id,
            "display_name": display_name,
            "domain": domain,
            "enabled": True,
            "source_files": [path.name for path in files],
            "persona": persona,
            "escalation": {
                "email": contact_email,
                "contact_name": contact_name,
                "message": (
                    f"Sorry, I can't answer that with confidence. Let me get a human "
                    f"from the {display_name} team to help you out."
                ),
            },
            "retrieval": {"top_k": 6, "score_threshold": 0.5},
        }
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True)
    print(f"  [OK]   Created {config_path}")

    print("\n  Ingesting content into Chroma...")
    from src.ingest import ingest

    ingest(client_id)

    print("\n  Client onboarded successfully.")
    print("  The public server picks up config changes within 2 seconds (or on restart).")
    print("\n  Widget embed snippet:\n")
    print(
        _generate_widget_snippet(
            client_id, backend_url, domain, display_name, contact_name
        )
    )


if __name__ == "__main__":
    main()
