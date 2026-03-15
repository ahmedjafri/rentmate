import { Button } from '@/components/ui/button';
import { SuggestionCategory } from '@/data/mockData';

const filters: { label: string; value: SuggestionCategory | 'all' }[] = [
  { label: 'All', value: 'all' },
  { label: 'Rent & Payments', value: 'rent' },
  { label: 'Maintenance', value: 'maintenance' },
  { label: 'Leasing', value: 'leasing' },
  { label: 'Compliance', value: 'compliance' },
];

interface Props {
  activeFilter: SuggestionCategory | 'all';
  onFilterChange: (filter: SuggestionCategory | 'all') => void;
  counts: Record<string, number>;
}

export function SuggestionFilters({ activeFilter, onFilterChange, counts }: Props) {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {filters.map((f) => (
        <Button
          key={f.value}
          variant={activeFilter === f.value ? 'default' : 'outline'}
          size="sm"
          onClick={() => onFilterChange(f.value)}
          className="rounded-lg text-xs gap-1.5"
        >
          {f.label}
          {counts[f.value] !== undefined && (
            <span className="ml-1 bg-background/20 rounded-full px-1.5 py-0.5 text-[10px] font-bold">
              {counts[f.value]}
            </span>
          )}
        </Button>
      ))}
    </div>
  );
}
