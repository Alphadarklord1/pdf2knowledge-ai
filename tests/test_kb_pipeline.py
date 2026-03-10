from __future__ import annotations

import uuid

from kb_export import export_draft_to_docx_bytes, export_share_package_bytes, export_topic_bundle_zip_bytes
from kb_parser import DecomposedSection, ParseResult, PdfPage, decompose_pages
from kb_pipeline import generate_kb_draft
from kb_rag import answer_question
from kb_store import (
    authenticate_user,
    create_share_item,
    create_signup_user,
    init_db,
    list_feedback_items,
    list_share_items,
    list_users,
    review_feedback_item,
    submit_feedback,
)


def test_decompose_pages_groups_content_into_sections() -> None:
    pages = [
        PdfPage(page_number=1, text="Introduction\nThis is the first page.\nTable 1 Revenue 2025 2026", text_char_count=68, recommended_mode="grayscale"),
        PdfPage(page_number=2, text="Process Overview\nFigure 2 workflow diagram\nThe process continues here.", text_char_count=71, recommended_mode="original"),
    ]
    result = decompose_pages(pages)
    assert len(result.sections) >= 2
    assert result.total_tables >= 1
    assert result.total_visual_references >= 1


def test_generate_kb_draft_returns_sections() -> None:
    parse_result = ParseResult(
        pages=[PdfPage(page_number=1, text="")],
        sections=[
            DecomposedSection(heading="Introduction", body="This is the source content.", page_numbers=[1]),
            DecomposedSection(heading="Steps", body="Step one. Step two.", page_numbers=[1]),
        ],
        warnings=[],
        total_tables=0,
        total_visual_references=0,
    )
    draft = generate_kb_draft(parse_result, "Create a KB article for reviewers.")
    assert draft.title
    assert draft.sections
    assert draft.llm_used is False


def test_docx_export_returns_bytes() -> None:
    parse_result = ParseResult(
        pages=[PdfPage(page_number=1, text="")],
        sections=[DecomposedSection(heading="Intro", body="Hello world", page_numbers=[1])],
        warnings=[],
        total_tables=0,
        total_visual_references=0,
    )
    draft = generate_kb_draft(parse_result, "Create a KB article.")
    payload = export_draft_to_docx_bytes(draft)
    assert payload[:2] == b"PK"
    bundle = export_topic_bundle_zip_bytes(draft)
    assert bundle[:2] == b"PK"


def test_signup_creates_pending_user_and_auth_works_for_seeded_admin() -> None:
    init_db()
    admin = authenticate_user("kb_admin", "Admin@123")
    assert admin is not None
    unique_user = f"new_user_{uuid.uuid4().hex[:8]}"
    ok, _ = create_signup_user(unique_user, "New User", "Password@123")
    assert ok is True
    users = list_users()
    assert any(item["user_id"] == unique_user and item["status"] == "pending" for item in users)


def test_document_rag_returns_grounded_hits() -> None:
    parse_result = ParseResult(
        pages=[PdfPage(page_number=1, text="")],
        sections=[
            DecomposedSection(
                heading="Security Controls",
                body="Passwords are hashed and sensitive data is masked during review.",
                page_numbers=[1],
            ),
            DecomposedSection(
                heading="Export Workflow",
                body="The system exports one master Word file and multiple topic documents.",
                page_numbers=[1],
            ),
        ],
        warnings=[],
        total_tables=0,
        total_visual_references=0,
    )
    result = answer_question(parse_result, "How does the system protect sensitive data?", language="en", top_k=3)
    assert result["hits"]
    assert result["policy_blocked"] is False


def test_feedback_submission_and_review_flow() -> None:
    init_db()
    ok, _ = submit_feedback("kb_admin", "ui", "Need a clearer topic filter for generated articles.")
    assert ok is True
    items = list_feedback_items(limit=20)
    assert items
    feedback_id = items[0]["feedback_id"]
    review_feedback_item(feedback_id, "kb_admin", "resolved")
    updated = list_feedback_items(limit=20)
    assert any(item["feedback_id"] == feedback_id and item["status"] == "resolved" for item in updated)


def test_share_package_creation_and_export() -> None:
    init_db()
    parse_result = ParseResult(
        pages=[PdfPage(page_number=1, text="")],
        sections=[DecomposedSection(heading="Topic One", body="Body text", page_numbers=[1])],
        warnings=[],
        total_tables=0,
        total_visual_references=0,
    )
    draft = generate_kb_draft(parse_result, "Create KB articles.")
    share_item = create_share_item("kb_admin", draft.title, share_note="Share with reviewers", source_filename="sample.pdf")
    assert share_item["share_code"]
    assert any(item["share_id"] == share_item["share_id"] for item in list_share_items(limit=20))
    payload = export_share_package_bytes(draft, share_item["share_code"], share_note="Share with reviewers", source_filename="sample.pdf")
    assert payload[:2] == b"PK"
