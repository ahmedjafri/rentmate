const VENDOR_TOKEN_KEY = 'vendorToken';

export function getVendorToken(): string {
  return localStorage.getItem(VENDOR_TOKEN_KEY) || '';
}

export function setVendorToken(t: string): void {
  localStorage.setItem(VENDOR_TOKEN_KEY, t);
}

export function vendorLogout(): void {
  localStorage.removeItem(VENDOR_TOKEN_KEY);
}

export function isVendorAuthenticated(): boolean {
  const token = getVendorToken();
  if (!token) return false;
  try {
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '=='.slice((b64.length % 4 === 0) ? 4 : b64.length % 4);
    const payload = JSON.parse(atob(padded));
    if (payload.exp && payload.exp * 1000 < Date.now()) {
      vendorLogout();
      return false;
    }
    return payload.type === 'vendor';
  } catch {
    return false;
  }
}
