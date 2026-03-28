import { useState } from 'react';
import { CheckCircle2, X, Loader2, XCircle, Zap, ShieldCheck, Hand } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { graphqlQuery, UPDATE_TASK_MUTATION } from '@/data/api';
import { useApp } from '@/context/AppContext';
import { toast } from 'sonner';

export type AgentProposedAction =
  | { _proposalId: string; action: 'close_task'; taskId: string }
  | { _proposalId: string; action: 'set_mode'; taskId: string; mode: 'autonomous' | 'manual' | 'waiting_approval' };

const modeLabels = {
  autonomous: 'Autonomous',
  manual: 'Manual',
  waiting_approval: 'Needs Approval',
} as const;

const modeIcons = {
  autonomous: Zap,
  manual: Hand,
  waiting_approval: ShieldCheck,
} as const;

interface Props {
  proposal: AgentProposedAction;
  onDismiss: (id: string) => void;
}

export function AgentActionConfirm({ proposal, onDismiss }: Props) {
  const { updateTask } = useApp();
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState(false);

  const handleConfirm = async () => {
    setConfirming(true);
    try {
      if (proposal.action === 'close_task') {
        await graphqlQuery(UPDATE_TASK_MUTATION, {
          input: { uid: proposal.taskId, taskStatus: 'resolved', taskMode: null },
        });
        updateTask(proposal.taskId, { status: 'resolved' });
        toast.success('Task closed');
      } else if (proposal.action === 'set_mode') {
        await graphqlQuery(UPDATE_TASK_MUTATION, {
          input: { uid: proposal.taskId, taskMode: proposal.mode, taskStatus: null },
        });
        updateTask(proposal.taskId, { mode: proposal.mode });
        toast.success(`Task mode set to ${modeLabels[proposal.mode]}`);
      }
      setDone(true);
    } catch {
      toast.error('Action failed — please try again');
    } finally {
      setConfirming(false);
    }
  };

  const label = proposal.action === 'close_task'
    ? 'Close task'
    : (() => {
        const ModeIcon = modeIcons[proposal.mode];
        return (
          <span className="flex items-center gap-1">
            Switch to <ModeIcon className="h-3 w-3 inline" /> {modeLabels[proposal.mode]} mode
          </span>
        );
      })();

  return (
    <div className={cn(
      'rounded-xl border p-3 space-y-2',
      done ? 'border-accent/30 bg-accent/5' : 'border-amber-500/20 bg-amber-500/5',
    )}>
      <div className="flex items-start gap-2">
        <div className="flex h-6 w-6 items-center justify-center rounded-full bg-amber-500/10 shrink-0 mt-0.5">
          <ShieldCheck className="h-3.5 w-3.5 text-amber-500" />
        </div>
        <div className="flex-1 min-w-0">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400">
            Needs Confirmation
          </span>
          <p className="text-sm font-medium leading-snug mt-0.5">{label}</p>
        </div>
      </div>

      {!done ? (
        <div className="flex items-center gap-2 pt-0.5">
          <Button
            size="sm"
            className="h-7 rounded-lg gap-1.5 text-xs"
            onClick={handleConfirm}
            disabled={confirming}
          >
            {confirming ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
            Confirm
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 rounded-lg text-xs text-muted-foreground hover:text-foreground"
            onClick={() => onDismiss(proposal._proposalId)}
            disabled={confirming}
          >
            <X className="h-3 w-3 mr-1" />
            Dismiss
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2 pt-0.5">
          <span className="text-xs text-accent font-medium flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3" /> Done
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 rounded-lg text-[11px] px-2 text-muted-foreground ml-auto"
            onClick={() => onDismiss(proposal._proposalId)}
          >
            <XCircle className="h-3 w-3" />
          </Button>
        </div>
      )}
    </div>
  );
}
