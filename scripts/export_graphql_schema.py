from pathlib import Path

from gql.schema import schema


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output_path = repo_root / "www" / "rentmate-ui" / "src" / "graphql" / "schema.graphql"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(schema.as_str())
    print(f"Wrote GraphQL schema to {output_path}")


if __name__ == "__main__":
    main()
