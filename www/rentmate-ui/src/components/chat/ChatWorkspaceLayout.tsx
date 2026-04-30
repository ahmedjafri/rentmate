import { useEffect, useState, type ReactNode } from 'react';
import { Bot, LayoutDashboard, MessageCircle } from 'lucide-react';

import { ChatPanel } from './ChatPanel';
import { useApp } from '@/context/AppContext';
import { cn } from '@/lib/utils';

type Pane = 'left' | 'middle' | 'right';

type PaneOption = {
  key: Pane;
  label: string;
  icon: typeof Bot;
};

const PANE_OPTIONS: Record<Pane, PaneOption> = {
  left: { key: 'left', label: 'Chats', icon: MessageCircle },
  middle: { key: 'middle', label: 'RentMate', icon: Bot },
  right: { key: 'right', label: 'Home', icon: LayoutDashboard },
};

/**
 * Shared "chat workspace" shell used by the dashboard and the Chats page.
 *
 * **Desktop (≥md)**: a fixed three-column layout — left rail, embedded
 * `ChatPanel`, optional right rail. The right rail only renders at lg+
 * (the dashboard's Action Desk needs the width).
 *
 * **Mobile (<md)**: only one pane fits at a time, so the panes are
 * stacked behind a top tab bar. The user lands on `mobileDefaultPane`
 * (typically the most useful screen for that page — the dashboard's
 * Home pane vs. the Chats page's conversation list) and can switch
 * between the available panes. Selecting a conversation/task auto-
 * switches to the chat pane so the tap-to-open flow doesn't dead-end.
 */
export function ChatWorkspaceLayout({
  leftRail,
  rightRail,
  mobileDefaultPane = 'left',
}: {
  leftRail: ReactNode;
  rightRail?: ReactNode;
  mobileDefaultPane?: Pane;
}) {
  const { chatPanel } = useApp();
  const [pane, setPane] = useState<Pane>(mobileDefaultPane);

  // Tap-through: when something opens a chat (conversation row, task
  // card, suggestion link, etc.), bring the chat pane into view on
  // mobile so the user actually sees the thread they just opened.
  useEffect(() => {
    if (chatPanel.conversationId || chatPanel.taskId || chatPanel.suggestionId) {
      setPane('middle');
    }
  }, [chatPanel.conversationId, chatPanel.taskId, chatPanel.suggestionId]);

  const tabs: PaneOption[] = rightRail
    ? [PANE_OPTIONS.right, PANE_OPTIONS.left, PANE_OPTIONS.middle]
    : [PANE_OPTIONS.left, PANE_OPTIONS.middle];

  return (
    <div className="flex flex-col h-full md:flex-row">
      {/* Mobile-only tab bar. Desktop hides it because all panes are
          rendered side-by-side. */}
      <div className="md:hidden flex shrink-0 border-b bg-card/40 backdrop-blur-sm">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const active = pane === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setPane(tab.key)}
              aria-pressed={active}
              data-testid={`workspace-tab-${tab.key}`}
              className={cn(
                'flex-1 flex items-center justify-center gap-1.5 py-2 text-xs font-medium transition-colors',
                active
                  ? 'border-b-2 border-primary text-foreground'
                  : 'border-b-2 border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Left rail (conversation list).
          Mobile: only when `pane === 'left'`. md+: always visible as a
          fixed-width sidebar (md:flex-none cancels the mobile flex-1). */}
      <div
        className={cn(
          'border-r min-h-0 flex-col',
          pane === 'left' ? 'flex flex-1' : 'hidden',
          'md:flex md:flex-none md:w-72 md:min-w-[280px] md:shrink-0 md:h-full',
        )}
      >
        {leftRail}
      </div>

      {/* Middle (embedded ChatPanel).
          Mobile: only when `pane === 'middle'`. md+: grows to fill (flex-[2]). */}
      <div
        className={cn(
          'min-w-0 min-h-0 flex-col',
          pane === 'middle' ? 'flex flex-1' : 'hidden',
          'md:flex md:flex-[2] md:h-full',
        )}
      >
        <ChatPanel embedded />
      </div>

      {/* Right rail (Action Desk on the dashboard, omitted elsewhere).
          Mobile: only when `pane === 'right'` (full-width).
          md→<lg: hidden — md is too narrow for the dashboard widgets.
          lg+: fixed-width sidebar. */}
      {rightRail && (
        <div
          className={cn(
            'overflow-auto min-h-0',
            pane === 'right' ? 'block flex-1' : 'hidden',
            'md:hidden lg:block lg:w-96 lg:min-w-[360px] lg:shrink-0 lg:flex-none lg:border-l lg:h-full',
          )}
        >
          {rightRail}
        </div>
      )}
    </div>
  );
}
