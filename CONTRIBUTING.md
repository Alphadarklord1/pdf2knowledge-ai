# Contributing to PDF2Knowledge AI

## Workflow
- Create a branch for each change.
- Keep changes scoped to one feature or fix.
- Run tests before opening a pull request.
- Describe the user-visible impact in the pull request.

## Local setup
```bash
cd "/Users/armankhan/Documents/kb-pdf-decomposer"
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./run_kb_prototype.sh
```

## Validation
```bash
cd "/Users/armankhan/Documents/kb-pdf-decomposer"
PYTHONPATH="/Users/armankhan/Documents/kb-pdf-decomposer" "/Users/armankhan/Documents/malomatia-competition-package/.venv/bin/python" -m pytest -q
```

## Pull request rules
- Explain what changed and why.
- Include screenshots for UI changes.
- Do not commit secrets, `.streamlit/secrets.toml`, or local database files.
- Keep the app usable without an OpenAI key.

## Scope guidance
- Prefer small, reviewable changes.
- Keep bilingual behavior intact.
- Keep enterprise privacy defaults intact.
- If a change affects parsing, export, or RAG behavior, add or update tests.
