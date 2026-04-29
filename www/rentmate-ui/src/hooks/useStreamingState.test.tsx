import { describe, it, expect } from 'vitest';
import { act, render, renderHook, screen } from '@testing-library/react';
import { useState } from 'react';

import { useStreamingState, type StreamScope } from './useStreamingState';

const SCOPE_A: StreamScope = { taskId: 'task-A', suggestionId: null, conversationId: null };
const SCOPE_B: StreamScope = { taskId: 'task-B', suggestionId: null, conversationId: null };
const CONV_X: StreamScope = { taskId: null, suggestionId: null, conversationId: 'conv-X' };
const CONV_Y: StreamScope = { taskId: null, suggestionId: null, conversationId: 'conv-Y' };

describe('useStreamingState — visibility scoping', () => {
  it('reports the stream as not visible before any beginStream call', () => {
    const { result } = renderHook(() => useStreamingState(SCOPE_A));
    expect(result.current.isTyping).toBe(false);
    expect(result.current.visibleIsTyping).toBe(false);
    expect(result.current.visibleProgressLog).toEqual([]);
  });

  it('shows the stream in the chat where it began', () => {
    const { result } = renderHook(() => useStreamingState(SCOPE_A));

    act(() => result.current.beginStream());

    expect(result.current.isTyping).toBe(true);
    expect(result.current.visibleIsTyping).toBe(true);

    act(() => result.current.setProgressLog((prev) => [...prev, 'Reading task context']));
    expect(result.current.visibleProgressLog).toEqual(['Reading task context']);
  });

  it('hides the stream when the user navigates to a different task', () => {
    let scope: StreamScope = SCOPE_A;
    const { result, rerender } = renderHook(() => useStreamingState(scope));

    act(() => result.current.beginStream());
    act(() => result.current.setProgressLog(() => ['Calling tool…']));

    expect(result.current.visibleIsTyping).toBe(true);
    expect(result.current.visibleProgressLog).toEqual(['Calling tool…']);

    // User clicks into a different task. Same panel, different scope.
    scope = SCOPE_B;
    rerender();

    // The underlying stream is still in flight, but it must not bleed
    // into task B's view.
    expect(result.current.isTyping).toBe(true);
    expect(result.current.visibleIsTyping).toBe(false);
    expect(result.current.visibleProgressLog).toEqual([]);
  });

  it('hides the stream when the user navigates between separate conversations', () => {
    let scope: StreamScope = CONV_X;
    const { result, rerender } = renderHook(() => useStreamingState(scope));

    act(() => result.current.beginStream());
    act(() => result.current.setProgressLog(() => ['Drafting reply']));

    scope = CONV_Y;
    rerender();

    expect(result.current.visibleIsTyping).toBe(false);
    expect(result.current.visibleProgressLog).toEqual([]);
  });

  it('restores the stream view when the user comes back to the originating chat', () => {
    let scope: StreamScope = SCOPE_A;
    const { result, rerender } = renderHook(() => useStreamingState(scope));

    act(() => result.current.beginStream());
    act(() => result.current.setProgressLog(() => ['Step 1']));

    scope = SCOPE_B;
    rerender();
    expect(result.current.visibleIsTyping).toBe(false);

    scope = SCOPE_A;
    rerender();
    expect(result.current.visibleIsTyping).toBe(true);
    expect(result.current.visibleProgressLog).toEqual(['Step 1']);
  });

  it('clears all streaming state on endStream regardless of current scope', () => {
    const { result } = renderHook(() => useStreamingState(SCOPE_A));

    act(() => result.current.beginStream());
    act(() => result.current.setProgressLog(() => ['mid-stream']));
    expect(result.current.visibleIsTyping).toBe(true);

    act(() => result.current.endStream());
    expect(result.current.isTyping).toBe(false);
    expect(result.current.visibleIsTyping).toBe(false);
    expect(result.current.progressLog).toEqual([]);
    expect(result.current.visibleProgressLog).toEqual([]);
  });
});

// --- DOM-level UX validation ---------------------------------------------
//
// Mirrors the chat-panel JSX gating: render the thinking row + progress
// trace under `visibleIsTyping` / `visibleProgressLog`. This catches
// regressions where someone wires the render path back to raw `isTyping`
// (which is exactly the leak this hook exists to prevent).
function StreamingThinkingHarness({
  initialScope,
}: {
  initialScope: StreamScope;
}) {
  const [scope, setScope] = useState<StreamScope>(initialScope);
  const stream = useStreamingState(scope);

  return (
    <div>
      {stream.visibleIsTyping && (
        <div data-testid="thinking-row">
          {stream.visibleProgressLog.length === 0
            ? <span data-testid="thinking-dots">…</span>
            : (
              <ul data-testid="progress-lines">
                {stream.visibleProgressLog.map((line, idx) => (
                  <li key={idx}>{line}</li>
                ))}
              </ul>
            )}
        </div>
      )}
      <button onClick={() => stream.beginStream()}>Begin</button>
      <button onClick={() => stream.setProgressLog((prev) => [...prev, 'Calling tool…'])}>
        Push log
      </button>
      <button onClick={() => stream.endStream()}>End</button>
      <button onClick={() => setScope(SCOPE_A)}>Go A</button>
      <button onClick={() => setScope(SCOPE_B)}>Go B</button>
    </div>
  );
}

describe('Streaming thinking row — UX rendering', () => {
  it('does not bleed the reasoning trace from one chat into another', () => {
    render(<StreamingThinkingHarness initialScope={SCOPE_A} />);

    // Start a stream in chat A and push a progress line.
    act(() => screen.getByText('Begin').click());
    act(() => screen.getByText('Push log').click());

    expect(screen.getByTestId('thinking-row')).toBeInTheDocument();
    expect(screen.getByText('Calling tool…')).toBeInTheDocument();

    // User navigates to chat B mid-stream.
    act(() => screen.getByText('Go B').click());

    // The reasoning trace must NOT follow them — it stays scoped to
    // chat A where the stream actually originated.
    expect(screen.queryByTestId('thinking-row')).not.toBeInTheDocument();
    expect(screen.queryByText('Calling tool…')).not.toBeInTheDocument();

    // Coming back to chat A surfaces the in-progress trace again.
    act(() => screen.getByText('Go A').click());
    expect(screen.getByTestId('thinking-row')).toBeInTheDocument();
    expect(screen.getByText('Calling tool…')).toBeInTheDocument();
  });

  it('clears the thinking row in every chat once endStream is called', () => {
    render(<StreamingThinkingHarness initialScope={SCOPE_A} />);
    act(() => screen.getByText('Begin').click());
    expect(screen.getByTestId('thinking-row')).toBeInTheDocument();

    act(() => screen.getByText('End').click());
    expect(screen.queryByTestId('thinking-row')).not.toBeInTheDocument();

    // And it stays cleared after navigating between chats.
    act(() => screen.getByText('Go B').click());
    expect(screen.queryByTestId('thinking-row')).not.toBeInTheDocument();
    act(() => screen.getByText('Go A').click());
    expect(screen.queryByTestId('thinking-row')).not.toBeInTheDocument();
  });
});
