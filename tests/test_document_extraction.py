"""
Tests for the document text extraction and LLM field extraction pipeline.

Run a specific PDF:
    pytest tests/test_document_extraction.py -s \
        --pdf path/to/lease.pdf

Defaults to the sample file in evals/ when no --pdf flag is given.
"""

import json
import os
import socket
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import pytest
from pypdf import PdfReader

pytestmark = pytest.mark.filterwarnings(
    "ignore:Use 'content=<...>' to upload raw bytes/text content.:DeprecationWarning"
)

# ── fixtures / CLI option ────────────────────────────────────────────────────

SAMPLE_PDF = (
    Path(__file__).parent.parent
    / "evals"
    / "sample_rental_agreement.pdf"
)


@pytest.fixture(scope="module")
def pdf_path(request):
    p = request.config.getoption("--pdf")
    path = Path(p) if p else SAMPLE_PDF
    if not path.exists():
        pytest.skip(f"PDF not found: {path}")
    return path


@pytest.fixture(scope="module")
def pdf_bytes(pdf_path):
    return pdf_path.read_bytes()


def _extract_text_with_fields(pdf_bytes: bytes) -> str:
    """Mirror the logic in document_processor.py: page text + AcroForm field values."""
    reader = PdfReader(BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    raw = "\n".join(pages).strip()
    try:
        fields = reader.get_fields() or {}
        form_lines = []
        for name, field in fields.items():
            val = field.get("/V")
            if val and isinstance(val, str) and not val.startswith("/"):
                form_lines.append(f"{name}: {val}")
        if form_lines:
            form_section = "[Form Fields]\n" + "\n".join(form_lines)
            raw = form_section + "\n\n" + raw
    except Exception:
        pass
    return raw


@pytest.fixture(scope="module")
def extracted_text(pdf_bytes):
    return _extract_text_with_fields(pdf_bytes)


# ── text extraction tests ────────────────────────────────────────────────────

def test_pdf_readable(pdf_path, pdf_bytes):
    """PDF can be opened and has at least one page."""
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1, "PDF has no pages"


def test_form_fields_extracted(pdf_bytes, pdf_path):
    """AcroForm field values are extracted (filled PDF forms store data here)."""
    reader = PdfReader(BytesIO(pdf_bytes))
    fields = reader.get_fields() or {}
    filled = {k: v.get("/V") for k, v in fields.items()
              if v.get("/V") and isinstance(v.get("/V"), str) and not v.get("/V").startswith("/")}
    print(f"\nForm fields found: {len(fields)} total, {len(filled)} filled")
    for k, v in filled.items():
        print(f"  {k}: {v!r}")


def test_text_extracted(extracted_text, pdf_path):
    """pypdf extracts non-empty text from the PDF (page text + form fields)."""
    print(f"\nFile: {pdf_path.name}")
    print(f"Extracted chars: {len(extracted_text)}")
    # Show the form fields section if present
    if "[Form Fields]" in extracted_text:
        form_section = extracted_text.split("[Form Fields]")[1]
        print(f"Form fields section:\n{form_section[:800]}")
    else:
        print(f"First 500 chars:\n{extracted_text[:500]}")
    assert len(extracted_text) > 0, "No text extracted from PDF"


def test_text_preview(extracted_text):
    """Print a fuller preview — useful for debugging extraction."""
    print(f"\n{'='*60}")
    print("FULL EXTRACTED TEXT (first 3000 chars):")
    print('='*60)
    print(extracted_text[:3000])
    print('='*60)


# ── LLM extraction tests ─────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are a lease document parser. Extract the following fields from the lease text below.
Respond ONLY with a valid JSON object — no markdown fences, no extra text.

Required fields (use null if not found):
{
  "tenant_first_name": string | null,
  "tenant_last_name": string | null,
  "tenant_email": string | null,
  "tenant_phone": string | null,
  "property_address": string | null,
  "unit_label": string | null,
  "lease_start_date": "YYYY-MM-DD" | null,
  "lease_end_date": "YYYY-MM-DD" | null,
  "monthly_rent": number | null
}

Lease document text:
"""

REQUIRED_KEYS = [
    "tenant_first_name", "tenant_last_name", "tenant_email", "tenant_phone",
    "property_address", "unit_label",
    "lease_start_date", "lease_end_date", "monthly_rent",
]


@pytest.fixture(scope="module")
def llm_result(extracted_text):
    """Call the LLM and return parsed JSON. Skips if no API key."""
    model = os.getenv("LLM_MODEL", "deepseek/deepseek-chat")
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or None

    if not api_key:
        pytest.skip("LLM_API_KEY not set — skipping LLM extraction tests")

    if base_url:
        parsed = urlparse(base_url)
        if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port:
            try:
                with socket.create_connection((parsed.hostname, parsed.port), timeout=0.5):
                    pass
            except OSError:
                pytest.skip(f"LLM backend unavailable for extraction test: cannot connect to {base_url}")

    import litellm
    truncated = extracted_text[:12000]
    try:
        response = litellm.completion(
            model=model,
            api_key=api_key,
            base_url=base_url,
            messages=[{"role": "user", "content": EXTRACTION_PROMPT + truncated}],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        pytest.skip(f"LLM backend unavailable for extraction test: {exc}")
    raw = response.choices[0].message.content
    result = json.loads(raw)
    return result


def test_llm_returns_all_keys(llm_result):
    """LLM response contains all expected fields."""
    print(f"\nLLM extraction result:\n{json.dumps(llm_result, indent=2)}")
    for key in REQUIRED_KEYS:
        assert key in llm_result, f"Missing key: {key}"


def test_llm_result_types(llm_result):
    """All extracted values are strings, numbers, or null."""
    for key, val in llm_result.items():
        assert val is None or isinstance(val, (str, int, float)), \
            f"Unexpected type for {key}: {type(val).__name__} = {val!r}"


def test_llm_extraction_summary(llm_result):
    """Print a summary of which fields were found vs null."""
    found = {k: v for k, v in llm_result.items() if v is not None}
    missing = [k for k, v in llm_result.items() if v is None]
    print(f"\nFound ({len(found)}): {list(found.keys())}")
    print(f"Missing/null ({len(missing)}): {missing}")
    if found:
        for k, v in found.items():
            print(f"  {k}: {v}")
    else:
        print("\n  ⚠ No fields extracted — document may be a blank template.")


def test_blank_template_detection(llm_result, extracted_text):
    """Warn clearly if the document looks like an unfilled template."""
    blank_indicators = extracted_text.count("_____")
    all_null = all(v is None for v in llm_result.values())

    print(f"\nBlank field indicators (___): {blank_indicators}")
    print(f"All LLM fields null: {all_null}")

    if blank_indicators > 5 and all_null:
        pytest.xfail(
            "Document appears to be a blank template with no filled-in data. "
            "Upload a completed lease agreement to extract tenant/property info."
        )
