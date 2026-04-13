import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from gql.schema import schema

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = REPO_ROOT / "www" / "rentmate-ui"
GRAPHQL_DIR = FRONTEND_DIR / "src" / "graphql"


def _read(path: Path) -> str:
    return path.read_text().replace("\r\n", "\n")


def _codegen_command(tmp_config: Path) -> list[str]:
    local_bin = FRONTEND_DIR / "node_modules" / ".bin" / "graphql-codegen"
    if local_bin.exists():
        return [str(local_bin), "--config", str(tmp_config)]
    npm = shutil.which("npm")
    if not npm:
        raise FileNotFoundError(
            "Could not find graphql-codegen locally and `npm` is not available for fallback resolution.",
        )
    return [
        npm,
        "exec",
        "--yes",
        "--package",
        "@graphql-codegen/cli",
        "--",
        "graphql-codegen",
        "--config",
        str(tmp_config),
    ]


def main() -> int:
    checked_in_schema = GRAPHQL_DIR / "schema.graphql"
    checked_in_queries = GRAPHQL_DIR / "queries.graphql"
    checked_in_generated = GRAPHQL_DIR / "generated.ts"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tmp_schema = tmp / "schema.graphql"
        tmp_queries = tmp / "queries.graphql"
        tmp_generated = tmp / "generated.ts"
        tmp_config = tmp / "codegen.ts"

        tmp_schema.write_text(schema.as_str())
        shutil.copy2(checked_in_queries, tmp_queries)
        tmp_config.write_text(
            f"""import type {{ CodegenConfig }} from '@graphql-codegen/cli';

const config: CodegenConfig = {{
  schema: '{tmp_schema.as_posix()}',
  documents: ['{tmp_queries.as_posix()}'],
  generates: {{
    '{tmp_generated.as_posix()}': {{
      plugins: ['typescript', 'typescript-operations', 'typed-document-node'],
      config: {{
        enumsAsTypes: true,
        avoidOptionals: true,
        skipTypename: true,
        scalars: {{
          JSON: 'unknown',
        }},
      }},
    }},
  }},
}};

export default config;
""",
        )

        result = subprocess.run(_codegen_command(tmp_config), cwd=FRONTEND_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode

        failures: list[str] = []
        if _read(checked_in_schema) != _read(tmp_schema):
            failures.append(
                "GraphQL schema export is stale. Run `npm run graphql:schema --prefix www/rentmate-ui`.",
            )
        if _read(checked_in_generated) != _read(tmp_generated):
            failures.append(
                "Generated GraphQL types are stale. Run `npm run graphql:codegen --prefix www/rentmate-ui`.",
            )

        if failures:
            sys.stderr.write("\n".join(failures) + "\n")
            return 1

    print("GraphQL codegen artifacts are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
