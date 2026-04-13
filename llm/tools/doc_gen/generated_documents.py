"""Helpers for generating agent-authored HTML documents and rendering them to PDF."""

from __future__ import annotations

import asyncio
import html
import textwrap
from dataclasses import dataclass

from llm.doc_gen_runtime import RenderDocumentRequest, get_doc_gen_client


@dataclass(frozen=True)
class RenderedDocument:
    html: str
    pdf_bytes: bytes
    renderer: str


def _normalize_whitespace(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _paragraphs_from_text(content: str) -> list[str]:
    blocks: list[str] = []
    for raw in _normalize_whitespace(content).split("\n\n"):
        text = raw.strip()
        if not text:
            continue
        if ":" in text and "\n" not in text:
            label, value = text.split(":", 1)
            blocks.append(
                (
                    '<div class="field-row">'
                    f'<div class="field-label">{html.escape(label.strip())}</div>'
                    f'<div class="field-value">{html.escape(value.strip())}</div>'
                    "</div>"
                )
            )
            continue
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if lines and all(line.startswith(("- ", "* ")) for line in lines):
            items = "".join(f"<li>{html.escape(line[2:].strip())}</li>" for line in lines)
            blocks.append(f'<ul class="bullet-list">{items}</ul>')
            continue
        paragraph = " ".join(lines)
        blocks.append(f"<p>{html.escape(paragraph)}</p>")
    return blocks


def _text_to_html_fragment(content: str) -> str:
    blocks = _paragraphs_from_text(content)
    if not blocks:
        return "<p></p>"
    return "\n".join(blocks)


def _normalize_html_fragment(raw_html: str | None, fallback_text: str | None) -> str:
    if raw_html and raw_html.strip():
        return raw_html.strip()
    if fallback_text is not None:
        return _text_to_html_fragment(fallback_text)
    raise ValueError("document generation requires html")


def _build_html_document(*, title: str, body_html: str) -> str:
    escaped_title = html.escape(title.strip() or "Document")
    return textwrap.dedent(
        f"""\
        <!DOCTYPE html>
        <html lang="en">
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{escaped_title}</title>
            <style>
              @page {{
                size: Letter;
                margin: 0.75in 0.65in 0.8in 0.65in;
              }}

              :root {{
                --rm-text: #1f2937;
                --rm-muted: #6b7280;
                --rm-line: #d1d5db;
                --rm-soft: #f3f4f6;
              }}

              * {{
                box-sizing: border-box;
              }}

              html, body {{
                margin: 0;
                padding: 0;
                color: var(--rm-text);
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 11pt;
                line-height: 1.38;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
              }}

              body {{
                counter-reset: page;
              }}

              header {{
                display: flex;
                justify-content: flex-end;
                align-items: center;
                gap: 10px;
                margin-bottom: 18px;
                color: var(--rm-muted);
              }}

              .brand-mark {{
                width: 24px;
                height: 24px;
                border: 1px solid var(--rm-line);
                background: var(--rm-soft);
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-weight: 700;
                font-size: 10px;
                letter-spacing: 0.08em;
              }}

              .brand-name {{
                font-weight: 600;
                font-size: 13px;
                letter-spacing: 0.03em;
              }}

              main {{
                min-height: calc(100vh - 110px);
              }}

              h1 {{
                margin: 0 0 16px;
                font-size: 22pt;
                line-height: 1.1;
                font-weight: 700;
              }}

              h2, h3 {{
                margin: 18px 0 8px;
                font-weight: 700;
              }}

              h2 {{
                font-size: 13.5pt;
                padding-bottom: 5px;
                border-bottom: 1px solid var(--rm-line);
              }}

              h3 {{
                font-size: 11.5pt;
              }}

              p {{
                margin: 0 0 10px;
              }}

              ul, ol {{
                margin: 0 0 12px 20px;
                padding: 0;
              }}

              li {{
                margin: 0 0 5px;
              }}

              .field-row {{
                display: grid;
                grid-template-columns: 140px 1fr;
                gap: 12px;
                align-items: start;
                margin: 0 0 10px;
                break-inside: avoid;
              }}

              .field-label {{
                font-size: 9pt;
                font-weight: 700;
                color: var(--rm-muted);
                text-transform: uppercase;
                letter-spacing: 0.04em;
                padding-top: 8px;
              }}

              .field-value {{
                min-height: 34px;
                border: 1px solid var(--rm-line);
                padding: 8px 10px;
                background: #fff;
              }}

              .bullet-list {{
                margin-left: 18px;
              }}

              .avoid-break {{
                break-inside: avoid;
              }}

              footer {{
                margin-top: 22px;
                padding-top: 8px;
                border-top: 1px solid var(--rm-line);
                color: var(--rm-muted);
                font-size: 8.5pt;
                text-align: right;
              }}
            </style>
          </head>
          <body>
            <header>
              <span class="brand-mark" aria-hidden="true">RM</span>
              <span class="brand-name">RentMate</span>
            </header>
            <main>
              <h1>{escaped_title}</h1>
              {body_html}
            </main>
            <footer>Prepared By RentMate</footer>
          </body>
        </html>
        """
    ).strip()


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
