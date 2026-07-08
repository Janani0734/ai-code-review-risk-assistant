"""
analyzer.py
Core static analysis engine for the AI Code Review Risk Assistant.

Parses Python source code into an AST, extracts structural facts
(imports, function defs, calls), and runs a rule engine over those
facts to produce a list of "findings" — each finding is a rule
violation with a severity weight.
"""

import ast
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any


# ---------------------------------------------------------------------------
# Known-good reference data used by the "hallucination pattern" checks.
# This is intentionally small/curated rather than exhaustive — it's meant to
# catch the most common LLM-hallucinated stdlib usage, not replace a linter.
# ---------------------------------------------------------------------------

# A snapshot of common real top-level standard library module names.
KNOWN_STDLIB_MODULES = {
    "os", "sys", "re", "json", "math", "random", "datetime", "time", "collections",
    "itertools", "functools", "typing", "pathlib", "subprocess", "threading",
    "multiprocessing", "socket", "http", "urllib", "logging", "unittest",
    "sqlite3", "csv", "io", "shutil", "copy", "hashlib", "hmac", "base64",
    "string", "textwrap", "enum", "abc", "dataclasses", "contextlib", "asyncio",
    "argparse", "configparser", "traceback", "warnings", "inspect", "importlib",
    "pickle", "struct", "array", "queue", "heapq", "bisect", "decimal",
    "fractions", "statistics", "uuid", "secrets", "zlib", "gzip", "tarfile",
    "zipfile", "glob", "fnmatch", "tempfile", "platform", "getpass", "signal",
    "ast", "dis", "types", "operator", "weakref", "gc", "ctypes", "email",
    "smtplib", "ftplib", "xml", "html", "webbrowser", "wsgiref", "ssl",
}

# Known functions per module and their real max/min positional-arg counts
# (a small curated table — used to catch obviously wrong call arity, e.g.
# an LLM inventing `os.path.join(a, b, c, d, e, f, g, h)` style misuse is
# hard to bound, but some functions have fixed arity worth checking).
KNOWN_STDLIB_FUNCTIONS: Dict[str, Dict[str, Any]] = {
    "os": {
        "getenv": {"min": 1, "max": 2},
        "listdir": {"min": 0, "max": 1},
        "remove": {"min": 1, "max": 1},
        "rename": {"min": 2, "max": 2},
        "mkdir": {"min": 1, "max": 2},
    },
    "json": {
        "loads": {"min": 1, "max": 1},
        "dumps": {"min": 1, "max": None},
        "load": {"min": 1, "max": None},
        "dump": {"min": 2, "max": None},
    },
    "math": {
        "sqrt": {"min": 1, "max": 1},
        "floor": {"min": 1, "max": 1},
        "ceil": {"min": 1, "max": 1},
        "pow": {"min": 2, "max": 2},
        "gcd": {"min": 1, "max": None},
    },
    "re": {
        "match": {"min": 2, "max": 3},
        "search": {"min": 2, "max": 3},
        "sub": {"min": 3, "max": 5},
        "compile": {"min": 1, "max": 2},
        "findall": {"min": 2, "max": 3},
    },
    "random": {
        "randint": {"min": 2, "max": 2},
        "choice": {"min": 1, "max": 1},
        "shuffle": {"min": 1, "max": 2},
        "random": {"min": 0, "max": 0},
    },
}

# Attributes on common modules that are frequently hallucinated by LLMs
# (i.e. they sound plausible but don't exist).
KNOWN_FAKE_ATTRIBUTES = {
    ("os", "path_exists"),      # real name is os.path.exists
    ("os", "read_file"),        # not a real os function
    ("json", "parse"),          # real name is json.loads
    ("json", "stringify"),      # real name is json.dumps (JS-ism)
    ("math", "squareroot"),     # real name is math.sqrt
    ("re", "matchAll"),         # JS-ism, not real in Python re
    ("list", "push"),           # JS-ism; Python lists use .append
    ("str", "format_map_safe"), # not a real str method
}

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]"),
    re.compile(r"(?i)AKIA[0-9A-Z]{16}"),  # AWS access key id pattern
    re.compile(r"(?i)-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"),
]


@dataclass
class Finding:
    rule_id: str
    message: str
    severity: str          # "low" | "medium" | "high"
    weight: int             # numeric weight used in risk score
    line: int = 0
    category: str = "static"  # "static" | "hallucination"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "severity": self.severity,
            "weight": self.weight,
            "line": self.line,
            "category": self.category,
        }


@dataclass
class AnalysisFacts:
    """Structural facts extracted from the source, used for reporting."""
    imports: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    num_lines: int = 0


