# Demo

Run the app locally (`python src/app.py`, then open `http://localhost:5000`)
and use the **Python sample** / **Java sample** toggle buttons above the
textarea to pre-load a matching pair of buggy snippets in each language, or
paste your own from `data/sample_snippets.md`.

**Python sample** triggers:
- an unused import
- a bare `except:` block
- a hardcoded API key
- a missing `None` check
- a hallucinated `stringutils` import
- a hallucinated `math.squareroot` call
- an argument-count mismatch on `json.loads`

**Java sample** triggers the equivalent set for Java syntax:
- an unused import
- an empty `catch (Exception e) {}` block (swallowed exception)
- a hardcoded API key
- a missing null-check before dereferencing a `String` parameter
- a hallucinated `fakepkg.MadeUp` import
- hallucinated `Math.squareroot()` / `String.valueof()` calls

Clicking "Analyze Code" renders a High-risk report with all applicable
findings listed for whichever language was pasted, each with its rule id,
severity, line number, and weight — matching the JSON examples in the main
README's Results section. The report also shows a small language pill
(`python` / `java`) confirming which parser handled the request.

For a recorded walkthrough, screen-record analyzing both samples side by
side and drop the `.gif`/`.mp4` link here.
