import type { NavigateFunction } from 'react-router-dom';

import type { Suggestion } from '@/data/mockData';

type OpenChat = (opts?: {
  suggestionId?: string | null;
  taskId?: string | null;
  pageContext?: string | null;
  conversationId?: string | null;
  lazy?: boolean;
}) => void;

export function openSuggestionInContext(
  suggestion: Suggestion,
  navigate: NavigateFunction,
  openChat: OpenChat,
) {
  if (suggestion.taskId) {
    openChat({ taskId: suggestion.taskId, suggestionId: suggestion.id });
    navigate(`/tasks/${suggestion.taskId}?suggestion=${encodeURIComponent(suggestion.id)}`);
    return;
  }

  if (suggestion.targetConversationId) {
    openChat({ conversationId: suggestion.targetConversationId, suggestionId: suggestion.id });
    navigate(`/chats?suggestion=${encodeURIComponent(suggestion.id)}`);
    return;
  }

  openChat({ suggestionId: suggestion.id });
  navigate(`/chats?suggestion=${encodeURIComponent(suggestion.id)}`);
}
