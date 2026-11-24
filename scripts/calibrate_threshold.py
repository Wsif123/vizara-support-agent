"""Threshold calibration report for a client's retrieval score_threshold.

Runs every labeled question in eval/eval_set.yaml through the retriever with
the score floor removed, then reports how well one similarity threshold
separates answerable questions from should-escalate ones.

Read-only: this script never writes a client config.

Usage: py -3.11 scripts/calibrate_threshold.py [client_id]
"""

import logging
import sys
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Small demo KBs have fewer chunks than top_k, and chromadb warns about it on
# every query; that warning is expected here and would drown the report.
logging.getLogger("chromadb").setLevel(logging.ERROR)

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import registry
from src.retriever import query as retrieve

EVAL_PATH = Path(__file__).parent.parent / "eval" / "eval_set.yaml"
LABEL = {True: "escalate", False: "answerable"}


def load_cases() -> list[dict]:
    with open(EVAL_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def top_score(client_id: str, question: str) -> float:
    docs = retrieve(client_id, question, top_k=8, score_threshold=0.0)
    return max((float(d["score"]) for d in docs), default=0.0)


def main() -> int:
    client_id = sys.argv[1] if len(sys.argv) > 1 else "vizara"

    item = registry.get_client(client_id)
    if item is None:
        print(f"Unknown client_id '{client_id}'.", file=sys.stderr)
        return 1
    config = item.get("config") or {}
    current = float(config.get("retrieval", {}).get("score_threshold", 0.5))

    rows = [
        (top_score(client_id, case["question"]), bool(case.get("must_escalate")), case["question"])
        for case in load_cases()
    ]
    rows.sort(key=lambda row: row[0], reverse=True)

    n_escalate = sum(1 for _, must_escalate, _ in rows if must_escalate)
    print(f"Threshold calibration — client: {client_id}")
    print(
        f"Eval set: {EVAL_PATH} "
        f"({len(rows)} cases: {len(rows) - n_escalate} answerable, {n_escalate} should-escalate)"
    )
    print(f"Current score_threshold: {current:.2f}\n")

    print(f"{'Score':<6} {'Label':<11} Question")
    print(f"{'-' * 5:<6} {'-' * 11:<11} {'-' * 8}")
    for score, must_escalate, question in rows:
        print(f"{score:<6.3f} {LABEL[must_escalate]:<11} {question}")

    answerable = [score for score, must_escalate, _ in rows if not must_escalate]
    escalate = [score for score, must_escalate, _ in rows if must_escalate]

    print()
    print(
        f"Highest answerable score: {max(answerable):.3f}"
        if answerable
        else "Highest answerable score: n/a (no answerable cases)"
    )
    print(
        f"Lowest should-escalate score: {min(escalate):.3f}"
        if escalate
        else "Lowest should-escalate score: n/a (no must_escalate: true cases)"
    )

    # What the retriever actually does: a chunk "passes" when score >=
    # score_threshold, and score is similarity (higher = closer). A safe
    # threshold therefore sits above every should-escalate score and at or
    # below every answerable score.
    misclassified = []
    for score, must_escalate, question in rows:
        would_escalate = score < current
        if would_escalate != must_escalate:
            misclassified.append((score, must_escalate, question, would_escalate))

    if not answerable or not escalate:
        print("\nCannot calibrate a separation point: the eval set needs both classes.")
        if not escalate:
            print(
                "Add questions the bot must refuse as `must_escalate: true` in "
                f"{EVAL_PATH.name} — until then no threshold is validated."
            )
        else:
            print(
                "Add answerable questions to "
                f"{EVAL_PATH.name} — until then no threshold is validated."
            )
        print(f"At the current threshold ({current:.2f}): {len(misclassified)} of {len(rows)} case(s) misclassified.")
        for score, must_escalate, question, _ in misclassified:
            print(f"  {score:.3f}  {LABEL[must_escalate]:<11} {question}")
        return 0

    lowest_answerable = min(answerable)
    highest_escalate = max(escalate)

    if highest_escalate < lowest_answerable:
        midpoint = (highest_escalate + lowest_answerable) / 2
        print(
            "\nSafe separation: every should-escalate case scores below every "
            "answerable case."
        )
        print(
            f"Suggested score_threshold: {midpoint:.3f} — safe "
            f"(above {highest_escalate:.3f}, at or below {lowest_answerable:.3f})."
        )
        return 0

    print("\nClasses overlap: some should-escalate cases score at least as high as answerable ones.")
    print(f"At the current threshold ({current:.2f}): {len(misclassified)} of {len(rows)} case(s) misclassified.")
    for score, must_escalate, question, would_escalate in misclassified:
        outcome = "would escalate" if would_escalate else "would be answered"
        print(f"  {score:.3f}  {LABEL[must_escalate]:<11} {question}  ({outcome})")
    print(
        "Suggestion: move the misclassified questions to the other class in "
        f"{EVAL_PATH.name} — either they belong there, or the knowledge base "
        "needs content that separates them."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
