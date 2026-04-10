const TOKEN_KEY = 'jwtToken';
const GRAPHQL_URL = '/graphql';

const LOGIN_MUTATION = `
  mutation Login($input: LoginInput!) {
    login(input: $input) {
      token
      user { uid username }
    }
  }
`;

export async function login(password: string, email?: string): Promise<void> {
  const res = await fetch(GRAPHQL_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: LOGIN_MUTATION,
      variables: { input: { password, ...(email ? { email } : {}) } },
    }),
  });
  const text = await res.text();
  if (!text) throw new Error(`Server error (HTTP ${res.status})`);
  const { data, errors } = JSON.parse(text);
  if (errors?.length) throw new Error(errors[0].message);
  const token = data?.login?.token;
  if (!token) throw new Error('Login failed. Please check your credentials.');
  localStorage.setItem(TOKEN_KEY, token);
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || '';
}

/** Decode the JWT payload without verifying the signature. */
export function getTokenPayload(): { sub: string; email: string } | null {
  const token = getToken();
  if (!token) return null;
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '=='.slice((b64.length % 4 === 0) ? 4 : b64.length % 4);
    return JSON.parse(atob(padded));
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) return false;
  try {
    // JWT uses base64url — convert to standard base64 before atob
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '=='.slice((b64.length % 4 === 0) ? 4 : b64.length % 4);
    const payload = JSON.parse(atob(padded));
    if (payload.exp && payload.exp * 1000 < Date.now()) {
      localStorage.removeItem(TOKEN_KEY);
      return false;
    }
  } catch {
    // Can't decode — leave token alone, let the server decide
  }
  return true;
}

/**
 * Wraps fetch and automatically handles 401 responses by clearing the token
 * and dispatching auth:logout so AuthGate shows the login screen.
 */
export async function authFetch(input: RequestInfo, init?: RequestInit): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    logout();
    window.dispatchEvent(new Event('auth:logout'));
  }
  return res;
}
