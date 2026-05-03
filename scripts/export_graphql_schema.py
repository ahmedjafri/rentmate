import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from gql.schema import schema  # noqa: E402


def main() -> None:
    output_path = REPO_ROOT / "www" / "rentmate-ui" / "src" / "graphql" / "schema.graphql"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(schema.as_str())
    print(f"Wrote GraphQL schema to {output_path}")


if __name__ == "__main__":
    main()
