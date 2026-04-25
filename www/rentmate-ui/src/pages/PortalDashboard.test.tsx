import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import TenantPortal from './TenantPortal';
import VendorPortal from './VendorPortal';

vi.mock('@/lib/tenantAuth', () => ({
  getTenantToken: () => 'tenant-token',
  isTenantAuthenticated: () => true,
  tenantLogout: vi.fn(),
}));

vi.mock('@/lib/vendorAuth', () => ({
  getVendorToken: () => 'vendor-token',
  isVendorAuthenticated: () => true,
  setVendorToken: vi.fn(),
  vendorLogout: vi.fn(),
}));

const fetchMock = vi.fn();

describe('Portal dashboard conversation switching', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', fetchMock);
    Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    });
  });

  it('switches tenant portal conversations by conversation id and shows the portal sidebar', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/tenant/me')) {
        return new Response(JSON.stringify({ id: 'tenant-1', name: 'Alice Renter' }), { status: 200 });
      }
      if (url.endsWith('/api/tenant/conversations')) {
        return new Response(JSON.stringify([
          {
            id: 'conv-1',
            conversation_type: 'tenant',
            title: 'Leaking sink',
            updated_at: '2026-04-24T00:00:00Z',
            last_message_at: '2026-04-24T00:00:00Z',
            last_message_body: 'Checking in',
            last_message_sender_name: 'RentMate',
            linked_task: { id: '1', task_number: 1, title: 'Leaking sink', status: 'active', category: 'maintenance', urgency: 'medium' },
          },
          {
            id: 'conv-2',
            conversation_type: 'tenant',
            title: 'Noise complaint',
            updated_at: '2026-04-24T00:05:00Z',
            last_message_at: '2026-04-24T00:05:00Z',
            last_message_body: 'Any update?',
            last_message_sender_name: 'RentMate',
            linked_task: { id: '2', task_number: 2, title: 'Noise complaint', status: 'active', category: 'compliance', urgency: 'low' },
          },
        ]), { status: 200 });
      }
      if (url.endsWith('/api/tenant/conversations/conv-1')) {
        return new Response(JSON.stringify({
          id: 'conv-1',
          conversation_type: 'tenant',
          title: 'Leaking sink',
          updated_at: '2026-04-24T00:00:00Z',
          last_message_at: '2026-04-24T00:00:00Z',
          last_message_body: 'Checking in',
          last_message_sender_name: 'RentMate',
          linked_task: { id: '1', task_number: 1, title: 'Leaking sink', status: 'active', category: 'maintenance', urgency: 'medium' },
          messages: [
            { id: 'm1', body: 'Sink thread', sender_name: 'RentMate', sender_type: 'account_user', is_ai: false, sent_at: '2026-04-24T00:00:00Z' },
          ],
        }), { status: 200 });
      }
      if (url.endsWith('/api/tenant/conversations/conv-2')) {
        return new Response(JSON.stringify({
          id: 'conv-2',
          conversation_type: 'tenant',
          title: 'Noise complaint',
          updated_at: '2026-04-24T00:05:00Z',
          last_message_at: '2026-04-24T00:05:00Z',
          last_message_body: 'Any update?',
          last_message_sender_name: 'RentMate',
          linked_task: { id: '2', task_number: 2, title: 'Noise complaint', status: 'active', category: 'compliance', urgency: 'low' },
          messages: [
            { id: 'm2', body: 'Noise thread', sender_name: 'RentMate', sender_type: 'account_user', is_ai: false, sent_at: '2026-04-24T00:05:00Z' },
          ],
        }), { status: 200 });
      }
      throw new Error(`Unhandled fetch ${url}`);
    });

    render(
      <MemoryRouter initialEntries={['/tenant-portal']}>
        <TenantPortal />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('Sink thread')).toBeInTheDocument();
    });
    expect(screen.getAllByText('Dashboard').length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByText('Noise complaint')[0]);
    await waitFor(() => {
      expect(screen.getByText('Noise thread')).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByText('Leaking sink')[0]);
    await waitFor(() => {
      expect(screen.getByText('Sink thread')).toBeInTheDocument();
    });
  });

  it('switches vendor portal conversations by conversation id', async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/vendor/me')) {
        return new Response(JSON.stringify({ id: 'vendor-1', name: 'Vince Vendor', has_account: true }), { status: 200 });
      }
      if (url.endsWith('/api/vendor/conversations')) {
        return new Response(JSON.stringify([
          {
            id: 'vendor-conv-1',
            conversation_type: 'vendor',
            title: 'Water heater quote',
            updated_at: '2026-04-24T00:00:00Z',
            last_message_at: '2026-04-24T00:00:00Z',
            last_message_body: 'Can you quote this?',
            last_message_sender_name: 'RentMate',
            linked_task: { id: '3', task_number: 3, title: 'Water heater quote', status: 'active', category: 'maintenance', urgency: 'high' },
          },
          {
            id: 'vendor-conv-2',
            conversation_type: 'vendor',
            title: 'Fence repair',
            updated_at: '2026-04-24T00:10:00Z',
            last_message_at: '2026-04-24T00:10:00Z',
            last_message_body: 'Can you come Friday?',
            last_message_sender_name: 'RentMate',
            linked_task: { id: '4', task_number: 4, title: 'Fence repair', status: 'paused', category: 'maintenance', urgency: 'medium' },
          },
        ]), { status: 200 });
      }
      if (url.endsWith('/api/vendor/conversations/vendor-conv-1')) {
        return new Response(JSON.stringify({
          id: 'vendor-conv-1',
          conversation_type: 'vendor',
          title: 'Water heater quote',
          updated_at: '2026-04-24T00:00:00Z',
          last_message_at: '2026-04-24T00:00:00Z',
          last_message_body: 'Can you quote this?',
          last_message_sender_name: 'RentMate',
          linked_task: { id: '3', task_number: 3, title: 'Water heater quote', status: 'active', category: 'maintenance', urgency: 'high' },
          messages: [
            { id: 'vm1', body: 'Quote thread', sender_name: 'RentMate', sender_type: 'account_user', is_ai: false, sent_at: '2026-04-24T00:00:00Z' },
          ],
        }), { status: 200 });
      }
      if (url.endsWith('/api/vendor/conversations/vendor-conv-2')) {
        return new Response(JSON.stringify({
          id: 'vendor-conv-2',
          conversation_type: 'vendor',
          title: 'Fence repair',
          updated_at: '2026-04-24T00:10:00Z',
          last_message_at: '2026-04-24T00:10:00Z',
          last_message_body: 'Can you come Friday?',
          last_message_sender_name: 'RentMate',
          linked_task: { id: '4', task_number: 4, title: 'Fence repair', status: 'paused', category: 'maintenance', urgency: 'medium' },
          messages: [
            { id: 'vm2', body: 'Fence thread', sender_name: 'RentMate', sender_type: 'account_user', is_ai: false, sent_at: '2026-04-24T00:10:00Z' },
          ],
        }), { status: 200 });
      }
      throw new Error(`Unhandled fetch ${url}`);
    });

    render(
      <MemoryRouter initialEntries={['/vendor-portal']}>
        <VendorPortal />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText('Quote thread')).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByText('Fence repair')[0]);
    await waitFor(() => {
      expect(screen.getByText('Fence thread')).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByText('Water heater quote')[0]);
    await waitFor(() => {
      expect(screen.getByText('Quote thread')).toBeInTheDocument();
    });
  });
});
