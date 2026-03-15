import { useState, useRef, useEffect, forwardRef, useImperativeHandle, useCallback } from 'react';
import { Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

interface Props {
  onSend: (message: string, insertedFromMessageId?: string) => void;
  disabled?: boolean;
  placeholder?: string;
  onInsertCleared?: (messageId: string) => void;
}

export interface ChatInputHandle {
  insertText: (text: string, fromMessageId?: string) => void;
}

export const ChatInput = forwardRef<ChatInputHandle, Props>(({ onSend, disabled, placeholder = 'Type a message...', onInsertCleared }, ref) => {
  const [input, setInput] = useState('');
  const [insertedMessageId, setInsertedMessageId] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(ref, () => ({
    insertText: (text: string, fromMessageId?: string) => {
      setInput(text);
      setInsertedMessageId(fromMessageId ?? null);
      // Focus the textarea after inserting
      setTimeout(() => textareaRef.current?.focus(), 50);
    },
  }));

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    }
  }, [input]);

  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    onSend(trimmed, insertedMessageId ?? undefined);
    setInput('');
    setInsertedMessageId(null);
  };

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    setInput(newValue);
    // If user clears the inserted text completely, notify parent
    if (insertedMessageId && newValue.trim() === '') {
      onInsertCleared?.(insertedMessageId);
      setInsertedMessageId(null);
    }
  }, [insertedMessageId, onInsertCleared]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex items-end gap-2 p-3 border-t bg-card/50">
      <Textarea
        ref={textareaRef}
        value={input}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        className="min-h-[40px] max-h-[200px] resize-none rounded-xl border-border/60 text-sm overflow-y-auto"
        rows={1}
      />
      <Button
        size="icon"
        onClick={handleSend}
        disabled={disabled || !input.trim()}
        className="rounded-xl shrink-0 h-10 w-10"
      >
        <Send className="h-4 w-4" />
      </Button>
    </div>
  );
});

ChatInput.displayName = 'ChatInput';
