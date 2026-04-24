import { useState } from 'react';
import { Plus, X, Zap, Wrench, FileText, ShieldCheck, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { createTask, sendMessage } from '@/graphql/client';
import { useApp } from '@/context/AppContext';
import { ActionDeskTask } from '@/data/mockData';
import { toast } from 'sonner';

export interface AgentProposedTask {
  _proposalId: string;
  title: string;
  category: string;
  urgency: string;
  description?: string;
  propertyId?: string;
}

const categoryIcons: Record<string, React.ElementType> = {
  maintenance: Wrench,
  lease: FileText,
  leasing: FileText,
  compliance: ShieldCheck,
};

const urgencyColors: Record<string, string> = {
  critical: 'bg-destructive/15 text-destructive border-destructive/20',
  high:     'bg-warning/15 text-warning-foreground border-warning/20',
  medium:   'bg-primary/10 text-primary border-primary/15',
  low:      'bg-muted text-muted-foreground border-muted-foreground/20',
};

interface Props {
  proposal: AgentProposedTask;
  onDismiss: (id: string) => void;
}

export function AgentTaskProposal({ proposal, onDismiss }: Props) {
  const { addTask, openChat } = useApp();
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<string | null>(null);

  const CategoryIcon = categoryIcons[proposal.category] ?? Zap;

  const handleCreate = async () => {
    setCreating(true);
    try {
      const result = await createTask({
        title: proposal.title,
        goal: proposal.description?.trim() || `Complete: ${proposal.title}`,
        source: 'agent',
        taskStatus: 'active',
        taskMode: 'manual',
        category: proposal.category,
        urgency: proposal.urgency,
        propertyId: proposal.propertyId ?? null,
        confidential: false,
      });
      const t = result.createTask;

      // Seed the task thread with context if the agent provided a description
      const contextBody = proposal.description?.trim();
      if (contextBody && t.aiConversationId) {
        await sendMessage({
          conversationId: t.aiConversationId,
          body: contextBody,
          messageType: 'context',
          senderName: 'RentMate',
          isAi: true,
        });
      }

      const newTask: ActionDeskTask = {
        id: String(t.uid),
        title: t.title ?? proposal.title,
        mode: (t.taskMode as ActionDeskTask['mode']) ?? 'manual',
        status: 'active',
        participants: [{ type: 'agent', name: 'RentMate AI' }],
        lastMessage: contextBody ?? '',
        lastMessageBy: 'RentMate',
        lastMessageAt: new Date(),
        unreadCount: 0,
        propertyId: t.propertyId ?? undefined,
        category: (t.category as ActionDeskTask['category']) ?? 'maintenance',
        urgency: (t.urgency as ActionDeskTask['urgency']) ?? 'medium',
        chatThread: contextBody ? [{
          id: `ctx-${t.uid}`,
          role: 'assistant' as const,
          content: contextBody,
          timestamp: new Date(),
          senderName: 'RentMate',
          senderType: 'ai' as const,
          messageType: 'context' as const,
        }] : [],
        confidential: false,
      };
      addTask(newTask);
      setCreated(t.uid);
      toast.success('Task created', { description: proposal.title });
    } catch (e) {
      toast.error('Failed to create task');
      console.error(e);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={cn(
      'rounded-xl border p-3 space-y-2',
      created ? 'border-accent/30 bg-accent/5' : 'border-primary/15 bg-primary/5',
    )}>
      <div className="flex items-start gap-2">
        <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 shrink-0 mt-0.5">
          <CategoryIcon className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap mb-0.5">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-primary">
              Proposed Task
            </span>
            <Badge
              variant="outline"
              className={cn('text-[9px] rounded-md px-1.5 py-0 h-4 border', urgencyColors[proposal.urgency] ?? urgencyColors.medium)}
            >
              {proposal.urgency}
            </Badge>
          </div>
          <p className="text-sm font-medium leading-snug">{proposal.title}</p>
          {proposal.description && (
            <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{proposal.description}</p>
          )}
        </div>
      </div>

      {!created ? (
        <div className="flex items-center gap-2 pt-0.5">
          <Button
            size="sm"
            className="h-7 rounded-lg gap-1.5 text-xs"
            onClick={handleCreate}
            disabled={creating}
          >
            {creating ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
            Create Task
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 rounded-lg text-xs text-muted-foreground hover:text-foreground"
            onClick={() => onDismiss(proposal._proposalId)}
            disabled={creating}
          >
            <X className="h-3 w-3 mr-1" />
            Dismiss
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2 pt-0.5">
          <span className="text-xs text-accent font-medium">Task created</span>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 rounded-lg text-[11px] px-2 text-primary hover:bg-primary/10"
            onClick={() => openChat({ taskId: created })}
          >
            Open →
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 rounded-lg text-[11px] px-2 text-muted-foreground ml-auto"
            onClick={() => onDismiss(proposal._proposalId)}
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      )}
    </div>
  );
}
