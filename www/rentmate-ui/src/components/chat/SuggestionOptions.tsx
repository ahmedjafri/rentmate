import { Button } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';
import { useState } from 'react';

export interface SuggestionOption {
  key: string;
  label: string;
  action: string;
  variant: string;
}

const DEFAULT_OPTIONS: SuggestionOption[] = [
  { key: 'accept', label: 'Accept', action: 'accept_task', variant: 'default' },
  { key: 'reject', label: 'Reject', action: 'reject_task', variant: 'ghost' },
];

interface SuggestionOptionsProps {
  options?: SuggestionOption[];
  onAction: (action: string) => Promise<void> | void;
}

export function SuggestionOptions({ options, onAction }: SuggestionOptionsProps) {
  const opts = options?.length ? options : DEFAULT_OPTIONS;
  const [loading, setLoading] = useState<string | null>(null);

  const handleClick = async (opt: SuggestionOption) => {
    setLoading(opt.key);
    try {
      await onAction(opt.action);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="flex items-center gap-2 p-3 border-t shrink-0 bg-muted/30">
      <span className="text-xs text-muted-foreground mr-auto">Choose an action:</span>
      {opts.map(opt => (
        <Button
          key={opt.key}
          size="sm"
          variant={opt.variant as 'default' | 'outline' | 'ghost'}
          className="h-7 text-xs rounded-lg"
          disabled={loading !== null}
          onClick={() => handleClick(opt)}
        >
          {loading === opt.key ? <Loader2 className="h-3 w-3 animate-spin" /> : opt.label}
        </Button>
      ))}
    </div>
  );
}
