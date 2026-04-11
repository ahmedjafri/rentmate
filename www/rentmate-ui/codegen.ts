import type { CodegenConfig } from '@graphql-codegen/cli';

const config: CodegenConfig = {
  schema: './src/graphql/schema.graphql',
  documents: ['./src/graphql/queries.graphql'],
  generates: {
    './src/graphql/generated.ts': {
      plugins: ['typescript', 'typescript-operations', 'typed-document-node'],
      config: {
        enumsAsTypes: true,
        avoidOptionals: true,
        skipTypename: true,
        scalars: {
          JSON: 'unknown',
        },
      },
    },
  },
};

export default config;
