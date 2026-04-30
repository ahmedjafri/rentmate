import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';

import { ChatWorkspaceLayout } from './ChatWorkspaceLayout';

// ChatPanel is heavy + irrelevant to layout tests — stub it.
vi.mock('./ChatPanel', () => ({
  ChatPanel: () => <div data-testid="embedded-chat-panel">embedded</div>,
}));

// useApp returns whatever the chatPanel mock currently is.
let chatPanelState = {
  isOpen: false,
  conversationId: null as string | null,
  taskId: null as string | null,
  suggestionId: null as string | null,
};
vi.mock('@/context/AppContext', () => ({
  useApp: () => ({
    chatPanel: chatPanelState,
  }),
}));

beforeEach(() => {
  chatPanelState = { isOpen: false, conversationId: null, taskId: null, suggestionId: null };
});

function renderLayout(opts: {
  withRightRail?: boolean;
  mobileDefaultPane?: 'left' | 'middle' | 'right';
} = {}) {
  return render(
    <ChatWorkspaceLayout
      leftRail={<div data-testid="left-rail-content">left rail</div>}
      rightRail={opts.withRightRail
        ? <div data-testid="right-rail-content">right rail</div>
        : undefined}
      mobileDefaultPane={opts.mobileDefaultPane}
    />,
  );
}

function paneVisibilityClasses() {
  // The pane wrappers are siblings of the tab bar inside the workspace
  // root. Reach through testid for the embedded children to land on the
  // right wrapper element.
  const left = screen.queryByTestId('left-rail-content')?.parentElement;
  const middle = screen.getByTestId('embedded-chat-panel').parentElement!;
  const right = screen.queryByTestId('right-rail-content')?.parentElement ?? null;
  return { left, middle, right };
}

describe('ChatWorkspaceLayout — mobile tab navigation', () => {
  it('renders a mobile tab bar with one entry per available pane', () => {
    renderLayout({ withRightRail: true });

    // Three tabs when a right rail is provided, in the same logical order
    // as the desktop panes.
    expect(screen.getByTestId('workspace-tab-right')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-tab-left')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-tab-middle')).toBeInTheDocument();
    expect(screen.getAllByRole('button').map(button => button.textContent)).toEqual([
      'Chat List',
      'Chat',
      'Action Desk',
    ]);
  });

  it('omits the right-rail tab when no right rail is supplied', () => {
    renderLayout({ withRightRail: false });

    expect(screen.queryByTestId('workspace-tab-right')).not.toBeInTheDocument();
    expect(screen.getByTestId('workspace-tab-left')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-tab-middle')).toBeInTheDocument();
  });

  it('marks the configured mobileDefaultPane tab as the active one on first paint', () => {
    renderLayout({ withRightRail: true, mobileDefaultPane: 'right' });

    expect(screen.getByTestId('workspace-tab-right')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('workspace-tab-left')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('workspace-tab-middle')).toHaveAttribute('aria-pressed', 'false');
  });

  it('makes the active pane visible on mobile and hides the inactive ones', () => {
    renderLayout({ withRightRail: true, mobileDefaultPane: 'right' });

    const { left, middle, right } = paneVisibilityClasses();

    // Inactive mobile panes get the bare `hidden` utility so they
    // collapse on small screens. The active pane uses `flex flex-1`
    // (or `block flex-1` for the right rail).
    // Match `hidden` only when it stands alone — `md:hidden` /
    // `lg:hidden` are separate breakpoint-scoped utilities.
    const bareHidden = /(?:^|\s)hidden(?:\s|$)/;
    expect(bareHidden.test(left?.className ?? '')).toBe(true);
    expect(bareHidden.test(middle.className)).toBe(true);
    expect(bareHidden.test(right?.className ?? '')).toBe(false);
    expect(right?.className).toMatch(/\bblock\b/);
  });

  it('switches the active pane when the user taps a tab', () => {
    renderLayout({ withRightRail: true, mobileDefaultPane: 'right' });

    act(() => screen.getByTestId('workspace-tab-left').click());

    expect(screen.getByTestId('workspace-tab-left')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('workspace-tab-right')).toHaveAttribute('aria-pressed', 'false');

    const { left, middle, right } = paneVisibilityClasses();
    expect(left?.className).toMatch(/\bflex flex-1\b/);
    expect(middle.className).toContain('hidden');
    expect(right?.className).toContain('hidden');
  });

  it('auto-switches to the middle (chat) pane when chatPanel.conversationId is set', () => {
    chatPanelState = { ...chatPanelState, conversationId: 'conv-42' };
    renderLayout({ withRightRail: true, mobileDefaultPane: 'right' });

    // The effect runs on mount because the dependency is non-null.
    expect(screen.getByTestId('workspace-tab-middle')).toHaveAttribute('aria-pressed', 'true');
  });

  it('auto-switches to the middle pane when chatPanel.taskId is set', () => {
    chatPanelState = { ...chatPanelState, taskId: 'task-7' };
    renderLayout({ withRightRail: true, mobileDefaultPane: 'right' });

    expect(screen.getByTestId('workspace-tab-middle')).toHaveAttribute('aria-pressed', 'true');
  });

  it('auto-switches to the middle pane when chatPanel.suggestionId is set', () => {
    chatPanelState = { ...chatPanelState, suggestionId: 's1' };
    renderLayout({ withRightRail: true, mobileDefaultPane: 'right' });

    expect(screen.getByTestId('workspace-tab-middle')).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('ChatWorkspaceLayout — desktop layout invariants', () => {
  it('always tags every pane with the corresponding desktop visibility classes so md+ shows side-by-side regardless of mobile pane state', () => {
    renderLayout({ withRightRail: true, mobileDefaultPane: 'right' });

    const { left, middle, right } = paneVisibilityClasses();

    // Even though `left` is mobile-hidden, it carries the `md:flex`
    // class so it appears at the md breakpoint.
    expect(left?.className).toMatch(/\bmd:flex\b/);
    // Middle is always shown at md+.
    expect(middle.className).toMatch(/\bmd:flex\b/);
    // The right rail uses lg:block — md width is too narrow for the
    // dashboard's Action Desk so it stays hidden between md and lg.
    expect(right?.className).toMatch(/\blg:block\b/);
  });
});
