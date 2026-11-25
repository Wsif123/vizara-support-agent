import os
import sys
import uuid
from pathlib import Path

import yaml

# Windows consoles default to cp1252, which cannot print LLM output containing
# characters such as non-breaking hyphens or the check/cross marks below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import answer

EVAL_PATH = Path(__file__).parent.parent / "eval" / "eval_set.yaml"
CLIENT_ID = os.getenv("EVAL_CLIENT_ID", "vizara")


def load_eval_set(path: Path = EVAL_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


VALID_EXPECT = {"answerable", "off_topic", "needs_human"}


def grade(item: dict, response: str, escalated: bool) -> tuple[bool, str]:
    """Decide pass/fail for one eval case, returning (passed, reason).

    `expect` is the three-way contract; `must_escalate` stays as a fallback for
    older case files that don't carry one yet.
    """
    expect = item.get("expect")
    if expect is not None:
        if expect not in VALID_EXPECT:
            raise ValueError(f"unknown expect value: {expect!r}")
        if expect == "answerable":
            if escalated:
                return False, "escalated an answerable question"
            return True, "answered without escalation"
        if expect == "off_topic":
            if escalated:
                return False, "escalated instead of declining"
            if "email" in response.lower():
                return False, "declined but asked for an email"
            return True, "declined without escalating or asking for an email"
        # needs_human
        if escalated:
            return True, "correctly escalated"
        return False, "should have escalated but didn't"

    # Legacy fallback for cases without `expect`.
    must_escalate = item.get("must_escalate", False)
    expected_kws = item.get("expected_keywords", [])
    if must_escalate:
        if escalated:
            return True, "correctly escalated (must_escalate fallback)"
        return False, "should have escalated but didn't"
    if escalated:
        return False, "escalated when it shouldn't have"
    if expected_kws and not check_keywords(response, expected_kws):
        return False, f"none of expected keywords found: {expected_kws}"
    return True, "answered without escalation (must_escalate fallback)"



def run():
    eval_set = load_eval_set()

    passed = 0
    failed = 0
    results = []

    print("=" * 60)
    print(f"Vizara Support Agent — Evaluation Run (client: {CLIENT_ID})")
    print("=" * 60)

    for i, item in enumerate(eval_set, 1):
        q = item["question"]
        expect = item.get("expect")
        # Fresh session per case: sharing one session let earlier answers leak
        # into later prompts via the 10-message history.
        session_id = f"eval-{uuid.uuid4().hex[:8]}"

        print(f"\n[{i}/{len(eval_set)}] Q: {q}")
        print("-" * 40)

        try:
            result = answer(session_id, q, CLIENT_ID)
            response = result["answer"]
            escalated = result["escalated"]

            print(f"   A: {response[:200]}...")
            print(f"   Escalated: {escalated}")
            if expect:
                print(f"   Expect: {expect}")
            else:
                print(f"   Expect: (fallback) must_escalate={item.get('must_escalate', False)}")

            ok, reason = grade(item, response, escalated)
            if ok:
                print(f"   ✓ PASS — {reason}")
                passed += 1
                status = "PASS"
            else:
                print(f"   ✗ FAIL — {reason}")
                failed += 1
                status = "FAIL"

            results.append({"question": q, "expect": expect, "status": status, "escalated": escalated})

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
