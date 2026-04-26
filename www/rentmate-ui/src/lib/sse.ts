// Agent-stream SSE consumer with built-in reconnect.
//
// Every LLM-backed flow (chat reply, routine run, task review, doc
// analysis) registers itself with the backend ``StreamRegistry`` and
// emits events via the standard SSE envelope:
//
//   data: {"type": "stream_id", "stream_id": "..."}
//   data: {"type": "progress",  "text": "..."}
//   data: {"type": "done",       ...caller_payload}
//   data: {"type": "error",     "message": "..."}
//
// ``consumeAgentStream`` calls the caller-provided ``start()`` to fire
// the initiating request, records the first ``stream_id`` frame, and
// transparently reconnects to ``GET /api/agent-streams/{stream_id}`` if
// the connection drops before a terminal frame arrives.

import { authFetch } from '@/lib/auth';

export interface SSEEvent {
  type: string;
  [key: string]: unknown;
}

export interface ConsumeAgentStreamOptions<E extends SSEEvent = SSEEvent> {
  /** Fires the initiating request. Typically a POST that starts a new run. */
  start: () => Promise<Response>;
  onStreamId?: (streamId: string) => void;
  onProgress?: (text: string) => void;
  onDone?: (event: E) => void;
  onError?: (message: string) => void;
  /** Catch-all for bespoke event types callers want to handle directly. */
  onEvent?: (event: E) => void;
  /** Max reconnect attempts after the initial stream (default 3). */
  maxReconnects?: number;
  /** Delay between reconnect attempts in ms (default 500). */
  reconnectDelayMs?: number;
}

export interface AgentStreamHandle {
  readonly streamId: string | null;
  cancel: () => void;
}

/** Parse an SSE byte stream, invoking handlers until a terminal frame
 *  arrives or the body ends. Internal helper — not exported. */
async function pumpStream<E extends SSEEvent>(
  body: ReadableStream<Uint8Array>,
  onEventJson: (event: E) => 'continue' | 'stop',
  signal: AbortSignal,
): Promise<{ terminated: boolean }> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      if (signal.aborted) return { terminated: true };
      const { done, value } = await reader.read();
      if (done) return { terminated: false };
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6)) as E;
          if (onEventJson(event) === 'stop') return { terminated: true };
        } catch {
          // skip malformed frames; the stream is best-effort.
        }
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* already released */ }
  }
}

export function consumeAgentStream<E extends SSEEvent = SSEEvent>(
  opts: ConsumeAgentStreamOptions<E>,
): AgentStreamHandle {
  const controller = new AbortController();
  const handle: { streamId: string | null } = { streamId: null };
  const maxReconnects = opts.maxReconnects ?? 3;
  const reconnectDelayMs = opts.reconnectDelayMs ?? 500;

  const dispatch = (event: E): 'continue' | 'stop' => {
    if (event.type === 'stream_id' && typeof event.stream_id === 'string') {
      handle.streamId = event.stream_id;
      opts.onStreamId?.(event.stream_id);
      opts.onEvent?.(event);
      return 'continue';
    }
    if (event.type === 'progress' && typeof event.text === 'string') {
      opts.onProgress?.(event.text);
      opts.onEvent?.(event);
      return 'continue';
    }
    if (event.type === 'done') {
      opts.onDone?.(event);
      opts.onEvent?.(event);
      return 'stop';
    }
    if (event.type === 'error') {
      const msg = typeof event.message === 'string' ? event.message : 'Agent error';
      opts.onError?.(msg);
      opts.onEvent?.(event);
      return 'stop';
    }
    opts.onEvent?.(event);
    return 'continue';
  };

  (async () => {
    try {
      const initial = await opts.start();
      if (!initial.ok || !initial.body) {
        opts.onError?.(`HTTP ${initial.status}`);
        return;
      }
      let result = await pumpStream<E>(initial.body, dispatch, controller.signal);
      if (result.terminated || controller.signal.aborted) return;

      // Stream ended without a terminal frame — try to reconnect via
      // the generic subscribe endpoint. Safe because new subscribers
      // replay the buffered history.
      for (let attempt = 0; attempt < maxReconnects; attempt++) {
        if (controller.signal.aborted) return;
        if (!handle.streamId) return;  // nothing to reconnect to
        await new Promise(r => setTimeout(r, reconnectDelayMs));
        try {
          const res = await authFetch(`/api/agent-streams/${handle.streamId}`, {
            signal: controller.signal,
          });
          if (!res.ok || !res.body) continue;
          result = await pumpStream<E>(res.body, dispatch, controller.signal);
          if (result.terminated || controller.signal.aborted) return;
        } catch (err) {
          if ((err as Error).name === 'AbortError') return;
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        opts.onError?.(err instanceof Error ? err.message : String(err));
      }
    }
  })();

  return {
    get streamId() { return handle.streamId; },
    cancel: () => controller.abort(),
  };
}
