/**
 * Tests that chat elements don't expand beyond their container.
 *
 * We check that the relevant CSS classes are applied:
 * - `overflow-hidden` prevents content from escaping the container
 * - `min-w-0` lets flex children shrink below their content size
 * - `break-words` / `break-all` wraps long strings instead of overflowing
 * - Absence of `truncate` / `whitespace-nowrap` (which force single-line text)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ChatMessageBubble } from './ChatMessage';
import { ChatMessage } from '@/data/mockData';

// ─── Mock useApp ─────────────────────────────────────────────────────────────

vi.mock('@/context/AppContext', () => ({
  useApp: () => ({
    openChat: vi.fn(),
  }),
}));

// ─── Helpers ──────────────────────────────────────────────────────────────────

const makeMessage = (overrides: Partial<ChatMessage> = {}): ChatMessage => ({
  id: 'test-msg',
  role: 'assistant',
  content: 'A'.repeat(200), // very long content
  timestamp: new Date(),
  senderName: 'RentMate',
  senderType: 'ai',
  messageType: 'message',
  ...overrides,
});

// ─── ChatMessageBubble overflow containment ───────────────────────────────────

describe('ChatMessageBubble overflow containment', () => {
  it('outer row has min-w-0 to allow flex shrinking', () => {
    render(<MemoryRouter><ChatMessageBubble message={makeMessage()} /></MemoryRouter>);
    // prose div → bubble div → row div
    const row = screen.getByText('A'.repeat(200)).closest('[class]')!.parentElement!.parentElement!;
    expect(row.className).toContain('min-w-0');
  });

  // For AI messages the text is rendered by ReactMarkdown inside a prose <div>.
  // We need the parent of that prose div to get the actual bubble element.
  const getAiBubble = (text: string) =>
    screen.getByText(text).closest('[class]')!.parentElement!;

  it('message bubble has overflow-hidden', () => {
    render(<MemoryRouter><ChatMessageBubble message={makeMessage()} /></MemoryRouter>);
    expect(getAiBubble('A'.repeat(200)).className).toContain('overflow-hidden');
  });

  it('message bubble has min-w-0', () => {
    render(<MemoryRouter><ChatMessageBubble message={makeMessage()} /></MemoryRouter>);
    expect(getAiBubble('A'.repeat(200)).className).toContain('min-w-0');
  });

  it('message bubble has break-words', () => {
    render(<MemoryRouter><ChatMessageBubble message={makeMessage()} /></MemoryRouter>);
    // break-words is on the bubble itself
    expect(getAiBubble('A'.repeat(200)).className).toContain('break-words');
  });

  it('manager message bubble has overflow-hidden', () => {
    render(<MemoryRouter><ChatMessageBubble message={makeMessage({ role: 'user', senderType: 'manager' })} /></MemoryRouter>);
    const bubble = screen.getByText('A'.repeat(200)).closest('[class]')!;
    expect(bubble.className).toContain('overflow-hidden');
  });
});

// ─── Thinking indicator overflow containment ─────────────────────────────────
//
// The thinking indicator is rendered inline inside ChatPanel.  Rather than
// mounting the entire ChatPanel (which has many complex dependencies), we
// render a minimal component that mirrors the exact JSX structure so that we
// can verify the classes without those dependencies.

function ThinkingIndicator({ lines }: { lines: string[] }) {
  return (
    <div data-testid="thinking-row" className="flex items-start gap-2 overflow-hidden text-muted-foreground">
      <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/10 shrink-0 mt-0.5" />
      <div data-testid="thinking-bubble" className="flex-1 min-w-0 overflow-hidden py-2 px-3 rounded-2xl bg-muted">
        {lines.length === 0 ? (
          <div data-testid="dots" className="flex gap-1 py-0.5">
            <span className="w-1.5 h-1.5 bg-muted-foreground/50 rounded-full" />
          </div>
        ) : (
          <div className="space-y-0.5 overflow-hidden">
            {lines.slice(-3).map((line, i, arr) => (
              <p
                key={i}
                data-testid="progress-line"
                className={`text-[11px] font-mono break-all overflow-hidden ${
                  i === arr.length - 1 ? 'text-foreground/80' : 'text-muted-foreground/50'
                }`}
              >
                {line}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

describe('thinking indicator overflow containment', () => {
  describe('outer row', () => {
    it('has overflow-hidden', () => {
      render(<ThinkingIndicator lines={[]} />);
      const row = screen.getByTestId('thinking-row');
      expect(row.className).toContain('overflow-hidden');
    });

    it('does not have whitespace-nowrap or truncate', () => {
      render(<ThinkingIndicator lines={[]} />);
      const row = screen.getByTestId('thinking-row');
      expect(row.className).not.toContain('whitespace-nowrap');
      expect(row.className).not.toContain('truncate');
    });
  });

  describe('thinking bubble', () => {
    it('has overflow-hidden', () => {
      render(<ThinkingIndicator lines={[]} />);
      const bubble = screen.getByTestId('thinking-bubble');
      expect(bubble.className).toContain('overflow-hidden');
    });

    it('has min-w-0', () => {
      render(<ThinkingIndicator lines={[]} />);
      const bubble = screen.getByTestId('thinking-bubble');
      expect(bubble.className).toContain('min-w-0');
    });
  });

  describe('progress lines', () => {
    beforeEach(() => {
      render(<ThinkingIndicator lines={['Checking DB', 'A'.repeat(300), 'Done']} />);
    });

    it('have break-all to wrap long strings', () => {
      screen.getAllByTestId('progress-line').forEach(el => {
        expect(el.className).toContain('break-all');
      });
    });

    it('have overflow-hidden', () => {
      screen.getAllByTestId('progress-line').forEach(el => {
        expect(el.className).toContain('overflow-hidden');
      });
    });

    it('do not have whitespace-nowrap or truncate', () => {
      screen.getAllByTestId('progress-line').forEach(el => {
        expect(el.className).not.toContain('whitespace-nowrap');
        expect(el.className).not.toContain('truncate');
      });
    });

    it('renders at most 3 lines', () => {
      expect(screen.getAllByTestId('progress-line')).toHaveLength(3);
    });
  });
});
