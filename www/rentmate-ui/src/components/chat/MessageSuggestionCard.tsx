import { useMemo, useState } from 'react';
import { Loader2, MessageCircle, Pencil, Send, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Suggestion } from '@/data/mockData';

export interface MessageSuggestionCardProps {
  suggestion: Suggestion;
  sendActionLabel?: string;
  onAccept: (action: string) => Promise<void>;
  onSendEdited: (body: string) => Promise<void>;
  onDismiss: () => Promise<void>;
  disabled?: boolean;
}

export function MessageSuggestionCard({
  suggestion,
  sendActionLabel = 'Send',
  onAccept,
  onSendEdited,
  onDismiss,
  disabled = false,
}: MessageSuggestionCardProps) {
  const draftText = suggestion.draftMessage ?? '';
  const [editing, setEditing] = useState(false);
  const [editedDraft, setEditedDraft] = useState(draftText);
  const [loading, setLoading] = useState<'accept' | 'edit' | 'dismiss' | null>(null);

  const canSendEdited = useMemo(() => editedDraft.trim().length > 0, [editedDraft]);

  const handleAccept = async () => {
    setLoading('accept');
    try {
      const sendAction = suggestion.options?.find((option) =>
        option.action === 'message_person_send' || option.action === 'send_and_create_task',
      )?.action;
      if (!sendAction) return;
      await onAccept(sendAction);
    } finally {
      setLoading(null);
    }
  };

  const handleSendEdited = async () => {
    if (!canSendEdited) return;
    setLoading('edit');
    try {
      await onSendEdited(editedDraft.trim());
      setEditing(false);
    } finally {
      setLoading(null);
    }
  };

  const handleDismiss = async () => {
    setLoading('dismiss');
    try {
      await onDismiss();
    } finally {
      setLoading(null);
    }
  };

  return (
    <Card className="mx-3 mb-3 rounded-xl border bg-muted/30 p-3 shadow-sm">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-primary">
        <MessageCircle className="h-3.5 w-3.5" />
        Suggested message
      </div>

      <p className="mb-2 text-xs font-medium text-foreground">{suggestion.title}</p>

      {editing ? (
        <div className="space-y-2">
          <Textarea
            value={editedDraft}
            onChange={(e) => setEditedDraft(e.target.value)}
            className="min-h-[96px] resize-none bg-background text-sm"
            autoFocus
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              className="h-8 rounded-lg text-xs"
              onClick={handleSendEdited}
              disabled={disabled || loading !== null || !canSendEdited}
            >
              {loading === 'edit' ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1 h-3.5 w-3.5" />}
              Send
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-8 rounded-lg text-xs"
              onClick={() => {
                setEditing(false);
                setEditedDraft(draftText);
              }}
              disabled={disabled || loading !== null}
            >
              <X className="mr-1 h-3.5 w-3.5" />
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="rounded-lg bg-background px-3 py-2 text-sm text-foreground">
            {draftText}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              className="h-8 rounded-lg text-xs"
              onClick={handleAccept}
              disabled={disabled || loading !== null}
            >
              {loading === 'accept' ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Send className="mr-1 h-3.5 w-3.5" />}
              {sendActionLabel}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 rounded-lg text-xs"
              onClick={() => setEditing(true)}
              disabled={disabled || loading !== null}
            >
              <Pencil className="mr-1 h-3.5 w-3.5" />
              Edit
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-8 rounded-lg text-xs"
              onClick={handleDismiss}
              disabled={disabled || loading !== null}
            >
              {loading === 'dismiss' ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <X className="mr-1 h-3.5 w-3.5" />}
              Dismiss
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}
