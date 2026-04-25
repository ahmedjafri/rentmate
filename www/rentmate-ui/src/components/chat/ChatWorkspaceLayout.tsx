import type { ReactNode } from 'react';

import { ChatPanel } from './ChatPanel';

/**
 * Shared 3-column "chat workspace" shell used by the dashboard and the
 * Chats page.
 *
 * - Left rail: caller-provided (typically a `ConversationListPane`).
 * - Middle: an embedded `ChatPanel` driven by the active chatPanel state.
 * - Right rail: optional. Omit for a 2-column layout where the chat fills
 *   the remaining width.
 */
export function ChatWorkspaceLayout({
  leftRail,
  rightRail,
}: {
  leftRail: ReactNode;
  rightRail?: ReactNode;
}) {
  return (
    <div className="flex flex-col md:flex-row h-full">
      <div className="w-72 min-w-[280px] shrink-0 border-r hidden md:flex flex-col h-full">
        {leftRail}
      </div>
      <div className="flex-[2] min-w-0 flex flex-col h-full">
        <ChatPanel embedded />
      </div>
      {rightRail && (
        <div className="w-96 min-w-[360px] shrink-0 overflow-auto hidden lg:block border-l">
          {rightRail}
        </div>
      )}
    </div>
  );
}
