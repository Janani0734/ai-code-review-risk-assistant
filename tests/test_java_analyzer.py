"""
Tests for the JavaAnalyzer rule engine.
Run with: pytest tests/ -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from java_analyzer import analyze_java_source  # noqa: E402


def test_clean_java_code_has_low_risk():
    code = """
public class Add {
    public int add(int a, int b) {
        return a + b;
    }
}
"""
    report = analyze_java_source(code)
    assert report["language"] == "java"
    assert report["risk_level"] == "Low"
    assert report["num_findings"] == 0


def test_java_unused_import_detected():
    code = """
import java.util.List;
import java.io.IOException;

public class Foo {
    public IOException makeError() {
        return new IOException("bad");
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "UNUSED_IMPORT" in rule_ids


def test_java_swallowed_exception_detected():
    code = """
public class Risky {
    public void risky() {
        try {
            int x = 1 / 0;
        } catch (Exception e) {
        }
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "SWALLOWED_EXCEPTION" in rule_ids


def test_java_hardcoded_secret_detected():
    code = """
public class Config {
    private String apiKey = "sk_live_51H8xJ2eZvKYlo2C0aBcD";
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HARDCODED_SECRET" in rule_ids
    assert report["risk_level"] == "High"


def test_java_missing_null_check_detected():
    code = """
public class Foo {
    public String getUser(String user) {
        return user.toUpperCase();
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "MISSING_NULL_CHECK" in rule_ids


def test_java_guarded_null_not_flagged():
    code = """
public class Foo {
    public String getUser(String user) {
        if (user != null) {
            return user.toUpperCase();
        }
        return null;
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "MISSING_NULL_CHECK" not in rule_ids


def test_java_high_complexity_detected():
    code = """
public class Compute {
    public int compute(int a, int b, int c, int d, int e) {
        int total = 0;
        for (int i = 0; i < a; i++) {
            if (i > b) {
                if (i < c) {
                    while (i < d) {
                        if (i == e) {
                            if (i % 2 == 0) {
                                total += 1;
                            }
                        }
                        i++;
                    }
                }
            }
        }
        return total;
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HIGH_COMPLEXITY" in rule_ids


def test_java_hallucinated_import_detected():
    code = """
import fakepkg.MadeUp;

public class Foo {
    public void useIt() {
        MadeUp thing = new MadeUp();
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HALLUCINATED_IMPORT" in rule_ids


def test_java_hallucinated_method_detected():
    code = """
public class Foo {
    public void useIt() {
        double r = Math.squareroot(16);
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "HALLUCINATED_METHOD" in rule_ids


def test_java_argument_mismatch_detected():
    code = """
public class Foo {
    public void useIt() {
        double r = Math.sqrt(4, 5);
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "ARGUMENT_MISMATCH" in rule_ids


def test_java_syntax_error_handled_gracefully():
    code = "public class Foo { public void bad( { } }"
    report = analyze_java_source(code)
    assert report["risk_level"] == "High"
    assert report["findings"][0]["rule_id"] == "PARSE_ERROR"


def test_java_broad_catch_detected():
    code = """
public class Foo {
    public void risky() {
        try {
            int x = 1 / 0;
        } catch (Exception e) {
            System.out.println("error");
        }
    }
}
"""
    report = analyze_java_source(code)
    rule_ids = [f["rule_id"] for f in report["findings"]]
    assert "BROAD_CATCH" in rule_ids
