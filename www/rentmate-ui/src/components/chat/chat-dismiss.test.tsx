import { describe, expect, it, vi } from 'vitest';

import { performTaskDismiss } from './ChatPanel';


describe('performTaskDismiss', () => {
  it('dismisses the task, removes it locally, and closes the panel', async () => {
    const updateTaskStatusFn = vi.fn().mockResolvedValue(undefined);
    const sendMessageFn = vi.fn().mockResolvedValue(undefined);
    const addChatMessage = vi.fn();
    const updateTask = vi.fn();
    const removeTask = vi.fn();
    const closeChat = vi.fn();
    const refreshData = vi.fn();

    await performTaskDismiss({
      taskId: '42',
      aiConversationId: 'conv-7',
      updateTaskStatusFn,
      sendMessageFn,
      addChatMessage,
      updateTask,
      removeTask,
      closeChat,
      refreshData,
    });

    expect(updateTaskStatusFn).toHaveBeenCalledWith('42', 'dismissed');
    expect(sendMessageFn).toHaveBeenCalledWith(expect.objectContaining({
      conversationId: 'conv-7',
      messageType: 'internal',
      isAi: true,
    }));
    expect(addChatMessage).toHaveBeenCalledWith(
      { taskId: '42' },
      expect.objectContaining({
        role: 'assistant',
        messageType: 'internal',
      }),
    );
    expect(updateTask).toHaveBeenCalledWith('42', { status: 'cancelled' });
    expect(removeTask).toHaveBeenCalledWith('42');
    expect(closeChat).toHaveBeenCalled();
    expect(refreshData).toHaveBeenCalled();
  });
});
