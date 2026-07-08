"""
java_analyzer.py
Java static analysis engine for the AI Code Review Risk Assistant.

Mirrors analyzer.py's rule categories and Finding/report shape so the
Flask API and frontend can treat Python and Java reports identically.
Uses the `javalang` library to parse Java source into an AST.
"""

from typing import List, Dict, Any
from dataclasses import dataclass, field

import javalang
from javalang.parser import JavaSyntaxError

from analyzer import Finding, AnalysisFacts  # reuse the shared data shapes


# ---------------------------------------------------------------------------
# Curated tables for Java hallucination-pattern checks, mirroring the
# Python analyzer's approach: a small set of common LLM mistakes rather
# than an exhaustive JDK method index.
# ---------------------------------------------------------------------------

# (qualifier, member) pairs that sound plausible but are not real JDK methods.
# Common LLM confusions: JS-isms, Python-isms, or slightly-wrong casing.
KNOWN_FAKE_JAVA_CALLS = {
    ("String", "valueof"),        # real is String.valueOf
    ("Math", "squareroot"),       # real is Math.sqrt
    ("Math", "power"),            # real is Math.pow
    ("Integer", "parse"),         # real is Integer.parseInt
    ("Arrays", "toList"),         # real is Arrays.asList
    ("System", "print"),          # real is System.out.println
    ("List", "push"),             # JS-ism; Java Lists use .add
    ("List", "pop"),              # not a real List method (that's Deque/Stack)
    ("String", "format_map"),     # Python-ism, not real in Java
    ("Objects", "isNull_safe"),   # not a real Objects method
}

# Real JDK methods with a known fixed/bounded arity, used to catch
# argument-count hallucinations similar to the Python arity table.
KNOWN_JAVA_METHOD_ARITY: Dict[str, Dict[str, Any]] = {
    "String": {
        "valueOf": {"min": 1, "max": 1},
        "format": {"min": 1, "max": None},
        "join": {"min": 2, "max": None},
    },
    "Integer": {
        "parseInt": {"min": 1, "max": 2},
        "valueOf": {"min": 1, "max": 2},
    },
    "Math": {
        "sqrt": {"min": 1, "max": 1},
        "pow": {"min": 2, "max": 2},
        "max": {"min": 2, "max": 2},
        "min": {"min": 2, "max": 2},
        "abs": {"min": 1, "max": 1},
    },
    "Arrays": {
        "asList": {"min": 0, "max": None},
        "sort": {"min": 1, "max": 3},
    },
    "Objects": {
        "isNull": {"min": 1, "max": 1},
        "nonNull": {"min": 1, "max": 1},
        "equals": {"min": 2, "max": 2},
    },
}

# Known-real top-level java.* / javax.* package prefixes, used to flag
# imports that don't look like real JDK or common third-party packages.
KNOWN_JAVA_PACKAGE_PREFIXES = (
    "java.", "javax.", "org.springframework", "org.junit", "com.google",
    "org.apache", "org.slf4j", "com.fasterxml", "org.hibernate", "io.jsonwebtoken",
    "org.mockito", "org.assertj", "com.amazonaws", "org.json", "com.squareup",
)

SECRET_KEYWORDS = ("apikey", "api_key", "secret", "token", "password", "passwd", "pwd")

# Well-known JDK core class names, used to catch near-miss typos (e.g.
# "Systems.out.println" instead of "System.out.println") that the fixed
# fake-call table above wouldn't catch since it only matches exact,
# pre-known mistakes rather than doing general name-similarity checking.
KNOWN_CORE_JAVA_CLASSES = (
    "System", "Math", "String", "Integer", "Long", "Double", "Float",
    "Boolean", "Character", "Object", "Arrays", "Objects", "List", "Map",
    "Set", "Collections", "Scanner", "StringBuilder", "ArrayList",
    "HashMap", "HashSet", "Thread", "Exception", "Runnable", "Optional",
)


