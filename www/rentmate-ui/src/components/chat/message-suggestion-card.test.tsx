import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MessageSuggestionCard } from './MessageSuggestionCard';
import { Suggestion } from '@/data/mockData';

function buildSuggestion(overrides: Partial<Suggestion> = {}): Suggestion {
  return {
    id: '12',
    title: 'Reply to Bob',
    category: 'compliance',
    urgency: 'high',
    status: 'pending',
    createdAt: new Date('2026-04-13T00:00:00Z'),
    chatThread: [],
    draftMessage: 'Hello Bob, please confirm receipt.',
    options: [
      { key: 'send', label: 'Send to Bob', action: 'message_person_send', variant: 'default' },
      { key: 'edit', label: 'Edit Message', action: 'edit_message', variant: 'outline' },
      { key: 'reject', label: 'Dismiss', action: 'reject_task', variant: 'ghost' },
    ],
    ...overrides,
  };
}

describe('MessageSuggestionCard', () => {
  it('accepts, edits, and dismisses message suggestions', async () => {
    const onAccept = vi.fn().mockResolvedValue(undefined);
    const onSendEdited = vi.fn().mockResolvedValue(undefined);
    const onDismiss = vi.fn().mockResolvedValue(undefined);

    render(
      <MessageSuggestionCard
        suggestion={buildSuggestion()}
        sendActionLabel="Send to Bob"
        onAccept={onAccept}
        onSendEdited={onSendEdited}
        onDismiss={onDismiss}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /send to bob/i }));
    await waitFor(() => expect(onAccept).toHaveBeenCalledWith('message_person_send'));

    fireEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    const editor = screen.getByRole('textbox');
    fireEvent.change(editor, { target: { value: 'Updated draft' } });
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }));
    await waitFor(() => expect(onSendEdited).toHaveBeenCalledWith('Updated draft'));

    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    await waitFor(() => expect(onDismiss).toHaveBeenCalled());
  });
});
