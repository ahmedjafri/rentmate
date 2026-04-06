import { Check, Circle, CircleDot, ListChecks } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface TaskStep {
  key: string;
  label: string;
  status: 'pending' | 'active' | 'done';
  note?: string;
}

export function ProgressSteps({ steps }: { steps?: TaskStep[] }) {
  if (!steps || steps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <ListChecks className="h-8 w-8 mb-2 opacity-40" />
        <p className="text-sm font-medium">No steps yet</p>
        <p className="text-xs mt-1 text-center px-6">
          RentMate will propose a plan once it has enough context about this task.
        </p>
      </div>
    );
  }

  return (
    <div className="px-4 py-3 space-y-0">
      {steps.map((step, i) => {
        const isLast = i === steps.length - 1;
        return (
          <div key={step.key} className="flex gap-3 relative">
            {/* Vertical line connecting steps */}
            {!isLast && (
              <div
                className={cn(
                  'absolute left-[11px] top-6 w-px bottom-0',
                  step.status === 'done' ? 'bg-primary/40' : 'bg-border',
                )}
              />
            )}
            {/* Step icon */}
            <div className="shrink-0 mt-0.5 z-10">
              {step.status === 'done' ? (
                <div className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-primary">
                  <Check className="h-3 w-3 text-primary-foreground" />
                </div>
              ) : step.status === 'active' ? (
                <div className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-primary/15">
                  <CircleDot className="h-4 w-4 text-primary" />
                </div>
              ) : (
                <div className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-muted">
                  <Circle className="h-3.5 w-3.5 text-muted-foreground/50" />
                </div>
              )}
            </div>
            {/* Step content */}
            <div className={cn('pb-4 min-w-0', isLast && 'pb-1')}>
              <p
                className={cn(
                  'text-sm leading-snug',
                  step.status === 'done' && 'text-muted-foreground line-through',
                  step.status === 'active' && 'font-medium text-foreground',
                  step.status === 'pending' && 'text-muted-foreground',
                )}
              >
                {step.label}
              </p>
              {step.note && (
                <p className="text-[11px] text-muted-foreground mt-0.5">{step.note}</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
