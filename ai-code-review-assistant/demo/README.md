# Demo

Run the app locally (`python src/app.py`, then open `http://localhost:5000`)
and paste any snippet from `data/sample_snippets.md` to reproduce the demo.

The textarea comes pre-loaded with a snippet that triggers:
- an unused import
- a bare `except:` block
- a hardcoded API key
- a missing `None` check
- a hallucinated `stringutils` import
- a hallucinated `math.squareroot` call
- an argument-count mismatch on `json.loads`

Clicking "Analyze Code" renders a High-risk report with all of these findings
listed, each with its rule id, severity, line number, and weight — exactly
like the JSON example in the main README's Results section.

For a recorded walkthrough, screen-record the above flow and drop the
`.gif`/`.mp4` link here.
