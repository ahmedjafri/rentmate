import { useState, useRef, useEffect, forwardRef, useImperativeHandle, useCallback, Dispatch, SetStateAction } from 'react';
import { Send, Paperclip, Loader2, X, FileText, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';

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
  lastSentMessage?: string;
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

const ACCEPTED_EXTENSIONS = ['.pdf', '.doc', '.docx', '.txt', '.csv', '.xls', '.xlsx', '.jpg', '.jpeg', '.png'];

function isAcceptedFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some(ext => name.endsWith(ext));
}

export const ChatInput = forwardRef<ChatInputHandle, Props>(({ onSend, disabled, placeholder = 'Type a message...', lastSentMessage, onInsertCleared, attachments = [], setAttachments, uploadFile }, ref) => {
  const [input, setInput] = useState('');
  const [insertedMessageId, setInsertedMessageId] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);

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

  const processFiles = useCallback((files: File[]) => {
    if (!uploadFile || !setAttachments) return;

    const accepted = files.filter(isAcceptedFile);
    const rejected = files.length - accepted.length;
    if (rejected > 0) {
      toast.error(`${rejected} file${rejected > 1 ? 's' : ''} not supported. Accepted: PDF, DOC, TXT, CSV, XLS, JPG, PNG`);
    }

    for (const file of accepted) {
      const localId = `att-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const newAtt: PendingAttachment = {
        localId,
        documentId: null,
        filename: file.name,
        status: 'uploading',
      };
      setAttachments(prev => [...prev, newAtt]);

      uploadFile(file).then(result => {
        setAttachments(prev => prev.map(a =>
          a.localId === localId
            ? { ...a, documentId: result?.id ?? null, status: result ? 'ready' as const : 'error' as const }
            : a
        ));
      });
    }
  }, [uploadFile, setAttachments]);

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

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;
    const fileList = Array.from(files);
    e.target.value = '';
    processFiles(fileList);
    setTimeout(() => textareaRef.current?.focus(), 100);
  };

  const removeAttachment = (localId: string) => {
    setAttachments?.(prev => prev.filter(a => a.localId !== localId));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (
      e.key === 'ArrowUp' &&
      !e.shiftKey &&
      !e.metaKey &&
      !e.ctrlKey &&
      !e.altKey &&
      !input &&
      lastSentMessage
    ) {
      e.preventDefault();
      setInput(lastSentMessage);
      setInsertedMessageId(null);
      requestAnimationFrame(() => {
        const el = textareaRef.current;
        if (!el) return;
        const end = lastSentMessage.length;
        el.focus();
        el.setSelectionRange(end, end);
      });
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer.types.includes('Files')) {
      setIsDragging(true);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setIsDragging(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragging(false);

    if (!uploadFile || !setAttachments) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      processFiles(files);
    }
  };

  const canSend = !disabled && !anyUploading && (input.trim() || attachments.some(a => a.status === 'ready'));

  return (
    <div
      className="border-t bg-card/50 shrink-0 relative"
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Drag overlay */}
      {isDragging && uploadFile && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-primary/5 border-2 border-dashed border-primary rounded-lg pointer-events-none">
          <div className="flex items-center gap-2 text-primary font-medium text-sm">
            <Upload className="h-4 w-4" />
            Drop files here
          </div>
        </div>
      )}
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