def _levenshtein(a: str, b: str) -> int:
    """Simple edit-distance implementation (no external dependency)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


class JavaAnalyzer:
    """Parses Java source code and runs the rule engine."""

    MAX_CYCLOMATIC_COMPLEXITY = 6

    def __init__(self, source_code: str):
        self.source_code = source_code
        self.findings: List[Finding] = []
        self.facts = AnalysisFacts()
        self.tree = None
        self.parse_error = None

        try:
            self.tree = javalang.parse.parse(source_code)
        except JavaSyntaxError as e:
            self.parse_error = f"JavaSyntaxError: {e}"
        except Exception as e:  # javalang raises a few different error types
            self.parse_error = f"ParseError: {e}"

    def analyze(self) -> Dict[str, Any]:
        if self.parse_error:
            self.findings.append(Finding(
                rule_id="PARSE_ERROR",
                message=self.parse_error,
                severity="high",
                weight=10,
                line=0,
                category="static",
            ))
            return self._build_report()

        self.facts.num_lines = len(self.source_code.splitlines())
        self._extract_facts()

        self._check_unused_imports()
        self._check_empty_catch()
        self._check_hardcoded_secrets()
        self._check_cyclomatic_complexity()
        self._check_missing_null_checks()

        self._check_unknown_imports()
        self._check_fake_method_calls()
        self._check_call_arity()
        self._check_core_class_typos()

        return self._build_report()

    # ------------------------------------------------------------------
    def _extract_facts(self):
        for _, node in self.tree.filter(javalang.tree.Import):
            self.facts.imports.append(node.path)
        for _, node in self.tree.filter(javalang.tree.MethodDeclaration):
            self.facts.functions.append(node.name)

    # ------------------------------------------------------------------
    # Static checks
    # ------------------------------------------------------------------
    def _check_unused_imports(self):
        """Flag imports whose simple class name never appears as an
        identifier elsewhere in the source (text-based check, since
        javalang doesn't resolve symbol usage)."""
        source_lines = self.source_code.splitlines()
        for _, node in self.tree.filter(javalang.tree.Import):
            simple_name = node.path.split(".")[-1]
            if simple_name == "*":
                continue
            # count occurrences of the simple name outside the import line itself
            usages = 0
            for line in source_lines:
                if line.strip().startswith("import "):
                    continue
                if simple_name in line:
                    usages += 1
            if usages == 0:
                self.findings.append(Finding(
                    rule_id="UNUSED_IMPORT",
                    message=f"Import '{node.path}' is never used.",
                    severity="low",
                    weight=1,
                    line=getattr(node, "position", None).line if node.position else 0,
                    category="static",
                ))

    def _check_empty_catch(self):
        for _, node in self.tree.filter(javalang.tree.TryStatement):
            for catch in node.catches:
                line = catch.block[0].position.line if catch.block and catch.block[0].position else (
                    node.position.line if node.position else 0
                )
                if not catch.block:
                    self.findings.append(Finding(
                        rule_id="SWALLOWED_EXCEPTION",
                        message=(
                            f"Catch block for {', '.join(catch.parameter.types)} "
                            "is empty; the exception is silently swallowed."
                        ),
                        severity="medium",
                        weight=3,
                        line=line,
                        category="static",
                    ))
                elif catch.parameter.types == ["Exception"] or catch.parameter.types == ["Throwable"]:
                    self.findings.append(Finding(
                        rule_id="BROAD_CATCH",
                        message=(
                            f"Catch block catches overly broad type "
                            f"'{catch.parameter.types[0]}'. Prefer a specific exception type."
                        ),
                        severity="medium",
                        weight=3,
                        line=line,
                        category="static",
                    ))

    def _check_hardcoded_secrets(self):
        for i, line in enumerate(self.source_code.splitlines(), start=1):
            lowered = line.lower()
            if "=" in line and '"' in line and any(k in lowered for k in SECRET_KEYWORDS):
                # rough heuristic: a string literal of length >= 6 assigned to
                # a variable whose name suggests a secret
                if any(len(part.strip().strip('";')) >= 6 for part in line.split('"')[1::2]):
                    self.findings.append(Finding(
                        rule_id="HARDCODED_SECRET",
                        message="Line appears to contain a hardcoded secret/credential.",
                        severity="high",
                        weight=8,
                        line=i,
                        category="static",
                    ))

    def _statement_complexity(self, method_node) -> int:
        complexity = 1
        if method_node.body is None:
            return complexity
        decision_types = (
            javalang.tree.IfStatement, javalang.tree.ForStatement,
            javalang.tree.WhileStatement, javalang.tree.DoStatement,
            javalang.tree.CatchClause, javalang.tree.TernaryExpression,
            javalang.tree.SwitchStatementCase,
        )
        for _, node in method_node:
            if isinstance(node, decision_types):
                complexity += 1
            if isinstance(node, javalang.tree.BinaryOperation) and node.operator in ("&&", "||"):
                complexity += 1
        return complexity

    def _check_cyclomatic_complexity(self):
        for _, node in self.tree.filter(javalang.tree.MethodDeclaration):
            score = self._statement_complexity(node)
            if score > self.MAX_CYCLOMATIC_COMPLEXITY:
                self.findings.append(Finding(
                    rule_id="HIGH_COMPLEXITY",
                    message=(
                        f"Method '{node.name}' has cyclomatic complexity "
                        f"{score} (threshold {self.MAX_CYCLOMATIC_COMPLEXITY}). "
                        "Consider splitting into smaller methods."
                    ),
                    severity="medium",
                    weight=4,
                    line=node.position.line if node.position else 0,
                    category="static",
                ))

    def _check_missing_null_checks(self):
        """Heuristic: a method parameter is dereferenced (obj.method() or
        obj.field) without any `if (param == null)` / `if (param != null)`
        guard anywhere in the method body."""
        for _, method in self.tree.filter(javalang.tree.MethodDeclaration):
            if not method.parameters:
                continue
            param_names = {p.name for p in method.parameters if p.type and not p.type.dimensions}
            if not param_names:
                continue

            guarded = set()
            for _, node in method.filter(javalang.tree.BinaryOperation):
                if node.operator in ("==", "!="):
                    for side in (node.operandl, node.operandr):
                        if isinstance(side, javalang.tree.MemberReference) and side.member in param_names:
                            guarded.add(side.member)

            unguarded = param_names - guarded
            if not unguarded:
                continue

            for _, node in method.filter(javalang.tree.MethodInvocation):
                if node.qualifier in unguarded:
                    self.findings.append(Finding(
                        rule_id="MISSING_NULL_CHECK",
                        message=(
                            f"Parameter '{node.qualifier}' in method '{method.name}' "
                            "is dereferenced without a null check, risking a NullPointerException."
                        ),
                        severity="medium",
                        weight=3,
                        line=node.position.line if node.position else method.position.line,
                        category="static",
                    ))
                    unguarded.discard(node.qualifier)

    # ------------------------------------------------------------------
    # Hallucination-pattern checks
    # ------------------------------------------------------------------
    def _check_unknown_imports(self):
        for _, node in self.tree.filter(javalang.tree.Import):
            if not node.path.startswith(KNOWN_JAVA_PACKAGE_PREFIXES):
                # Only flag single-segment or clearly made-up-looking packages
                # to keep false positives low (real third-party packages we
                # don't recognize are common and shouldn't all be flagged).
                segments = node.path.split(".")
                if len(segments) <= 2 and not node.path.startswith("java"):
                    self.findings.append(Finding(
                        rule_id="HALLUCINATED_IMPORT",
                        message=(
                            f"Import '{node.path}' does not match any known JDK "
                            "or common third-party package pattern — looks like "
                            "a hallucinated import."
                        ),
                        severity="high",
                        weight=6,
                        line=node.position.line if node.position else 0,
                        category="hallucination",
                    ))

    def _check_fake_method_calls(self):
        for _, node in self.tree.filter(javalang.tree.MethodInvocation):
            key = (node.qualifier, node.member)
            if key in KNOWN_FAKE_JAVA_CALLS:
                self.findings.append(Finding(
                    rule_id="HALLUCINATED_METHOD",
                    message=(
                        f"'{node.qualifier}.{node.member}()' does not exist. "
                        "This looks like an LLM-hallucinated API call."
                    ),
                    severity="high",
                    weight=6,
                    line=node.position.line if node.position else 0,
                    category="hallucination",
                ))

    def _check_call_arity(self):
        for _, node in self.tree.filter(javalang.tree.MethodInvocation):
            if not node.qualifier:
                continue
            table = KNOWN_JAVA_METHOD_ARITY.get(node.qualifier)
            if not table or node.member not in table:
                continue
            spec = table[node.member]
            num_args = len(node.arguments) if node.arguments else 0
            min_args, max_args = spec["min"], spec["max"]
            if num_args < min_args or (max_args is not None and num_args > max_args):
                expected = f"{min_args}" if min_args == max_args else (
                    f"{min_args}-{max_args}" if max_args is not None else f"at least {min_args}"
                )
                self.findings.append(Finding(
                    rule_id="ARGUMENT_MISMATCH",
                    message=(
                        f"'{node.qualifier}.{node.member}()' called with {num_args} "
                        f"argument(s); expected {expected}."
                    ),
                    severity="high",
                    weight=5,
                    line=node.position.line if node.position else 0,
                    category="hallucination",
                ))

    def _check_core_class_typos(self):
        """Catch near-miss typos of well-known JDK core classes in method
        call qualifiers (e.g. 'Systems.out.println' instead of
        'System.out.println'). This is a general similarity check,
        complementing the fixed KNOWN_FAKE_JAVA_CALLS table above which
        only catches specific, pre-known LLM mistakes."""
        seen_lines = set()
        for _, node in self.tree.filter(javalang.tree.MethodInvocation):
            if not node.qualifier:
                continue
            root_name = node.qualifier.split(".")[0]
            if root_name in KNOWN_CORE_JAVA_CLASSES:
                continue  # exact match, not a typo
            if len(root_name) < 4:
                continue  # too short to compare safely, avoid false positives
            if not root_name[0].isupper():
                continue  # Java convention: variables start lowercase, classes
                          # start uppercase — skip likely-variable identifiers
                          # to avoid flagging things like 'mList' or 'array'
            for real_name in KNOWN_CORE_JAVA_CLASSES:
                dist = _levenshtein(root_name, real_name)
                if 0 < dist <= 2:
                    line = node.position.line if node.position else 0
                    if line in seen_lines:
                        break
                    seen_lines.add(line)
                    self.findings.append(Finding(
                        rule_id="HALLUCINATED_CLASS_NAME",
                        message=(
                            f"'{root_name}' looks like a typo of the real JDK class "
                            f"'{real_name}' — this call will not compile."
                        ),
                        severity="high",
                        weight=6,
                        line=line,
                        category="hallucination",
                    ))
                    break

    # ------------------------------------------------------------------
    def _build_report(self) -> Dict[str, Any]:
        total_weight = sum(f.weight for f in self.findings)
        risk_level, risk_score = self._score_to_risk(total_weight)
        return {
            "language": "java",
            "risk_level": risk_level,
            "risk_score": risk_score,
            "total_weight": total_weight,
            "num_findings": len(self.findings),
            "facts": {
                "imports": self.facts.imports,
                "functions": self.facts.functions,
                "num_lines": self.facts.num_lines,
            },
            "findings": [f.to_dict() for f in self.findings],
        }

    @staticmethod
    def _score_to_risk(total_weight: int):
        risk_score = min(100, round((total_weight / 40) * 100))
        if total_weight >= 8:
            return "High", risk_score
        elif total_weight >= 3:
            return "Medium", risk_score
        else:
            return "Low", risk_score


def analyze_java_source(source_code: str) -> Dict[str, Any]:
    analyzer = JavaAnalyzer(source_code)
    return analyzer.analyze()
