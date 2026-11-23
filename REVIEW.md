## Decisions already locked
- Stay on Render Free until a client pays.
- Keep the console local; no public exposure, no server move yet.
- Keep Chroma; do not upgrade.
- Postgres (Neon) is the transcript store.
- One worker only.

## Item 4.1. Promote script
Create scripts/promote_client.py:
- Take client_id as argv[1]; exit 1 with a message if missing or if
  config/client_<id>.yaml does not exist.
- Read that YAML to get the client "name" for the data path.
- Refuse to continue (exit 1) if `git status --porcelain` lists any changed path
  outside config/ and data/. Print the offending paths.
- git add the config file and data/<name>/.
- If nothing is staged, print "Nothing to promote for <id>" and exit 0.
- git commit -m "client: <id>".
- git push.
- Print the Render service URL from RENDER_DEMO_URL when set, otherwise
  https://vizara-support-agent.onrender.com, plus "Deploy triggered — verify
  /health, then send one test message."

stdlib only, Python 3.11, subprocess for git.
