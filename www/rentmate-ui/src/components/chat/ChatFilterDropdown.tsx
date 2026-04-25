import { Filter } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

import type { TabKey } from './ConvRow';

export type ChatFilter = 'all' | TabKey;

const FILTER_OPTIONS: { value: ChatFilter; label: string }[] = [
  { value: 'all',     label: 'All' },
  { value: 'user_ai', label: 'RentMate' },
  { value: 'tenant',  label: 'Tenants' },
  { value: 'vendor',  label: 'Vendors' },
];

const FILTER_LABELS: Record<ChatFilter, string> = Object.fromEntries(
  FILTER_OPTIONS.map((option) => [option.value, option.label]),
) as Record<ChatFilter, string>;

export function chatFilterLabel(value: ChatFilter): string {
  return FILTER_LABELS[value] ?? 'All';
}

export function ChatFilterDropdown({
  value,
  onChange,
}: {
  value: ChatFilter;
  onChange: (next: ChatFilter) => void;
}) {
  const isFiltered = value !== 'all';
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          title={`Filter chats: ${chatFilterLabel(value)}`}
        >
          <Filter className={isFiltered ? 'h-4 w-4 text-primary' : 'h-4 w-4'} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuLabel className="text-[10px] uppercase tracking-wide text-muted-foreground">
          Filter by
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => onChange(next as ChatFilter)}
        >
          {FILTER_OPTIONS.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value} className="text-xs">
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
