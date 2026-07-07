"""
Tests for the CodeAnalyzer rule engine.
Run with: pytest tests/ -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer import analyze_source  # noqa: E402


def test_clean_code_has_low_risk():
    code = """
def add(a, b):
    return a + b
"""
    report = analyze_source(code)
    assert report["risk_level"] == "Low"
    assert report["num_findings"] == 0


def test_unused_import_detected():
    code = """
import os
import sys

def foo():
    return sys.path
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "UNUSED_IMPORT" in rule_ids


def test_bare_except_detected():
    code = """
def risky():
    try:
        x = 1 / 0
    except:
        pass
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "BARE_EXCEPT" in rule_ids


def test_hardcoded_secret_detected():
    code = """
API_KEY = "sk_live_51H8xJ2eZvKYlo2C0aBcD"
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HARDCODED_SECRET" in rule_ids
    assert report["risk_level"] == "High"


def test_missing_none_check_detected():
    code = """
def get_user(user=None):
    return user.name
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "MISSING_NONE_CHECK" in rule_ids


def test_guarded_none_not_flagged():
    code = """
def get_user(user=None):
    if user is None:
        return None
    return user.name
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "MISSING_NONE_CHECK" not in rule_ids


def test_high_cyclomatic_complexity_detected():
    code = """
def compute(a, b, c, d, e):
    total = 0
    for i in range(a):
        if i > b:
            if i < c:
                while i < d:
                    if i == e:
                        if i % 2 == 0:
                            if i > 1:
                                total += 1
                    i += 1
    return total
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HIGH_COMPLEXITY" in rule_ids


def test_hallucinated_import_detected():
    code = """
import stringutils

def foo():
    return stringutils.clean("x")
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HALLUCINATED_IMPORT" in rule_ids


def test_hallucinated_attribute_detected():
    code = """
import math

def foo():
    return math.squareroot(16)
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HALLUCINATED_ATTRIBUTE" in rule_ids


def test_argument_mismatch_detected():
    code = """
import json

def foo():
    return json.loads('{"a":1}', True, False)
"""
    report = analyze_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "ARGUMENT_MISMATCH" in rule_ids


def test_syntax_error_handled_gracefully():
    code = "def foo(:\n    pass"
    report = analyze_source(code)
    assert report["risk_level"] == "High"
    assert report["findings"][0]["rule_id"] == "PARSE_ERROR"


def test_risk_scoring_thresholds():
    # Multiple medium findings should push risk to Medium or High
    code = """
def risky():
    try:
        x = 1
    except:
        pass
    try:
        y = 2
    except:
        pass
"""
    report = analyze_source(code)
    assert report["total_weight"] >= 5
    assert report["risk_level"] in ("Medium", "High")
