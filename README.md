# AI Code Review Risk Assistant

## Problem
AI coding assistants now write a large share of the code that lands in real
pull requests, but review capacity hasn't scaled with it. Reviewers are
increasingly asked to catch not just ordinary bugs, but a newer failure mode —
LLM "hallucinated" code that looks fluent but calls functions that don't
exist, imports non-existent modules, or passes the wrong number of arguments.
Manually scanning every AI-generated diff for this is slow and easy to miss.

## Solution
This tool statically analyzes a pasted Python or Java file/diff and flags two
categories of risk: (1) classic static-analysis issues — unused imports,
bare/swallowed exceptions, hardcoded secrets, missing null-checks, and
overly complex functions — and (2) hallucination-pattern issues specific to
AI-generated code, like calls to non-existent standard-library/JDK methods,
attribute typos (`json.parse` instead of `json.loads`, `Math.squareroot`
instead of `Math.sqrt`), and argument-count mismatches. The input language
is auto-detected from the file extension (`.py` vs `.java`) and routed to
the matching AST parser. It produces a weighted Low/Medium/High risk score
and a structured JSON + human-readable report, in the same shape regardless
of source language.

## Live Demo
Try it here: [janani-code-review-assistant.azurewebsites.net](https://janani-code-review-assistant-hfdjhpa3d5eva7g8.koreacentral-01.azurewebsites.net)

Hosted on Azure App Service (Free tier) via GitHub Actions CI/CD — every push to `main` auto-deploys.

## Architecture
![architecture diagram](docs/architecture.png)

The frontend posts pasted code (plus a filename) to a Flask REST API. The API
detects the source language from the file extension and routes to one of two
AST-based rule engines: `analyzer.py` (built on Python's built-in `ast`
module) for `.py` files, or `java_analyzer.py` (built on the `javalang`
library) for `.java` files. Both engines walk their respective parse tree
once to extract structural facts (imports, functions/methods, calls) and
then run an independent set of rule checks over those facts, producing the
same `Finding` shape (rule id, severity, weight, line, category) regardless
of language. Each rule violation becomes a "finding"; a shared scorer sums
the weights into a normalized 0–100 risk score and a Low/Medium/High bucket.
Reports — tagged with their detected language — are persisted to SQLite so
past analyses across both languages can be browsed via `/api/history`.

## Tech Stack
- Python 3.12
- Flask 3 (REST API)
- Python's built-in `ast` module (Python parsing, no external dependency)
- `javalang` (Java parsing — pure-Python Java AST parser)
- SQLite (via `sqlite3`, no ORM needed for this scale)
- Vanilla HTML/CSS/JS frontend (no build step) — includes a language toggle
  with Python/Java sample snippets
- pytest for the test suite

## Features
- Paste or upload a Python **or Java** file/diff for analysis — language is
  auto-detected from the filename extension (`.py` / `.java`), with an
  optional explicit override via the API
- Static checks (both languages): unused imports, bare/swallowed exceptions
  (`except:` in Python, empty/overly-broad `catch` in Java), hardcoded
  secrets/credentials, missing null-guards, cyclomatic complexity
- Hallucination-pattern checks (both languages): non-existent imports,
  hallucinated methods/attributes (`math.squareroot`, `Math.squareroot`,
  `String.valueof`), argument-count mismatches against a curated table of
  real stdlib/JDK method signatures
- Weighted Low/Medium/High risk score (0–100 normalized score), computed by
  a shared, language-agnostic scorer
- JSON report + a readable summary rendered in the browser, tagged with the
  detected language
- Analysis history stored in SQLite (with a `language` column) and
  browsable via the UI
- 29 automated tests: 12 for the Python engine, 12 for the Java engine, 5
  for language-detection/routing at the API layer

## Setup & Run
```bash
git clone <this-repo-url>
cd ai-code-review-assistant
pip install -r requirements.txt --break-system-packages   # or use a venv
cd src
python app.py
```
Then open `http://localhost:5000` in a browser. A sample vulnerable snippet
is pre-loaded in the textarea — click "Analyze Code" to see it scored.

### Deployment
This repo includes a `render.yaml` for one-click deployment on
[Render](https://render.com)'s free tier. Connect the GitHub repo in the
Render dashboard and it will build and start automatically using the
`gunicorn` command in `render.yaml`. Note: Render's free tier uses an
ephemeral filesystem, so the SQLite-backed `/api/history` feature resets on
every restart/redeploy — the core `/api/analyze` risk scoring is unaffected.

Run the test suite:
```bash
pytest tests/ -v
```

## Results / Demo
Pasting the Python sample snippet in `data/sample_snippets.md` (Sample 1:
unused import + bare except + hardcoded secret + missing None check)
produces:

```json
{
  "language": "python",
  "risk_level": "High",
  "risk_score": 30,
  "total_weight": 12,
  "num_findings": 3,
  "findings": [
    {"rule_id": "UNUSED_IMPORT", "severity": "low", "weight": 1, "line": 1},
    {"rule_id": "HARDCODED_SECRET", "severity": "high", "weight": 8, "line": 11},
    {"rule_id": "MISSING_NONE_CHECK", "severity": "medium", "weight": 3, "line": 4}
  ]
}
```

The equivalent Java snippet (`data/sample_snippets.md` Sample 5 — same bugs,
Java syntax) produces the same shape of report, tagged `"language": "java"`,
with `SWALLOWED_EXCEPTION` in place of Python's `BARE_EXCEPT`:

```json
{
  "language": "java",
  "risk_level": "High",
  "risk_score": 30,
  "total_weight": 12,
  "num_findings": 3,
  "findings": [
    {"rule_id": "UNUSED_IMPORT", "severity": "low", "weight": 1, "line": 1},
    {"rule_id": "SWALLOWED_EXCEPTION", "severity": "medium", "weight": 3, "line": 11},
    {"rule_id": "HARDCODED_SECRET", "severity": "high", "weight": 8, "line": 4}
  ]
}
```

**Sanity-checked against real code, not just crafted test snippets:** to
validate the analyzer isn't just pattern-matching its own test fixtures, I
ran it against actual source files from the `requests` library (Python's
most widely-used HTTP client, millions of downloads/day) pulled straight
from PyPI. It correctly found zero false hallucination-pattern flags across
~4,600 lines of real production code, while still (accurately) surfacing
legitimate complexity hotspots — e.g. `models.py`'s `prepare_body` method
came back at cyclomatic complexity 27 against a threshold of 7, and
`utils.py`'s `should_bypass_proxies` at complexity 23 — both of which are
genuinely dense, deeply-nested functions in the real library. That gave
useful confidence that the static-analysis rules (complexity, swallowed
exceptions, unused imports) generalize to real-world code, not just to the
synthetic examples used in the test suite.

See `demo/` for screenshots of the web UI analyzing both a Python and a
Java sample side by side.

## What I learned
Writing the "hallucination pattern" checks was the most interesting part —
you can't just diff against a dictionary of real stdlib/JDK names, because
most hallucinated calls are *plausible* (`json.parse`, `math.squareroot`,
`String.valueof`) rather than random gibberish, so the checks need a small
curated table of common LLM mistakes rather than a generic linter approach.
Porting this idea to Java surfaced a different lesson: Java's static typing
means a `MethodInvocation` node's `qualifier` isn't always resolvable to a
real class without full type inference (which `javalang` doesn't do), so
the hallucinated-call checks are necessarily heuristic — they catch the
common LLM mistakes in the curated table, not an exhaustive JDK diff. I also
learned that cyclomatic complexity thresholds need real-world, per-language
calibration — the textbook default of 10 was too lenient for both Python
and Java given the nested-conditional style AI assistants tend to generate,
and Python's and Java's own idioms (list comprehensions vs. explicit loops)
shift the natural baseline slightly differently, so each language ended up
with its own tuned threshold after testing against realistic examples.

## Future improvements
- Add a diff-aware mode that only analyzes changed lines in a PR, with
  GitHub Action integration for CI
- Expand the hallucinated-function table (both languages) using a larger
  corpus of real vs. LLM-generated code samples
- Add per-rule configurability (teams can tune weights/thresholds)
- Extend language coverage further (e.g. JavaScript/TypeScript via a JS
  AST parser), following the same detect-by-extension routing pattern
  already used for Python vs. Java
- Add authentication and per-user history instead of a single shared SQLite
  file, for multi-user deployments
