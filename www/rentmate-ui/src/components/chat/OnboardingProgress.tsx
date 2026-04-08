import { Check, Circle, X } from 'lucide-react';
import { cn } from '@/lib/utils';

interface OnboardingProgressProps {
  steps: {
    configure_llm: 'pending' | 'done';
    add_property: 'pending' | 'done';
    upload_document: 'pending' | 'done';
    tell_concerns: 'pending' | 'done';
  };
  onDismiss: () => void;
}

const stepLabels: { key: keyof OnboardingProgressProps['steps']; label: string }[] = [
  { key: 'configure_llm', label: 'Configure AI' },
  { key: 'add_property', label: 'Add a property' },
  { key: 'upload_document', label: 'Upload a document' },
  { key: 'tell_concerns', label: "What's on your mind" },
];

export function OnboardingProgress({ steps, onDismiss }: OnboardingProgressProps) {
  const allDone = Object.values(steps).every(v => v === 'done');
  if (allDone) return null;

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 border-b bg-muted/30 shrink-0">
      {stepLabels.map((step, i) => {
        const isDone = steps[step.key] === 'done';
        return (
          <div key={step.key} className="flex items-center gap-1.5">
            {i > 0 && <div className="w-4 h-px bg-border" />}
            {isDone ? (
              <div className="flex h-4 w-4 items-center justify-center rounded-full bg-primary shrink-0">
                <Check className="h-2.5 w-2.5 text-primary-foreground" />
              </div>
            ) : (
              <div className="flex h-4 w-4 items-center justify-center rounded-full bg-muted shrink-0">
                <Circle className="h-3 w-3 text-muted-foreground/50" />
              </div>
            )}
            <span
              className={cn(
                'text-[11px] whitespace-nowrap',
                isDone ? 'text-muted-foreground line-through' : 'text-foreground',
              )}
            >
              {step.label}
            </span>
          </div>
        );
      })}
      <button
        onClick={onDismiss}
        className="ml-auto p-1 rounded-md hover:bg-muted transition-colors shrink-0"
        title="Dismiss onboarding"
      >
        <X className="h-3 w-3 text-muted-foreground" />
      </button>
    </div>
  );
}
