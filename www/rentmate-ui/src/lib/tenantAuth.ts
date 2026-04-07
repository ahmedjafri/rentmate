const TENANT_TOKEN_KEY = 'tenantToken';

export function getTenantToken(): string {
  return localStorage.getItem(TENANT_TOKEN_KEY) || '';
}

export function setTenantToken(t: string): void {
  localStorage.setItem(TENANT_TOKEN_KEY, t);
}

export function tenantLogout(): void {
  localStorage.removeItem(TENANT_TOKEN_KEY);
}

export function isTenantAuthenticated(): boolean {
  const token = getTenantToken();
  if (!token) return false;
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '=='.slice((b64.length % 4 === 0) ? 4 : b64.length % 4);
    const payload = JSON.parse(atob(padded));
    if (payload.exp && payload.exp * 1000 < Date.now()) {
      tenantLogout();
      return false;
    }
    return payload.type === 'tenant';
  } catch {
    return false;
  }
}
