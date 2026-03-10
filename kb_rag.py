from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from typing import Any

from kb_parser import ParseResult

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u0600-\u06FF]+")
DOC_CITATION_RE = re.compile(r"\[(TOPIC-[0-9]+)/(CHUNK-[0-9]+)\]")
DISALLOWED_QUERY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(delete|erase|destroy|ignore the pdf|invent|make up)\b",
        r"\b(password|secret|token|api key)\b",
        r"\b(احذف|أتلف|تجاهل الملف|اخترع|كلمة المرور|الرمز السري)\b",
    ]
]
MIN_EVIDENCE_SCORE = 0.12


@dataclass(frozen=True)
class RetrievalHit:
    rank: int
    topic_id: str
    chunk_id: str
    title: str
    text: str
    base_score: float
    rerank_score: float
    keyword_hits: list[str]
    reasons: list[str]


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "")]


def _chunk_tokens(tokens: list[str], chunk_size: int = 85, overlap: int = 18) -> list[list[str]]:
    step = max(1, chunk_size - overlap)
    chunks: list[list[str]] = []
    for start in range(0, len(tokens), step):
        part = tokens[start : start + chunk_size]
        if part:
            chunks.append(part)
        if start + chunk_size >= len(tokens):
            break
    return chunks


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


def validate_query(question: str, language: str) -> tuple[bool, str | None]:
    value = question.strip()
    if not value:
        return False, "Enter a question first." if language == "en" else "اكتب سؤالاً أولاً."
    for pattern in DISALLOWED_QUERY_PATTERNS:
        if pattern.search(value):
            return False, (
                "This assistant answers only from the uploaded PDF and cannot reveal secrets or invent content."
                if language == "en"
                else "هذا المساعد يجيب فقط من ملف PDF المرفوع ولا يكشف الأسرار ولا يختلق المحتوى."
            )
    return True, None


def build_index(parse_result: ParseResult) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    for idx, section in enumerate(parse_result.sections, start=1):
        text = f"{section.heading}\n{section.body}".strip()
        tokens = tokenize(text)
        for cidx, part in enumerate(_chunk_tokens(tokens), start=1):
            chunks.append(
                {
                    "topic_id": f"TOPIC-{idx}",
                    "chunk_id": f"CHUNK-{cidx}",
                    "title": section.heading,
                    "text": " ".join(part),
                    "keywords": set(tokenize(section.heading)),
                }
            )
    if not chunks:
        return {"chunks": [], "idf": {}}
    df: Counter[str] = Counter()
    for chunk in chunks:
        df.update(set(tokenize(chunk["text"])))
    total_chunks = len(chunks)
    idf = {token: math.log((1 + total_chunks) / (1 + freq)) + 1.0 for token, freq in df.items()}
    for chunk in chunks:
        counter = Counter(tokenize(chunk["text"]))
        vector, norm = _tfidf_vector(counter, idf, sum(counter.values()))
        chunk["vector"] = vector
        chunk["norm"] = norm
        chunk["counter"] = counter
    return {"chunks": chunks, "idf": idf}


def retrieve(index: dict[str, Any], question: str, top_k: int = 5) -> list[RetrievalHit]:
    query_tokens = tokenize(question)
    if not query_tokens or not index.get("chunks"):
        return []
    q_counter = Counter(query_tokens)
    q_vector, q_norm = _tfidf_vector(q_counter, index["idf"], len(query_tokens))
    if q_norm <= 0:
        return []
    q_set = set(query_tokens)
    ranked: list[dict[str, Any]] = []
    for chunk in index["chunks"]:
        c_norm = float(chunk["norm"])
        if c_norm <= 0:
            continue
        base_score = _dot(q_vector, chunk["vector"]) / (q_norm * c_norm)
        keyword_hits = sorted(q_set.intersection(chunk["keywords"]))
        rerank_score = base_score + (0.06 * len(keyword_hits))
        reasons = []
        if keyword_hits:
            reasons.append(f"keyword_hits={len(keyword_hits)}")
        ranked.append({"chunk": chunk, "base_score": base_score, "rerank_score": rerank_score, "keyword_hits": keyword_hits, "reasons": reasons})
    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
    hits: list[RetrievalHit] = []
    for rank, item in enumerate(ranked[:top_k], start=1):
        chunk = item["chunk"]
        hits.append(
            RetrievalHit(
                rank=rank,
                topic_id=str(chunk["topic_id"]),
                chunk_id=str(chunk["chunk_id"]),
                title=str(chunk["title"]),
                text=str(chunk["text"]),
                base_score=float(item["base_score"]),
                rerank_score=float(item["rerank_score"]),
                keyword_hits=list(item["keyword_hits"]),
                reasons=list(item["reasons"]),
            )
        )
    return hits


def grounded_fallback_answer(hits: list[RetrievalHit], language: str) -> str:
    if not hits:
        return "No relevant document sections were found." if language == "en" else "لم يتم العثور على أقسام وثيقة ذات صلة."
    lines = [
        "Grounded answer from retrieved PDF sections:" if language == "en" else "إجابة موثقة من أقسام PDF المسترجعة:",
    ]
    for hit in hits[:3]:
        lines.append(f"- [{hit.topic_id}/{hit.chunk_id}] {hit.title}: {hit.text[:220]}...")
    return "\n".join(lines)


def _openai_answer(question: str, hits: list[RetrievalHit], api_key: str, model: str, language: str) -> str | None:
    context = "\n\n".join(f"[{h.topic_id}/{h.chunk_id}] {h.title}\n{h.text}" for h in hits[:5])
    body = json.dumps(
        {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a constrained PDF knowledge assistant. Answer only from retrieved chunks from the uploaded PDF. "
                        "Preserve meaning, mention uncertainty, and include citations like [TOPIC-1/CHUNK-1]."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nLanguage: {language}\n\nRetrieved chunks:\n{context}",
                },
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
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
    answer = str(content).strip() if content else None
    if answer and DOC_CITATION_RE.search(answer):
        return answer
    return None


def answer_question(parse_result: ParseResult, question: str, *, language: str = "en", top_k: int = 5, openai_api_key: str | None = None, openai_model: str = "gpt-4o-mini") -> dict[str, Any]:
    allowed, error = validate_query(question, language)
    if not allowed:
        return {
            "answer": error,
            "hits": [],
            "used_llm": False,
            "insufficient_evidence": True,
            "policy_blocked": True,
        }
    index = build_index(parse_result)
    hits = retrieve(index, question, top_k=top_k)
    strong_hits = [hit for hit in hits if hit.rerank_score >= MIN_EVIDENCE_SCORE]
    if not strong_hits:
        return {
            "answer": "Retrieved evidence is insufficient for a reliable answer." if language == "en" else "الأدلة المسترجعة غير كافية لإجابة موثوقة.",
            "hits": [hit.__dict__ for hit in hits],
            "used_llm": False,
            "insufficient_evidence": True,
            "policy_blocked": False,
        }
    if openai_api_key:
        llm_answer = _openai_answer(question, strong_hits, openai_api_key, openai_model, language)
        if llm_answer:
            return {
                "answer": llm_answer,
                "hits": [hit.__dict__ for hit in strong_hits],
                "used_llm": True,
                "insufficient_evidence": False,
                "policy_blocked": False,
            }
    return {
        "answer": grounded_fallback_answer(strong_hits, language),
        "hits": [hit.__dict__ for hit in strong_hits],
        "used_llm": False,
        "insufficient_evidence": False,
        "policy_blocked": False,
    }
