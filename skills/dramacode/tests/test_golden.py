"""The binge golden set — flywheel #5 regression guard (unit-level)."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.golden import check_golden, golden_rewards, golden_set


def test_golden_set_has_strong_and_weak():
    kinds = {k for _n, _e, k in golden_set()}
    assert kinds == {"strong", "weak"}


def test_golden_check_passes_on_the_current_eval():
    problems = check_golden()
    assert problems == [], "\n".join(problems)


def test_strong_out_rewards_weak():
    r = golden_rewards()
    strong = [r[n] for n, _e, k in golden_set() if k == "strong"]
    weak = [r[n] for n, _e, k in golden_set() if k == "weak"]
    assert min(strong) > max(weak)
