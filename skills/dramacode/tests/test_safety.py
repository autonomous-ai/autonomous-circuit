"""Tests for the pre-publish safety gate + compliance scan."""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from dramalib.safety import (
    LIKENESS_VERDICTS,
    SAFETY_CHECKPOINTS,
    compliance_scan,
    likeness_gate,
)


def test_no_screener_returns_not_screened_never_pass():
    r = likeness_gate(images=["a.png"])
    assert r["verdict"] == "not_screened"        # absence of screening != safe
    assert r["verdict"] != "pass"
    assert "not_screened" in LIKENESS_VERDICTS


def test_screener_escalates_above_threshold_else_passes():
    hi = likeness_gate(images=["a.png"],
                       screener=lambda imgs: [{"name": "X", "similarity": 0.9, "source": "u"}])
    assert hi["verdict"] == "escalate" and hi["top_similarity"] == 0.9
    lo = likeness_gate(images=["a.png"],
                       screener=lambda imgs: [{"name": "X", "similarity": 0.1, "source": "u"}])
    assert lo["verdict"] == "pass"


def test_three_checkpoints_exist():
    assert SAFETY_CHECKPOINTS == ("character_sheet_lock", "first_frame", "pre_ad_publish")


def test_compliance_scan_flags_red_lines_and_banned_terms():
    flags = compliance_scan(text="a nonconsensual scene with an underage character")
    cats = {f["category"] for f in flags}
    assert "non_consent" in cats and "minor_safety" in cats
    assert all(f["severity"] == "blocker" for f in flags)
    # caller banned term
    b = compliance_scan(text="mentions BrandX", banned_terms=["brandx"])
    assert b and b[0]["category"] == "banned_term"


def test_clean_text_scans_clean():
    assert compliance_scan(text="she slaps him and walks out, chin high") == []
