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
    step_count: 2,
    trace_count: 0,
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
    // Legacy run — no AgentSteps yet, only AgentTrace rows. The
    // server's legacy adapter still synthesizes ATIF steps on read,
    // so the UI uses the same per-step rendering for both.
    step_count: 0,
    trace_count: 5,
  },
];

const TRAJECTORY_NEWER = {
  schema_version: 'ATIF-v1.4',
  session_id: 'run-newer',
  agent: {
    name: 'rentmate-test',
    version: null,
    model_name: 'anthropic/claude-sonnet-4-6',
  },
  steps: [
    {
      step_id: 1,
      timestamp: '2026-04-25T12:00:00.050Z',
      source: 'user',
      message: 'review the task',
    },
    {
      step_id: 2,
      timestamp: '2026-04-25T12:00:00.100Z',
      source: 'agent',
      message: 'Looking up vendors then booking the plumber.',
      model_name: 'anthropic/claude-sonnet-4-6',
      tool_calls: [
        {
          tool_call_id: 'call_lookup_1',
          function_name: 'lookup_vendors',
          arguments: { vendor_type: 'plumber' },
        },
      ],
      observation: {
        results: [
          { source_call_id: 'call_lookup_1', content: 'ERROR: vendor api 500' },
        ],
      },
      metrics: {
        prompt_tokens: 100,
        completion_tokens: 50,
        cost_usd: 0.00045,
      },
      extra: { error_kind: 'tool_error' },
    },
  ],
  final_metrics: {
    total_prompt_tokens: 100,
    total_completion_tokens: 50,
    total_cost_usd: 0.00045,
    total_steps: 2,
  },
  extra: { rentmate_status: 'errored' },
};

function buildResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('DevTools RunsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authFetchMock.mockImplementation(async (input: string) => {
      if (input.startsWith('/dev/runs/run-newer/trajectory')) return buildResponse(TRAJECTORY_NEWER);
      if (input.startsWith('/dev/runs?')) return buildResponse(RUNS);
      if (input.startsWith('/dev/runs/')) return buildResponse({ schema_version: 'ATIF-v1.4', session_id: 'x', agent: {}, steps: [], final_metrics: { total_prompt_tokens: 0, total_completion_tokens: 0, total_cost_usd: 0, total_steps: 0 } });
      if (input.startsWith('/dev/trace-filters/tasks')) return buildResponse([]);
      if (input.startsWith('/dev/trace-filters/chats')) return buildResponse([]);
      if (input.startsWith('/dev/memory-items')) return buildResponse([]);
      if (input.startsWith('/dev/traces')) return buildResponse([]);
      return buildResponse({});
    });
  });

  it('renders runs newest-first with status, source, totals, and step/trace counts', async () => {
    render(
      <MemoryRouter>
        <DevTools />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText('Agent Runs')).toBeInTheDocument());

    await waitFor(() => {
      expect(screen.getByLabelText('Toggle run run-newer')).toBeInTheDocument();
      expect(screen.getByLabelText('Toggle run run-older')).toBeInTheDocument();
    });

    const newerRow = screen.getByLabelText('Toggle run run-newer');
    expect(within(newerRow).getByText('errored')).toBeInTheDocument();
    expect(within(newerRow).getByText('task_review')).toBeInTheDocument();
    expect(within(newerRow).getByText('100→50')).toBeInTheDocument();
    // Post-cutover run — shows step count (not trace count).
    expect(within(newerRow).getByText('2 steps')).toBeInTheDocument();
    expect(within(newerRow).getByText('plumber tool blew up')).toBeInTheDocument();

    // Pre-cutover run with no AgentSteps — falls back to trace count.
    const olderRow = screen.getByLabelText('Toggle run run-older');
    expect(within(olderRow).getByText('completed')).toBeInTheDocument();
    expect(within(olderRow).getByText('5 traces')).toBeInTheDocument();
  });

  it('lazy-fetches the ATIF trajectory on expand and renders steps inline', async () => {
    render(
      <MemoryRouter>
        <DevTools />
      </MemoryRouter>,
    );

    const toggle = await screen.findByLabelText('Toggle run run-newer');
    expect(authFetchMock.mock.calls.some(call => String(call[0]).startsWith('/dev/runs/run-newer/trajectory'))).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(authFetchMock.mock.calls.some(call => String(call[0]).startsWith('/dev/runs/run-newer/trajectory'))).toBe(true);
    });

    // The expanded view shows a row per ATIF step with source badges.
    await waitFor(() => {
      expect(screen.getByText('user')).toBeInTheDocument();
      expect(screen.getByText('agent')).toBeInTheDocument();
      // Errored agent step is flagged inline.
      expect(screen.getByText('error')).toBeInTheDocument();
      // Tool count badge.
      expect(screen.getByText('1 tool')).toBeInTheDocument();
    });
  });
});
