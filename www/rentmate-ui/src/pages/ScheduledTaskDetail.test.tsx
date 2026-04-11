import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ScheduledTaskDetail from './ScheduledTaskDetail';

const mockGetScheduledTask = vi.fn();
const mockUpdateScheduledTask = vi.fn();
const mockRunScheduledTask = vi.fn();
const mockDeleteScheduledTask = vi.fn();
const mockAuthFetch = vi.fn();

vi.mock('@/graphql/client', () => ({
  getScheduledTask: (...args: unknown[]) => mockGetScheduledTask(...args),
  updateScheduledTask: (...args: unknown[]) => mockUpdateScheduledTask(...args),
  runScheduledTask: (...args: unknown[]) => mockRunScheduledTask(...args),
  deleteScheduledTask: (...args: unknown[]) => mockDeleteScheduledTask(...args),
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
    <MemoryRouter initialEntries={['/scheduled-tasks/task-1']}>
      <Routes>
        <Route path="/scheduled-tasks/:id" element={<ScheduledTaskDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ScheduledTaskDetail simulation', () => {
  beforeEach(() => {
    mockGetScheduledTask.mockReset();
    mockUpdateScheduledTask.mockReset();
    mockRunScheduledTask.mockReset();
    mockDeleteScheduledTask.mockReset();
    mockAuthFetch.mockReset();
    mockGetScheduledTask.mockResolvedValue({ scheduledTask: task });
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

    await screen.findByText('Suggestions That Would Be Created');
    expect(screen.getByText('Simulation Result')).toBeInTheDocument();
    expect(screen.getByText('Renewal follow-up for Alice Smith')).toBeInTheDocument();
    expect(screen.getByText('Lease expires on 2026-05-20 for Unit 2A at 123 Test St.')).toBeInTheDocument();
    expect(screen.getByText('Property: prop-123')).toBeInTheDocument();
    expect(screen.getByText('Risk: 3')).toBeInTheDocument();
    expect(screen.getByText(/"tenant_name": "Alice Smith"/)).toBeInTheDocument();
  });
});
