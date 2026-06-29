import sys
import uuid
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import answer

EVAL_PATH = Path(__file__).parent.parent / "eval" / "eval_set.yaml"


def load_eval_set(path: Path = EVAL_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def run():
    eval_set = load_eval_set()
    session_id = f"eval-{uuid.uuid4().hex[:8]}"

    passed = 0
    failed = 0
    results = []

    print("=" * 60)
    print("Vizara Support Agent — Evaluation Run")
    print("=" * 60)

    for i, item in enumerate(eval_set, 1):
        q = item["question"]
        expected_kws = item.get("expected_keywords", [])
        must_escalate = item.get("must_escalate", False)

        print(f"\n[{i}/{len(eval_set)}] Q: {q}")
        print("-" * 40)

        try:
            result = answer(session_id, q)
            response = result["answer"]
            escalated = result["escalated"]

            print(f"   A: {response[:200]}...")
            print(f"   Escalated: {escalated}")

            if must_escalate:
                if escalated:
                    print(f"   ✓ CORRECT — correctly escalated")
                    passed += 1
                    status = "PASS"
                else:
                    print(f"   ✗ FAIL — should have escalated but didn't")
                    failed += 1
                    status = "FAIL"
            else:
                if escalated:
                    print(f"   ✗ FAIL — escalated when it shouldn't have")
                    failed += 1
                    status = "FAIL"
                elif expected_kws and not check_keywords(response, expected_kws):
                    print(f"   ✗ FAIL — none of expected keywords found: {expected_kws}")
                    failed += 1
                    status = "FAIL"
                else:
                    print(f"   ✓ PASS")
                    passed += 1
                    status = "PASS"

            results.append({"question": q, "status": status, "escalated": escalated})

        except Exception as e:
            print(f"   ✗ ERROR — {e}")
            failed += 1
            results.append({"question": q, "status": "ERROR", "error": str(e)})

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(eval_set)}")
    print("=" * 60)

    return passed, failed


if __name__ == "__main__":
    run()
