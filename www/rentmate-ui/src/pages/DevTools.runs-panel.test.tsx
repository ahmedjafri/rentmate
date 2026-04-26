import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import DevTools from './DevTools';

const authFetchMock = vi.fn();

vi.mock('@/lib/auth', () => ({
  authFetch: (...args: unknown[]) => authFetchMock(...args),
  getToken: () => 'token',
  logout: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const RUNS = [
  {
    id: 'run-newer',
    source: 'task_review',
    status: 'errored',
    task_id: '42',
    conversation_id: null,
    model: 'anthropic/claude-sonnet-4-6',
    agent_version: 'rentmate-test',
    execution_path: 'local',
    started_at: '2026-04-25T12:00:00Z',
    ended_at: '2026-04-25T12:00:01Z',
    duration_ms: 1000,
    iteration_count: 2,
    total_input_tokens: 100,
    total_output_tokens: 50,
    total_cost_cents: '1.5000',
    trigger_input: 'review the task',
    final_response: null,
    error_message: 'plumber tool blew up',
    trace_count: 1,
  },
  {
    id: 'run-older',
    source: 'chat',
    status: 'completed',
    task_id: null,
    conversation_id: 'conv-7',
    model: 'anthropic/claude-haiku-4-5',
    agent_version: 'rentmate-test',
    execution_path: 'local',
    started_at: '2026-04-25T11:55:00Z',
    ended_at: '2026-04-25T11:55:02Z',
    duration_ms: 2000,
    iteration_count: 4,
    total_input_tokens: 200,
    total_output_tokens: 80,
    total_cost_cents: '0.3500',
    trigger_input: 'find me the lease',
    final_response: 'Here it is.',
    error_message: null,
    trace_count: 5,
  },
];

const TRACES_FOR_NEWER = [
  {
    id: 'trace-newer-0',
    timestamp: '2026-04-25T12:00:00.100Z',
    trace_type: 'tool_call',
    source: 'task_review',
    run_id: 'run-newer',
    sequence_num: 0,
    task_id: '42',
    conversation_id: null,
    tool_name: 'lookup_vendors',
    summary: 'Looking up vendors',
    detail: null,
    suggestion_id: null,
  },
];

function buildResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('DevTools RunsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authFetchMock.mockImplementation(async (input: string) => {
      if (input.startsWith('/dev/runs')) return buildResponse(RUNS);
      if (input.startsWith('/dev/trace-filters/tasks')) return buildResponse([]);
      if (input.startsWith('/dev/trace-filters/chats')) return buildResponse([]);
      if (input.startsWith('/dev/traces?run_id=run-newer')) return buildResponse(TRACES_FOR_NEWER);
      if (input.startsWith('/dev/memory-items')) return buildResponse([]);
      if (input.startsWith('/dev/traces')) return buildResponse([]);
      return buildResponse({});
    });
  });

  it('renders runs newest-first with status, source, totals and trace count', async () => {
    render(
      <MemoryRouter>
        <DevTools />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText('Agent Runs')).toBeInTheDocument());

    // Both runs render.
    await waitFor(() => {
      expect(screen.getByLabelText('Toggle run run-newer')).toBeInTheDocument();
      expect(screen.getByLabelText('Toggle run run-older')).toBeInTheDocument();
    });

    const newerRow = screen.getByLabelText('Toggle run run-newer');
    expect(within(newerRow).getByText('errored')).toBeInTheDocument();
    expect(within(newerRow).getByText('task_review')).toBeInTheDocument();
    expect(within(newerRow).getByText('100→50')).toBeInTheDocument();
    expect(within(newerRow).getByText('1 traces')).toBeInTheDocument();
    // Error message preview is shown on the row.
    expect(within(newerRow).getByText('plumber tool blew up')).toBeInTheDocument();

    const olderRow = screen.getByLabelText('Toggle run run-older');
    expect(within(olderRow).getByText('completed')).toBeInTheDocument();
    expect(within(olderRow).getByText('5 traces')).toBeInTheDocument();
    expect(within(olderRow).getByText('200→80')).toBeInTheDocument();
  });

  it('lazy-fetches traces for a run on expand', async () => {
    render(
      <MemoryRouter>
        <DevTools />
      </MemoryRouter>,
    );

    const toggle = await screen.findByLabelText('Toggle run run-newer');

    // Before expand: no per-run trace fetch was issued.
    expect(authFetchMock.mock.calls.some(call => String(call[0]).startsWith('/dev/traces?run_id=run-newer'))).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(authFetchMock.mock.calls.some(call => String(call[0]).startsWith('/dev/traces?run_id=run-newer'))).toBe(true);
    });

    // The trace shows up nested under the run row.
    await waitFor(() => {
      expect(screen.getByText('Looking up vendors')).toBeInTheDocument();
      expect(screen.getByText('lookup_vendors')).toBeInTheDocument();
    });
  });

  it('per-run copy button copies the run header + every trace, fetching them when needed', async () => {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    render(
      <MemoryRouter>
        <DevTools />
      </MemoryRouter>,
    );

    const copyButton = await screen.findByLabelText('Copy run run-newer');

    // Pre-condition: the run hasn't been expanded yet, so no traces fetched.
    expect(authFetchMock.mock.calls.some(call => String(call[0]).startsWith('/dev/traces?run_id=run-newer'))).toBe(false);

    fireEvent.click(copyButton);

    // The copy click should NOT toggle the row expanded state.
    const toggle = screen.getByLabelText('Toggle run run-newer');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    // It should fetch the run's traces (lazy load) and write to clipboard.
    await waitFor(() => {
      expect(authFetchMock.mock.calls.some(call => String(call[0]).startsWith('/dev/traces?run_id=run-newer'))).toBe(true);
    });
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));

    const copied = String(writeText.mock.calls[0][0]);
    expect(copied).toContain('Run run-newer');
    expect(copied).toContain('errored');
    expect(copied).toContain('plumber tool blew up');
    // Each trace appears as an indented line under the run header.
    expect(copied).toContain('Looking up vendors');
    expect(copied).toContain('tool_call');
  });
});
