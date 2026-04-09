/**
 * Tests for the Vendors page.
 *
 * Strategy: mock useApp (context) and graphqlQuery (API) so tests are fast and
 * hermetic.  We verify:
 *   - empty state renders correctly
 *   - vendor cards render the right fields
 *   - search and type-filter narrow the list
 *   - add/edit dialog opens with correct pre-fill
 *   - confirm-delete flow calls the mutation and removes the card
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import Vendors from './Vendors';
import type { Vendor } from '@/data/mockData';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockAddVendor = vi.fn();
const mockUpdateVendor = vi.fn();
const mockRemoveVendor = vi.fn();
let mockVendors: Vendor[] = [];
let mockIsLoading = false;

vi.mock('@/context/AppContext', () => ({
  useApp: () => ({
    vendors: mockVendors,
    isLoading: mockIsLoading,
    addVendor: mockAddVendor,
    updateVendor: mockUpdateVendor,
    removeVendor: mockRemoveVendor,
    getEntityContext: () => '',
    setEntityContext: vi.fn(),
  }),
}));

const mockGraphqlQuery = vi.fn().mockImplementation((query: string) => {
  // EntityContextCard fetches private notes — return empty
  if (typeof query === 'string' && query.includes('entityNote')) {
    return Promise.resolve({ entityNote: null });
  }
  return Promise.resolve({});
});
vi.mock('@/data/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/data/api')>();
  return {
    ...original,
    graphqlQuery: (...args: unknown[]) => mockGraphqlQuery(...args),
  };
});

// Radix Select uses ResizeObserver internally
global.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeVendor = (overrides: Partial<Vendor> = {}): Vendor => ({
  id: 'v1',
  name: 'Jane Smith',
  company: 'Smith Plumbing',
  vendorType: 'Plumber',
  phone: '555-1234',
  email: 'jane@example.com',
  notes: 'Reliable',
  ...overrides,
});

const renderPage = () => render(<Vendors />);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Vendors page', () => {
  beforeEach(() => {
    mockVendors = [];
    mockIsLoading = false;
    mockAddVendor.mockReset();
    mockUpdateVendor.mockReset();
    mockRemoveVendor.mockReset();
    mockGraphqlQuery.mockReset();
    // Default: handle entity note queries + vendorTypes
    mockGraphqlQuery.mockImplementation((query: string) => {
      if (typeof query === 'string' && query.includes('entityNote')) {
        return Promise.resolve({ entityNote: null });
      }
      return Promise.resolve({ vendorTypes: ['Plumber', 'Electrician', 'HVAC', 'Landscaper'] });
    });
  });

  // --- Render without crash ---

  it('renders without crashing (catches missing VENDOR_TYPES constant regression)', () => {
    // This test would have caught the ReferenceError: VENDOR_TYPES is not defined
    // that occurred when the hardcoded constant was removed but JSX still referenced it.
    expect(() => renderPage()).not.toThrow();
  });

  // --- Empty state ---

  it('shows empty state when there are no vendors', () => {
    renderPage();
    expect(screen.getByText(/no vendors yet/i)).toBeInTheDocument();
  });

  it('shows the correct vendor count in the subtitle', () => {
    mockVendors = [makeVendor()];
    renderPage();
    expect(screen.getByText('1 vendor')).toBeInTheDocument();
  });

  it('pluralises vendor count correctly', () => {
    mockVendors = [makeVendor({ id: 'v1' }), makeVendor({ id: 'v2', name: 'Bob' })];
    renderPage();
    expect(screen.getByText('2 vendors')).toBeInTheDocument();
  });

  // --- Vendor card rendering ---

  it('renders vendor name and company', () => {
    mockVendors = [makeVendor()];
    renderPage();
    expect(screen.getByText('Jane Smith')).toBeInTheDocument();
    expect(screen.getByText('Smith Plumbing')).toBeInTheDocument();
  });

  it('renders vendorType badge', () => {
    mockVendors = [makeVendor()];
    renderPage();
    expect(screen.getByText('Plumber')).toBeInTheDocument();
  });

  it('renders phone and email', () => {
    mockVendors = [makeVendor()];
    renderPage();
    expect(screen.getByText('555-1234')).toBeInTheDocument();
    expect(screen.getByText('jane@example.com')).toBeInTheDocument();
  });

  // --- Search filter ---

  it('filters vendors by name search', () => {
    mockVendors = [
      makeVendor({ id: 'v1', name: 'Jane Smith' }),
      makeVendor({ id: 'v2', name: 'Bob Jones', company: 'Jones HVAC', vendorType: 'HVAC' }),
    ];
    renderPage();
    const input = screen.getByPlaceholderText(/search vendors/i);
    fireEvent.change(input, { target: { value: 'bob' } });
    expect(screen.queryByText('Jane Smith')).not.toBeInTheDocument();
    expect(screen.getByText('Bob Jones')).toBeInTheDocument();
  });

  it('filters vendors by company search', () => {
    mockVendors = [
      makeVendor({ id: 'v1', name: 'Jane Smith', company: 'Smith Plumbing' }),
      makeVendor({ id: 'v2', name: 'Bob Jones', company: 'Jones HVAC', vendorType: 'HVAC' }),
    ];
    renderPage();
    const input = screen.getByPlaceholderText(/search vendors/i);
    fireEvent.change(input, { target: { value: 'jones hvac' } });
    expect(screen.queryByText('Jane Smith')).not.toBeInTheDocument();
    expect(screen.getByText('Bob Jones')).toBeInTheDocument();
  });

  it('shows no-match message when search yields nothing', () => {
    mockVendors = [makeVendor()];
    renderPage();
    const input = screen.getByPlaceholderText(/search vendors/i);
    fireEvent.change(input, { target: { value: 'zzznomatch' } });
    expect(screen.getByText(/no vendors match/i)).toBeInTheDocument();
  });

  // --- Add dialog ---

  it('opens the add dialog when "+ Add Vendor" is clicked', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /add vendor/i }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByRole('heading', { name: 'Add Vendor' })).toBeInTheDocument();
  });

  it.skip('calls createVendor mutation and addVendor on submit', async () => {
    mockGraphqlQuery.mockImplementation((query: string) => {
      if (typeof query === 'string' && query.includes('entityNote')) return Promise.resolve({ entityNote: null });
      if (typeof query === 'string' && query.includes('createVendor')) return Promise.resolve({
        createVendor: { uid: 'new-id', name: 'New Guy', company: null, vendorType: null, phone: null, email: null, notes: null },
      });
      return Promise.resolve({ vendorTypes: ['Plumber'] });
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /add vendor/i }));

    const nameInput = screen.getByLabelText(/name/i);
    fireEvent.change(nameInput, { target: { value: 'New Guy' } });

    const dialog = screen.getByRole('dialog');
    const submitButton = within(dialog).getByRole('button', { name: /add vendor/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(mockGraphqlQuery).toHaveBeenCalledWith(expect.stringContaining('createVendor'), expect.anything());
      expect(mockAddVendor).toHaveBeenCalledWith(expect.objectContaining({ id: 'new-id', name: 'New Guy' }));
    });
  });

  it('shows validation error when name is empty on submit', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /add vendor/i }));
    const dialog = screen.getByRole('dialog');
    const submitButton = within(dialog).getByRole('button', { name: /add vendor/i });
    fireEvent.click(submitButton);
    await waitFor(() => {
      // Should not call createVendor mutation (entity note queries are OK)
      expect(mockGraphqlQuery).not.toHaveBeenCalledWith(
        expect.stringContaining('createVendor'), expect.anything(),
      );
    });
  });

  // --- Edit dialog ---

  it('pre-fills the edit dialog with existing vendor data', () => {
    mockVendors = [makeVendor()];
    renderPage();
    // Click the pencil edit button (first button in the absolute button group on the card)
    const card = screen.getByText('Jane Smith').closest('[class*="p-4"]')!;
    const buttons = card.querySelectorAll('button');
    fireEvent.click(buttons[0]); // first is edit (pencil)
    const nameInput = screen.getByLabelText(/name/i) as HTMLInputElement;
    expect(nameInput.value).toBe('Jane Smith');
  });

  it('calls updateVendor mutation and updateVendor on submit', async () => {
    mockVendors = [makeVendor()];
    mockGraphqlQuery.mockImplementation((query: string) => {
      if (typeof query === 'string' && query.includes('entityNote')) return Promise.resolve({ entityNote: null });
      if (typeof query === 'string' && query.includes('updateVendor')) return Promise.resolve({
        updateVendor: { uid: 'v1', name: 'Jane Updated', company: 'Smith Plumbing', vendorType: 'Plumber', phone: '555-1234', email: 'jane@example.com', notes: 'Reliable' },
      });
      return Promise.resolve({ vendorTypes: ['Plumber'] });
    });

    renderPage();
    const card = screen.getByText('Jane Smith').closest('[class*="p-4"]')!;
    const buttons = card.querySelectorAll('button');
    fireEvent.click(buttons[0]); // first is edit (pencil)

    const nameInput = screen.getByLabelText(/name/i) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: 'Jane Updated' } });

    fireEvent.click(screen.getByRole('button', { name: /update/i }));

    await waitFor(() => {
      expect(mockGraphqlQuery).toHaveBeenCalledWith(expect.stringContaining('updateVendor'), expect.anything());
      expect(mockUpdateVendor).toHaveBeenCalledWith('v1', expect.objectContaining({ name: 'Jane Updated' }));
    });
  });

  // --- Delete flow ---

  it('shows confirm button after clicking trash', () => {
    mockVendors = [makeVendor()];
    renderPage();
    const card = screen.getByText('Jane Smith').closest('[class*="p-4"]')!;
    const buttons = card.querySelectorAll('button');
    fireEvent.click(buttons[1]); // second button is trash
    expect(screen.getByRole('button', { name: /confirm/i })).toBeInTheDocument();
  });

  it('calls deleteVendor mutation and removeVendor on confirm', async () => {
    mockVendors = [makeVendor()];
    mockGraphqlQuery.mockImplementation((query: string) => {
      if (typeof query === 'string' && query.includes('entityNote')) return Promise.resolve({ entityNote: null });
      if (typeof query === 'string' && query.includes('deleteVendor')) return Promise.resolve({ deleteVendor: true });
      return Promise.resolve({ vendorTypes: ['Plumber'] });
    });

    renderPage();
    const card = screen.getByText('Jane Smith').closest('[class*="p-4"]')!;
    const buttons = card.querySelectorAll('button');
    fireEvent.click(buttons[1]); // trash
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));

    await waitFor(() => {
      expect(mockGraphqlQuery).toHaveBeenCalledWith(
        expect.stringContaining('deleteVendor'),
        expect.objectContaining({ uid: 'v1' }),
      );
      expect(mockRemoveVendor).toHaveBeenCalledWith('v1');
    });
  });

  // --- Loading state ---

  it('renders PageLoader while loading', () => {
    mockIsLoading = true;
    renderPage();
    // PageLoader renders a spinner; check it's rendered and cards are not
    expect(screen.queryByText(/no vendors yet/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Jane Smith')).not.toBeInTheDocument();
  });
});
