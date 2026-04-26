import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import RoutineDetail from './RoutineDetail';

const mockGetRoutine = vi.fn();
const mockUpdateRoutine = vi.fn();
const mockDeleteRoutine = vi.fn();
const mockAuthFetch = vi.fn();

vi.mock('@/graphql/client', () => ({
  getRoutine: (...args: unknown[]) => mockGetRoutine(...args),
  updateRoutine: (...args: unknown[]) => mockUpdateRoutine(...args),
  deleteRoutine: (...args: unknown[]) => mockDeleteRoutine(...args),
}));

vi.mock('@/lib/auth', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

const task = {
  uid: 'task-1',
  name: 'Lease expiry review',
  prompt: 'Review expiring leases',
  schedule: '0 9 * * 1',
  scheduleDisplay: 'Every Monday at 9am',
  isDefault: false,
  enabled: false,
  state: 'paused',
  repeat: null,
  completedCount: 0,
  nextRunAt: null,
  lastRunAt: null,
  lastStatus: null,
  lastOutput: null,
  simulatedAt: null,
  createdAt: '2026-04-11T00:00:00Z',
};

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={['/routines/task-1']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/routines/:id" element={<RoutineDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('RoutineDetail simulation', () => {
  beforeEach(() => {
    mockGetRoutine.mockReset();
    mockUpdateRoutine.mockReset();
    mockDeleteRoutine.mockReset();
    mockAuthFetch.mockReset();
    mockGetRoutine.mockResolvedValue({ routine: task });
  });

  it('streams run reasoning traces and final output', async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          `data: ${JSON.stringify({ type: 'progress', text: 'Checking leases' })}\n\n`,
        ));
        controller.enqueue(new TextEncoder().encode(
          `data: ${JSON.stringify({
            type: 'done',
            reply: 'Created 2 renewal suggestions.',
            task: {
              uid: 'task-1',
              lastStatus: 'ok',
              lastOutput: 'Created 2 renewal suggestions.',
              lastRunAt: '2026-04-11T12:00:00Z',
              completedCount: 1,
              nextRunAt: '2026-04-18T09:00:00Z',
              state: 'scheduled',
              enabled: false,
            },
          })}\n\n`,
        ));
        controller.close();
      },
    });

    mockAuthFetch.mockResolvedValue({ ok: true, body: stream });

    renderPage();

    await screen.findByDisplayValue('Lease expiry review');
    fireEvent.click(screen.getByRole('button', { name: /run now/i }));

    await screen.findByText('Run Trace');
    expect(screen.getByText('Checking leases')).toBeInTheDocument();
    expect(screen.getByText('Run Result')).toBeInTheDocument();
    expect(screen.getByText('Created 2 renewal suggestions.')).toBeInTheDocument();
  });

  it('renders simulated suggestions as cards instead of plain prose', async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          `data: ${JSON.stringify({
            type: 'done',
            reply: 'I would create 1 suggestion.',
            suggestions: [{
              id: 'sim-1',
              title: 'Renewal follow-up for Alice Smith',
              body: 'Lease expires on 2026-05-20 for Unit 2A at 123 Test St.',
              category: 'leasing',
              urgency: 'medium',
              property_id: 'prop-123',
              risk_score: 3,
              action_payload: {
                tenant_name: 'Alice Smith',
                unit_label: '2A',
                expiry_date: '2026-05-20',
              },
            }],
          })}\n\n`,
        ));
        controller.close();
      },
    });

    mockAuthFetch.mockResolvedValue({ ok: true, body: stream });

    renderPage();

    await screen.findByDisplayValue('Lease expiry review');
    fireEvent.click(screen.getByRole('button', { name: /simulate/i }));

    await screen.findByText('Actions That Would Be Taken');
    expect(screen.getByText('Simulation Result')).toBeInTheDocument();
    expect(screen.getByText('Renewal follow-up for Alice Smith')).toBeInTheDocument();
    expect(screen.getByText('Lease expires on 2026-05-20 for Unit 2A at 123 Test St.')).toBeInTheDocument();
    expect(screen.getByText('Property: prop-123')).toBeInTheDocument();
    expect(screen.getByText('Risk: 3')).toBeInTheDocument();
    expect(screen.getByText(/"tenant_name": "Alice Smith"/)).toBeInTheDocument();
  });
});
