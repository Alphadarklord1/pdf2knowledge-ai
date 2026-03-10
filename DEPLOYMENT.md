# PDF2Knowledge AI Deployment

## GitHub
Create a standalone repository for this folder only.
Recommended repository name: `pdf2knowledge-ai`.

## Streamlit Community Cloud
- Repository: your GitHub repo for this project
- Branch: `main`
- Main file path: `kb_app.py`

## Secrets
Paste the following in Streamlit secrets if you want OpenAI-backed draft enhancement and RAG answer synthesis:

```toml
openai_api_key = "sk-..."
openai_model = "gpt-4o-mini"
```

## Notes
- The app works without OpenAI. In that mode it uses local extraction, local drafting heuristics, and local TF-IDF retrieval.
- If `openai_api_key` is configured in secrets, users can leave the UI key field blank and the deployed app will use the secret automatically.
- Keep `kb_app.db` and `.streamlit/secrets.toml` out of source control.
