import { Slider } from '@/components/ui/slider';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ActionPolicyLevel, actionPolicyLabels } from '@/data/mockData';
import { Lock } from 'lucide-react';

const allLevels: ActionPolicyLevel[] = ['strict', 'balanced', 'aggressive'];

interface Props {
  label: string;
  value: ActionPolicyLevel;
  onChange: (level: ActionPolicyLevel) => void;
  maxLevel?: ActionPolicyLevel;
  maxLevelReason?: string;
}

export function AutonomySlider({ label, value, onChange, maxLevel, maxLevelReason }: Props) {
  const maxIndex = maxLevel ? allLevels.indexOf(maxLevel) : allLevels.length - 1;
  const currentIndex = allLevels.indexOf(value);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs text-primary font-medium">{actionPolicyLabels[value]}</span>
      </div>
      <Slider
        value={[currentIndex]}
        min={0}
        max={maxIndex}
        step={1}
        onValueChange={([v]) => onChange(allLevels[v])}
        className="w-full"
      />
      <div className="flex justify-between">
        {allLevels.map((l, i) => {
          const disabled = i > maxIndex;
          const label = (
            <span
              key={l}
              className={`text-[10px] ${disabled ? 'text-muted-foreground/40' : 'text-muted-foreground'} ${disabled ? 'flex items-center gap-0.5' : ''}`}
            >
              {actionPolicyLabels[l]}
              {disabled && <Lock className="h-2.5 w-2.5 inline-block" />}
            </span>
          );

          if (disabled && maxLevelReason) {
            return (
              <Tooltip key={l}>
                <TooltipTrigger asChild>
                  {label}
                </TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-[200px] text-xs">
                  {maxLevelReason}
                </TooltipContent>
              </Tooltip>
            );
          }

          return label;
        })}
      </div>
    </div>
  );
}
