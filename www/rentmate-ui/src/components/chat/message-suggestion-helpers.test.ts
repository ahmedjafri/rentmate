import { describe, expect, it } from 'vitest';

import { getMessageSuggestionSendAction, isMessageSuggestion } from './messageSuggestion';

describe('message suggestion helpers', () => {
  it('recognizes editable message suggestions', () => {
    const suggestion = {
      status: 'pending',
      draftMessage: 'hello',
      options: [
        { key: 'send', label: 'Send', action: 'message_person_send', variant: 'default' },
        { key: 'edit', label: 'Edit', action: 'edit_message', variant: 'outline' },
      ],
    };

    expect(isMessageSuggestion(suggestion)).toBe(true);
    expect(getMessageSuggestionSendAction(suggestion)).toBe('message_person_send');
  });

  it('rejects non-message suggestion flows', () => {
    const suggestion = {
      status: 'pending',
      draftMessage: 'upload it',
      options: [
        { key: 'upload', label: 'Upload', action: 'request_file_upload', variant: 'default' },
        { key: 'dismiss', label: 'Dismiss', action: 'reject_task', variant: 'ghost' },
      ],
    };

    expect(isMessageSuggestion(suggestion)).toBe(false);
    expect(getMessageSuggestionSendAction(suggestion)).toBe(null);
  });
});
