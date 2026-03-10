# PDF2Knowledge AI

A separate MVP project for the Knowledge Base PDF decomposition challenge.

## What it does
- Upload a PDF
- Extract text page by page using `pypdf`
- Decompose the document into candidate sections
- Detect table-like lines and visual references heuristically
- Generate a knowledge-base draft locally
- Ask grounded questions against the uploaded PDF with a document RAG assistant
- Search detected sections and generated topics from one workspace query
- Split the draft into topic-based Word documents
- Optionally enhance the draft with OpenAI
- Export an editable `.docx` file
- Export a `.zip` bundle with one topic document per detected topic
- Generate internal share packages with share codes and handoff metadata
- Show scan-readiness guidance inspired by mobile document-scanning workflows
- Require sign-in with local accounts and approval-based public signup
- Provide privacy masking, audit events, supervisor account administration, and a support inbox

## Why this is separate
This project is intentionally separate from `/Users/armankhan/Documents/malomatia-competition-package`.
It reuses the same engineering approach: guarded AI usage, structured decomposition, local-first processing, and optional OpenAI enhancement.
The document assistant specifically borrows the Malomatia retrieval pattern:
- chunk extracted sections into smaller units
- score them with a lightweight TF-IDF index
- rerank by heading keyword overlap
- return grounded answers with topic/chunk citations
- optionally ask OpenAI to rewrite the answer only when cited evidence is already present
It also borrows two operational patterns from the earlier projects:
- support feedback and audit-style operational visibility from the Malomatia dashboard
- fast search/filter workflow ideas from StudyPilot for browsing generated knowledge assets

## Run
```bash
cd "/Users/armankhan/Documents/kb-pdf-decomposer"
./run_kb_prototype.sh
```

Open: `http://localhost:8520`

## Deploy
- GitHub repo target: `pdf2knowledge-ai`
- Streamlit app entrypoint: `/Users/armankhan/Documents/kb-pdf-decomposer/kb_app.py`
- Optional Streamlit secrets:

```toml
openai_api_key = "sk-..."
openai_model = "gpt-4o-mini"
```

- If `openai_api_key` is configured in Streamlit secrets, the app will use it automatically when the user leaves the API key field blank.

## Current MVP boundaries
- Best with text-based PDFs
- Scanned/image-only PDFs will likely need OCR, which is not included in this first pass
- Table and visual handling is heuristic in this MVP
- `.docx` and topic bundle export are supported
- Document RAG is grounded to extracted sections, so poor extraction quality will limit answer quality
- Passwords are hashed, but this is still a local prototype account system

## Files
- `kb_app.py`: Streamlit UI
- `kb_parser.py`: PDF extraction and decomposition
- `kb_pipeline.py`: draft generation logic
- `kb_rag.py`: document retrieval, reranking, and grounded Q&A
- `kb_guardrails.py`: prompt/instruction guardrails
- `kb_export.py`: Word export
- `kb_store.py`: local accounts, settings, and audit storage
- `kb_privacy.py`: masking helpers
- `tests/test_kb_pipeline.py`: pipeline, sharing, feedback, and RAG regression tests
- `tests/test_kb_pipeline.py`: basic regression tests
