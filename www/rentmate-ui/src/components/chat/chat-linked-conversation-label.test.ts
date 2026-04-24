import { describe, expect, it } from 'vitest';

import { getLinkedConversationTabLabel } from './ChatPanel';

describe('getLinkedConversationTabLabel', () => {
  it('prefixes vendor conversations clearly', () => {
    expect(getLinkedConversationTabLabel({ conversationType: 'vendor', label: 'Handyman Rob', participants: [] } as any))
      .toBe('Vendor: Handyman Rob');
  });

  it('prefixes tenant conversations clearly', () => {
    expect(getLinkedConversationTabLabel({ conversationType: 'tenant', label: 'Alice Renter', participants: [] } as any))
      .toBe('Tenant: Alice Renter');
  });

  it('leaves other conversation labels unchanged', () => {
    expect(getLinkedConversationTabLabel({ conversationType: 'user_ai', label: 'AI', participants: [] } as any))
      .toBe('AI');
  });

  it('uses the vendor participant name when there is only one vendor thread with a generic label', () => {
    const convo = {
      conversationType: 'vendor',
      label: 'Vendor',
      participants: [{ participantType: 'vendor', name: 'Handyman Rob' }],
    } as any;
    expect(getLinkedConversationTabLabel(convo, [convo])).toBe('Vendor: Handyman Rob');
  });

  it('uses the tenant participant name when there is only one tenant thread with a generic label', () => {
    const convo = {
      conversationType: 'tenant',
      label: 'Tenant',
      participants: [{ participantType: 'tenant', name: 'Alice Renter' }],
    } as any;
    expect(getLinkedConversationTabLabel(convo, [convo])).toBe('Tenant: Alice Renter');
  });

  it('keeps generic labels when there are multiple vendor threads', () => {
    const first = {
      conversationType: 'vendor',
      label: 'Vendor',
      participants: [{ participantType: 'vendor', name: 'Handyman Rob' }],
    } as any;
    const second = {
      conversationType: 'vendor',
      label: 'Vendor',
      participants: [{ participantType: 'vendor', name: 'QuickFix Plumbing' }],
    } as any;
    expect(getLinkedConversationTabLabel(first, [first, second])).toBe('Vendor: Vendor');
  });
});
