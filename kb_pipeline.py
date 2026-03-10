from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import math
import re
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

from kb_guardrails import validate_instruction
from kb_parser import DecomposedSection, ParseResult

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u0600-\u06FF]+")
MIN_SECTION_SCORE = 0.03


@dataclass
class DraftSection:
    heading: str
    content: str
    source_pages: list[int] = field(default_factory=list)
    source_headings: list[str] = field(default_factory=list)


@dataclass
class KBDraft:
    title: str
    summary: str
    sections: list[DraftSection]
    visual_notes: list[str]
    table_notes: list[str]
    warnings: list[str]
    llm_used: bool = False


@dataclass
class TopicDocument:
    topic_id: str
    title: str
    summary: str
    sections: list[DraftSection]


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def _tfidf_vector(counter: Counter[str], idf: dict[str, float], total_tokens: int) -> tuple[dict[str, float], float]:
    if total_tokens <= 0:
        return {}, 0.0
    vector: dict[str, float] = {}
    for token, count in counter.items():
        weight = (count / total_tokens) * idf.get(token, 0.0)
        if weight > 0:
            vector[token] = weight
    norm = math.sqrt(sum(v * v for v in vector.values()))
    return vector, norm


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(token, 0.0) for token, weight in a.items())


def _rank_sections(parse_result: ParseResult, instruction: str) -> list[tuple[DecomposedSection, float]]:
    query_tokens = tokenize(instruction)
    if not query_tokens:
        return [(section, 0.0) for section in parse_result.sections]
    df = Counter()
    section_tokens: dict[str, list[str]] = {}
    for section in parse_result.sections:
        text = f"{section.heading}\n{section.body}"
        tokens = tokenize(text)
        section_tokens[section.heading] = tokens
        df.update(set(tokens))
    total_sections = max(1, len(parse_result.sections))
    idf = {token: math.log((1 + total_sections) / (1 + freq)) + 1.0 for token, freq in df.items()}
    q_counter = Counter(query_tokens)
    q_vec, q_norm = _tfidf_vector(q_counter, idf, len(query_tokens))
    ranked: list[tuple[DecomposedSection, float]] = []
    for section in parse_result.sections:
        tokens = section_tokens.get(section.heading, [])
        s_counter = Counter(tokens)
        s_vec, s_norm = _tfidf_vector(s_counter, idf, len(tokens))
        score = 0.0 if q_norm <= 0 or s_norm <= 0 else _dot(q_vec, s_vec) / (q_norm * s_norm)
        ranked.append((section, score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _local_draft(parse_result: ParseResult, instruction: str) -> KBDraft:
    ranked = _rank_sections(parse_result, instruction)
    chosen = [section for section, score in ranked if score >= MIN_SECTION_SCORE][:6]
    if not chosen:
        chosen = [section for section, _ in ranked[:6]]
    title = instruction.strip().split("\n", 1)[0][:90] or "Knowledge Base Draft"
    summary_parts = []
    visual_notes: list[str] = []
    table_notes: list[str] = []
    draft_sections: list[DraftSection] = []

    for idx, section in enumerate(chosen, start=1):
        snippet = " ".join(section.body.split())[:500]
        if idx <= 2 and snippet:
            summary_parts.append(snippet)
        visual_notes.extend(f"{item} [p{','.join(map(str, section.page_numbers))}]" for item in section.visual_references)
        table_notes.extend(f"{item} [p{','.join(map(str, section.page_numbers))}]" for item in section.table_like_lines)
        draft_sections.append(
            DraftSection(
                heading=section.heading,
                content=f"{snippet}\n\nSource pages: {', '.join(map(str, section.page_numbers))}",
                source_pages=section.page_numbers,
                source_headings=[section.heading],
            )
        )

    summary = " ".join(summary_parts)[:900] or "The document was decomposed into structured sections for KB drafting."
    return KBDraft(
        title=title,
        summary=summary,
        sections=draft_sections,
        visual_notes=list(dict.fromkeys(visual_notes))[:20],
        table_notes=list(dict.fromkeys(table_notes))[:20],
        warnings=parse_result.warnings.copy(),
        llm_used=False,
    )


def _openai_chat(prompt: str, api_key: str, model: str) -> str | None:
    body = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a constrained knowledge-base drafting assistant. "
                        "Use only the extracted PDF content provided. Preserve meaning, state ambiguity clearly, and do not invent facts."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return str(content).strip() if content else None


def generate_kb_draft(parse_result: ParseResult, instruction: str, *, openai_api_key: str | None = None, openai_model: str = "gpt-4o-mini") -> KBDraft:
    allowed, error = validate_instruction(instruction)
    if not allowed:
        raise ValueError(error or "Instruction blocked")
    draft = _local_draft(parse_result, instruction)
    if not openai_api_key:
        return draft

    excerpt_blocks = []
    for section in draft.sections[:6]:
        excerpt_blocks.append(f"## {section.heading}\n{section.content}")
    prompt = (
        f"Instruction:\n{instruction}\n\n"
        f"Write a knowledge-base draft with a short summary and section content.\n"
        f"Preserve source meaning and mention uncertainty when needed.\n\n"
        f"Extracted content:\n{chr(10).join(excerpt_blocks)}"
    )
    response = _openai_chat(prompt, openai_api_key, openai_model)
    if response:
        draft.summary = response[:2000]
        draft.llm_used = True
    else:
        draft.warnings.append("OpenAI enhancement failed; local draft was used.")
    return draft


def split_draft_into_topic_documents(draft: KBDraft) -> list[TopicDocument]:
    documents: list[TopicDocument] = []
    for index, section in enumerate(draft.sections, start=1):
        documents.append(
            TopicDocument(
                topic_id=f"topic-{index}",
                title=section.heading,
                summary=section.content.split("\n", 1)[0][:400],
                sections=[section],
            )
        )
    return documents
