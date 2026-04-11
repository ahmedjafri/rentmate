import { expect, Page } from '@playwright/test';
import { print } from 'graphql';
import type { TypedDocumentNode } from '@graphql-typed-document-node/core';

import { LoginDocument } from '@/graphql/generated';

export async function loginViaGraphql(page: Page): Promise<string> {
  const data = await graphqlRequest(page, LoginDocument, {
    input: { password: 'rentmate' },
  });
  const token = data.login?.token;
  if (!token) throw new Error(`Login failed: ${JSON.stringify(data)}`);
  return token;
}

export async function graphqlRequest<TResult, TVariables>(
  page: Page,
  document: TypedDocumentNode<TResult, TVariables>,
  variables: TVariables,
  token?: string,
): Promise<TResult> {
  const res = await page.request.post('/graphql', {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    data: {
      query: print(document),
      variables,
    },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  if (body.errors?.length) {
    throw new Error(`GraphQL error: ${JSON.stringify(body.errors)}`);
  }
  return body.data as TResult;
}
