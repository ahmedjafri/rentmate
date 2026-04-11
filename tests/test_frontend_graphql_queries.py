import re
from pathlib import Path

from graphql import parse, validate

from gql.schema import schema

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "www" / "rentmate-ui" / "src"


def _validate_document(doc: str) -> list[str]:
    document = parse(doc)
    errors = validate(schema._schema, document)
    return [err.message for err in errors]


def test_frontend_graphql_documents_match_backend_schema():
    graphql_doc = FRONTEND_ROOT / "graphql" / "queries.graphql"
    doc = graphql_doc.read_text()
    errors = _validate_document(doc)
    assert errors == []


def test_frontend_source_has_no_inline_graphql_documents():
    allowed = {
        FRONTEND_ROOT / "graphql" / "queries.graphql",
        FRONTEND_ROOT / "graphql" / "generated.ts",
    }
    inline_pattern = re.compile(r"`\s*(query|mutation)\b", re.MULTILINE)

    offenders: list[str] = []
    for path in FRONTEND_ROOT.rglob("*.[tj]s*"):
        if path in allowed:
            continue
        text = path.read_text()
        if inline_pattern.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == []
