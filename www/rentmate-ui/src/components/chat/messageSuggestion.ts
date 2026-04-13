import { Suggestion } from '@/data/mockData';

export function getMessageSuggestionSendAction(
  suggestion: Pick<Suggestion, 'options'> | null | undefined,
): string | null {
  const option = suggestion?.options?.find((item) =>
    item.action === 'message_person_send' || item.action === 'send_and_create_task',
  );
  return option?.action ?? null;
}

export function isMessageSuggestion(
  suggestion: Pick<Suggestion, 'status' | 'draftMessage' | 'options'> | null | undefined,
): boolean {
  if (!suggestion || suggestion.status !== 'pending') return false;
  if (!suggestion.draftMessage?.trim()) return false;
  if (suggestion.options?.some((item) => item.action === 'request_file_upload')) return false;
  if (!suggestion.options?.some((item) => item.action === 'edit_message')) return false;
  return getMessageSuggestionSendAction(suggestion) !== null;
}
