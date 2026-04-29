import { useCallback, useMemo, useState } from 'react';

/**
 * Identifies WHICH chat thread (task / suggestion / conversation) a chat
 * stream belongs to. The combination uniquely scopes a stream — switching
 * any one of these means the user moved to a different chat.
 */
export type StreamScope = {
  taskId: string | null;
  suggestionId: string | null;
  conversationId: string | null;
};

export type StreamingState = {
  /** Raw "a stream is in flight somewhere" flag. */
  isTyping: boolean;
  /** Raw progress log lines from the in-flight stream. */
  progressLog: string[];
  /** Append-only log setter (used by SSE handlers). */
  setProgressLog: React.Dispatch<React.SetStateAction<string[]>>;
  /**
   * Whether the in-flight stream belongs to the chat the user is
   * currently looking at. Render code that surfaces the thinking row /
   * progress lines should gate on this so streams started in one
   * conversation don't leak their reasoning trace into another.
   */
  visibleIsTyping: boolean;
  /** Same gating applied to the progress lines themselves. */
  visibleProgressLog: string[];
  /** Mark a new stream as in flight, scoped to the current chat. */
  beginStream: () => void;
  /** Clear all streaming state. */
  endStream: () => void;
};

/**
 * Track an in-flight chat stream + the scope it belongs to so the UI
 * doesn't bleed reasoning traces across chats.
 *
 * The pre-fix bug: the chat panel kept a global `isTyping` + `progressLog`
 * that updated whenever any stream ran. If a stream started in task A and
 * the user switched to task B mid-flight, the thinking row + recent
 * progress lines from A appeared inside B's chat — looking like B was
 * doing the work.
 */
export function useStreamingState(currentScope: StreamScope): StreamingState {
  const [streamScope, setStreamScope] = useState<StreamScope | null>(null);
  const [isTyping, setIsTyping] = useState(false);
  const [progressLog, setProgressLog] = useState<string[]>([]);

  const beginStream = useCallback(() => {
    setStreamScope({
      taskId: currentScope.taskId,
      suggestionId: currentScope.suggestionId,
      conversationId: currentScope.conversationId,
    });
    setIsTyping(true);
    setProgressLog([]);
  }, [currentScope.taskId, currentScope.suggestionId, currentScope.conversationId]);

  const endStream = useCallback(() => {
    setStreamScope(null);
    setIsTyping(false);
    setProgressLog([]);
  }, []);

  const isInCurrentScope = useMemo(() => {
    if (streamScope === null) return false;
    return streamScope.taskId === currentScope.taskId
      && streamScope.suggestionId === currentScope.suggestionId
      && streamScope.conversationId === currentScope.conversationId;
  }, [streamScope, currentScope.taskId, currentScope.suggestionId, currentScope.conversationId]);

  return {
    isTyping,
    progressLog,
    setProgressLog,
    visibleIsTyping: isTyping && isInCurrentScope,
    visibleProgressLog: isInCurrentScope ? progressLog : [],
    beginStream,
    endStream,
  };
}