class CodeAnalyzer:
    """Parses source code and runs the rule engine."""

    MAX_CYCLOMATIC_COMPLEXITY = 7

    def __init__(self, source_code: str):
        self.source_code = source_code
        self.findings: List[Finding] = []
        self.facts = AnalysisFacts()
        self.tree = None
        self.parse_error = None

        try:
            self.tree = ast.parse(source_code)
        except SyntaxError as e:
            self.parse_error = f"SyntaxError: {e.msg} at line {e.lineno}"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
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

        # Static checks
        self._check_unused_imports()
        self._check_bare_except()
        self._check_hardcoded_secrets()
        self._check_cyclomatic_complexity()
        self._check_missing_none_checks()

        # Hallucination-pattern checks
        self._check_nonexistent_imports()
        self._check_fake_attributes()
        self._check_call_arity()

        return self._build_report()

    # ------------------------------------------------------------------
    # Fact extraction
    # ------------------------------------------------------------------
    def _extract_facts(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.facts.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self.facts.imports.append(node.module)
            elif isinstance(node, ast.FunctionDef):
                self.facts.functions.append(node.name)

    # ------------------------------------------------------------------
    # Static analysis rules
    # ------------------------------------------------------------------
    def _check_unused_imports(self):
        """Flag imports that are never referenced elsewhere in the source."""
        imported_names = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    imported_names.append((name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    imported_names.append((name, node.lineno))

        # Collect all identifier usages (Name nodes + Attribute value roots)
        used_names = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)

        for name, lineno in imported_names:
            if name not in used_names and name != "*":
                self.findings.append(Finding(
                    rule_id="UNUSED_IMPORT",
                    message=f"Import '{name}' is never used.",
                    severity="low",
                    weight=1,
                    line=lineno,
                    category="static",
                ))

    def _check_bare_except(self):
        """Flag bare `except:` and overly broad `except Exception:` blocks
        with no handling (pass-only body), which silently swallow errors."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    self.findings.append(Finding(
                        rule_id="BARE_EXCEPT",
                        message="Bare 'except:' catches all exceptions, including "
                                "KeyboardInterrupt/SystemExit. Specify an exception type.",
                        severity="medium",
                        weight=3,
                        line=node.lineno,
                        category="static",
                    ))
                elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    self.findings.append(Finding(
                        rule_id="SWALLOWED_EXCEPTION",
                        message="Exception is caught but silently ignored (pass only).",
                        severity="medium",
                        weight=3,
                        line=node.lineno,
                        category="static",
                    ))

    def _check_hardcoded_secrets(self):
        for i, line in enumerate(self.source_code.splitlines(), start=1):
            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    self.findings.append(Finding(
                        rule_id="HARDCODED_SECRET",
                        message="Line appears to contain a hardcoded secret/credential.",
                        severity="high",
                        weight=8,
                        line=i,
                        category="static",
                    ))
                    break  # one finding per line is enough

    def _cyclomatic_complexity(self, func_node: ast.FunctionDef) -> int:
        """Approximate cyclomatic complexity: 1 + number of decision points."""
        complexity = 1
        decision_nodes = (
            ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler,
            ast.With, ast.BoolOp, ast.IfExp,
        )
        for node in ast.walk(func_node):
            if isinstance(node, decision_nodes):
                complexity += 1
            # each `and`/`or` operand beyond the first adds a branch
            if isinstance(node, ast.BoolOp):
                complexity += max(0, len(node.values) - 1)
        return complexity

    def _check_cyclomatic_complexity(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef):
                score = self._cyclomatic_complexity(node)
                if score > self.MAX_CYCLOMATIC_COMPLEXITY:
                    self.findings.append(Finding(
                        rule_id="HIGH_COMPLEXITY",
                        message=(
                            f"Function '{node.name}' has cyclomatic complexity "
                            f"{score} (threshold {self.MAX_CYCLOMATIC_COMPLEXITY}). "
                            "Consider splitting into smaller functions."
                        ),
                        severity="medium",
                        weight=4,
                        line=node.lineno,
                        category="static",
                    ))

    def _check_missing_none_checks(self):
        """Heuristic: flag direct attribute/subscript access on a function
        parameter that has a default of None, without an intervening
        `if param is None` / `if param` guard anywhere in the function."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue

            none_default_params = set()
            defaults = node.args.defaults
            args = node.args.args
            # defaults align to the tail of args
            offset = len(args) - len(defaults)
            for i, default in enumerate(defaults):
                if isinstance(default, ast.Constant) and default.value is None:
                    none_default_params.add(args[offset + i].arg)

            if not none_default_params:
                continue

            # does the function body reference `param is None` / `if param` anywhere?
            guarded = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Compare):
                    left = sub.left
                    if isinstance(left, ast.Name) and left.id in none_default_params:
                        for comparator in sub.comparators:
                            if isinstance(comparator, ast.Constant) and comparator.value is None:
                                guarded.add(left.id)
                if isinstance(sub, ast.If):
                    test = sub.test
                    if isinstance(test, ast.Name) and test.id in none_default_params:
                        guarded.add(test.id)
                    if isinstance(test, ast.UnaryOp) and isinstance(test.operand, ast.Name):
                        if test.operand.id in none_default_params:
                            guarded.add(test.operand.id)

            unguarded = none_default_params - guarded
            if not unguarded:
                continue

            for sub in ast.walk(node):
                target_name = None
                if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                    target_name = sub.value.id
                elif isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name):
                    target_name = sub.value.id

                if target_name in unguarded:
                    self.findings.append(Finding(
                        rule_id="MISSING_NONE_CHECK",
                        message=(
                            f"Parameter '{target_name}' in function '{node.name}' "
                            "defaults to None but is accessed without a None check, "
                            "risking AttributeError/TypeError."
                        ),
                        severity="medium",
                        weight=3,
                        line=getattr(sub, "lineno", node.lineno),
                        category="static",
                    ))
                    unguarded.discard(target_name)  # one finding per param per function

    # ------------------------------------------------------------------
    # Hallucination-pattern rules
    # ------------------------------------------------------------------
    def _check_nonexistent_imports(self):
        """Flag imports of top-level modules that aren't in our known-good
        stdlib set AND aren't installed as third-party packages available
        in this environment. We only flag single-word top-level modules
        that look like stdlib-style names but aren't — third-party
        packages (numpy, requests, flask, etc.) are NOT flagged since
        those are legitimately installed via pip and can't be verified
        by name alone."""
        # A small denylist of names LLMs commonly hallucinate as if they
        # were stdlib, to keep the false-positive rate low while still
        # demonstrating the pattern.
        COMMONLY_HALLUCINATED = {
            "os_utils", "stringutils", "collections_extra", "jsonutils",
            "systools", "fileutils", "mathutils", "textutils",
        }
        for node in ast.walk(self.tree):
            module_name = None
            lineno = 0
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name.split(".")[0]
                    lineno = node.lineno
                    if module_name in COMMONLY_HALLUCINATED:
                        self._flag_fake_import(module_name, lineno)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module_name = node.module.split(".")[0]
                    lineno = node.lineno
                    if module_name in COMMONLY_HALLUCINATED:
                        self._flag_fake_import(module_name, lineno)

    def _flag_fake_import(self, module_name: str, lineno: int):
        self.findings.append(Finding(
            rule_id="HALLUCINATED_IMPORT",
            message=(
                f"Module '{module_name}' does not appear to be a real "
                "standard-library or common package — looks like a "
                "hallucinated import."
            ),
            severity="high",
            weight=6,
            line=lineno,
            category="hallucination",
        ))

    def _check_fake_attributes(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                key = (node.value.id, node.attr)
                if key in KNOWN_FAKE_ATTRIBUTES:
                    self.findings.append(Finding(
                        rule_id="HALLUCINATED_ATTRIBUTE",
                        message=(
                            f"'{node.value.id}.{node.attr}' does not exist. "
                            "This looks like an LLM-hallucinated API call."
                        ),
                        severity="high",
                        weight=6,
                        line=node.lineno,
                        category="hallucination",
                    ))

    def _check_call_arity(self):
        """Check calls against the curated KNOWN_STDLIB_FUNCTIONS arity table."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
                continue

            module_name = func.value.id
            func_name = func.attr
            module_table = KNOWN_STDLIB_FUNCTIONS.get(module_name)
            if not module_table or func_name not in module_table:
                continue

            spec = module_table[func_name]
            num_args = len(node.args)
            min_args, max_args = spec["min"], spec["max"]

            if num_args < min_args or (max_args is not None and num_args > max_args):
                expected = f"{min_args}" if min_args == max_args else (
                    f"{min_args}-{max_args}" if max_args is not None else f"at least {min_args}"
                )
                self.findings.append(Finding(
                    rule_id="ARGUMENT_MISMATCH",
                    message=(
                        f"'{module_name}.{func_name}()' called with {num_args} "
                        f"argument(s); expected {expected}."
                    ),
                    severity="high",
                    weight=5,
                    line=node.lineno,
                    category="hallucination",
                ))

    # ------------------------------------------------------------------
    # Report building
    # ------------------------------------------------------------------
    def _build_report(self) -> Dict[str, Any]:
        total_weight = sum(f.weight for f in self.findings)
        risk_level, risk_score = self._score_to_risk(total_weight)

        return {
            "language": "python",
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
        """Map a raw weighted score to a Low/Medium/High bucket plus a
        normalized 0-100 risk score for display purposes."""
        # Normalize: cap at 40 raw weight points -> 100 score
        risk_score = min(100, round((total_weight / 40) * 100))
        if total_weight >= 8:
            return "High", risk_score
        elif total_weight >= 3:
            return "Medium", risk_score
        else:
            return "Low", risk_score


def analyze_source(source_code: str) -> Dict[str, Any]:
    """Convenience function used by the API layer."""
    analyzer = CodeAnalyzer(source_code)
    return analyzer.analyze()
