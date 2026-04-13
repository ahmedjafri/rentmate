import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChatInput } from './ChatInput';


describe('ChatInput history recall', () => {
  it('fills the last sent message when ArrowUp is pressed on an empty input', () => {
    render(
      <ChatInput
        onSend={vi.fn()}
        lastSentMessage="Please send the 14-day notice"
      />,
    );

    const textarea = screen.getByPlaceholderText('Type a message...') as HTMLTextAreaElement;
    fireEvent.keyDown(textarea, { key: 'ArrowUp' });

    expect(textarea.value).toBe('Please send the 14-day notice');
  });

  it('does not override existing text when ArrowUp is pressed', () => {
    render(
      <ChatInput
        onSend={vi.fn()}
        lastSentMessage="Please send the 14-day notice"
      />,
    );

    const textarea = screen.getByPlaceholderText('Type a message...') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'new draft' } });
    fireEvent.keyDown(textarea, { key: 'ArrowUp' });

    expect(textarea.value).toBe('new draft');
  });
});
