import { useState, useRef, useEffect, forwardRef, useImperativeHandle, useCallback, Dispatch, SetStateAction } from 'react';
import { Send, Paperclip, Loader2, X, FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

export interface PendingAttachment {
  localId: string;
  documentId: string | null;  // null while uploading
  filename: string;
  status: 'uploading' | 'ready' | 'error';
}

interface Props {
  onSend: (message: string, attachments?: PendingAttachment[], insertedFromMessageId?: string) => void;
  disabled?: boolean;
  placeholder?: string;
  onInsertCleared?: (messageId: string) => void;
  /** Controlled attachment list — managed by parent to survive re-renders. */
  attachments?: PendingAttachment[];
  setAttachments?: Dispatch<SetStateAction<PendingAttachment[]>>;
  /** Upload a file and return { id, filename } or null on failure. */
  uploadFile?: (file: File) => Promise<{ id: string; filename: string } | null>;
}

export interface ChatInputHandle {
  insertText: (text: string, fromMessageId?: string) => void;
  triggerFileUpload: () => void;
}

export const ChatInput = forwardRef<ChatInputHandle, Props>(({ onSend, disabled, placeholder = 'Type a message...', onInsertCleared, attachments = [], setAttachments, uploadFile }, ref) => {
  const [input, setInput] = useState('');
  const [insertedMessageId, setInsertedMessageId] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const anyUploading = attachments.some(a => a.status === 'uploading');

  useImperativeHandle(ref, () => ({
    insertText: (text: string, fromMessageId?: string) => {
      setInput(text);
      setInsertedMessageId(fromMessageId ?? null);
      setTimeout(() => textareaRef.current?.focus(), 50);
    },
    triggerFileUpload: () => {
      fileInputRef.current?.click();
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
    const readyAttachments = attachments.filter(a => a.status === 'ready');
    if (!trimmed && readyAttachments.length === 0) return;
    if (anyUploading) return;
    onSend(trimmed, readyAttachments.length > 0 ? readyAttachments : undefined, insertedMessageId ?? undefined);
    setInput('');
    setInsertedMessageId(null);
    setAttachments?.([]);
  };

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    setInput(newValue);
    if (insertedMessageId && newValue.trim() === '') {
      onInsertCleared?.(insertedMessageId);
      setInsertedMessageId(null);
    }
  }, [insertedMessageId, onInsertCleared]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0 || !uploadFile || !setAttachments) return;
    // Copy the FileList BEFORE clearing the input — clearing resets the live FileList
    const fileList = Array.from(files);
    e.target.value = '';

    for (const file of fileList) {
      const localId = `att-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const newAtt: PendingAttachment = {
        localId,
        documentId: null,
        filename: file.name,
        status: 'uploading',
      };
      // Add chip immediately
      setAttachments(prev => [...prev, newAtt]);

      // Upload in background — uses functional setState to avoid stale closures
      uploadFile(file).then(result => {
        setAttachments(prev => prev.map(a =>
          a.localId === localId
            ? { ...a, documentId: result?.id ?? null, status: result ? 'ready' as const : 'error' as const }
            : a
        ));
      });
    }

    setTimeout(() => textareaRef.current?.focus(), 100);
  };

  const removeAttachment = (localId: string) => {
    setAttachments?.(prev => prev.filter(a => a.localId !== localId));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const canSend = !disabled && !anyUploading && (input.trim() || attachments.some(a => a.status === 'ready'));

  return (
    <div className="border-t bg-card/50 shrink-0">
      {/* Attachment chips */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pt-2">
          {attachments.map(att => (
            <div
              key={att.localId}
              className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs ${
                att.status === 'error'
                  ? 'bg-destructive/10 text-destructive'
                  : att.status === 'uploading'
                    ? 'bg-muted/80 text-muted-foreground'
                    : 'bg-primary/10 text-foreground border border-primary/20'
              }`}
            >
              {att.status === 'uploading' ? (
                <Loader2 className="h-3 w-3 animate-spin shrink-0" />
              ) : att.status === 'ready' ? (
                <FileText className="h-3 w-3 shrink-0 text-primary" />
              ) : (
                <X className="h-3 w-3 shrink-0" />
              )}
              <span className="truncate max-w-[180px]">{att.filename}</span>
              {att.status === 'uploading' && (
                <span className="text-[10px] text-muted-foreground">uploading…</span>
              )}
              <button
                onClick={() => removeAttachment(att.localId)}
                className="ml-0.5 p-0.5 rounded hover:bg-foreground/10 transition-colors"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}
      {/* Input row */}
      <div className="flex items-end gap-2 p-3">
        {uploadFile && (
          <>
            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleFileChange} accept=".pdf,.doc,.docx,.txt,.csv,.xls,.xlsx,.jpg,.jpeg,.png" />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              className="rounded-xl shrink-0 h-10 w-10 text-muted-foreground hover:text-foreground"
            >
              <Paperclip className="h-4 w-4" />
            </Button>
          </>
        )}
        <Textarea
          ref={textareaRef}
          value={input}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={attachments.length > 0 ? 'Add a message (optional)...' : placeholder}
          disabled={disabled}
          className="min-h-[40px] max-h-[200px] resize-none rounded-xl border-border/60 text-sm overflow-y-auto"
          rows={1}
        />
        <Button
          size="icon"
          onClick={handleSend}
          disabled={!canSend}
          className="rounded-xl shrink-0 h-10 w-10"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
});

ChatInput.displayName = 'ChatInput';
