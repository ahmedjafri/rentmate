import { Button } from '@/components/ui/button';
import { Paperclip, Building2, MessageCircle, ArrowRight, Settings } from 'lucide-react';

export type OnboardingChoice = 'upload' | 'manual' | 'prose' | 'skip' | 'configure_llm';

interface OnboardingChipsProps {
  onSelect: (choice: OnboardingChoice) => void;
  disabled?: boolean;
  /** When true, show only the "Configure AI" chip instead of the normal options. */
  llmNotConfigured?: boolean;
}

const defaultChips: { key: OnboardingChoice; label: string; icon: React.ElementType }[] = [
  { key: 'upload', label: 'Upload a lease or document', icon: Paperclip },
  { key: 'manual', label: 'Add a property manually', icon: Building2 },
  { key: 'prose', label: 'Tell me about your portfolio', icon: MessageCircle },
  { key: 'skip', label: "Skip \u2014 I'll explore on my own", icon: ArrowRight },
];

const llmChips: { key: OnboardingChoice; label: string; icon: React.ElementType }[] = [
  { key: 'configure_llm', label: 'Configure AI model', icon: Settings },
  { key: 'skip', label: "Skip \u2014 I'll explore on my own", icon: ArrowRight },
];

export function OnboardingChips({ onSelect, disabled, llmNotConfigured }: OnboardingChipsProps) {
  const chips = llmNotConfigured ? llmChips : defaultChips;
  return (
    <div className="flex flex-wrap gap-2 px-4 pb-3">
      {chips.map(chip => {
        const Icon = chip.icon;
        return (
          <Button
            key={chip.key}
            variant="outline"
            size="sm"
            className="h-auto min-h-[44px] py-2 px-3 text-xs rounded-xl gap-2 whitespace-normal text-left justify-start"
            disabled={disabled}
            onClick={() => onSelect(chip.key)}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {chip.label}
          </Button>
        );
      })}
    </div>
  );
}
