"""Compatibility wrappers for backend doc_gen helpers."""

from __future__ import annotations

import asyncio

from agent.doc_gen_loader import load_doc_gen_module
from agent.doc_gen_runtime import RenderDocumentRequest, get_doc_gen_client

_impl = load_doc_gen_module("generated_documents")

RenderedDocument = _impl.RenderedDocument


def _normalize_whitespace(value: str) -> str:
    return _impl._normalize_whitespace(value)


def _paragraphs_from_text(content: str) -> list[str]:
    return _impl._paragraphs_from_text(content)


def _text_to_html_fragment(content: str) -> str:
    return _impl._text_to_html_fragment(content)


def _normalize_html_fragment(raw_html: str | None, fallback_text: str | None) -> str:
    return _impl._normalize_html_fragment(raw_html, fallback_text)


def _build_html_document(*, title: str, body_html: str) -> str:
    return _impl._build_html_document(title=title, body_html=body_html)


def render_document(*, title: str, html_content: str | None = None, text_content: str | None = None) -> RenderedDocument:
    body_html = _normalize_html_fragment(html_content, text_content)
    html_document = _build_html_document(title=title, body_html=body_html)
    rendered = get_doc_gen_client().render_document(RenderDocumentRequest(html_document=html_document))
    return RenderedDocument(
        html=html_document,
        pdf_bytes=rendered.pdf_bytes,
        renderer=rendered.renderer,
    )


async def render_document_async(*, title: str, html_content: str | None = None, text_content: str | None = None) -> RenderedDocument:
    return await asyncio.to_thread(
        render_document,
        title=title,
        html_content=html_content,
        text_content=text_content,
    )
