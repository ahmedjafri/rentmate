import { useState, useMemo } from 'react';
import { AnimatePresence } from 'framer-motion';
import { useApp } from '@/context/AppContext';
import { SuggestionCard } from './SuggestionCard';
import { SuggestionFilters } from './SuggestionFilters';
import { SuggestionCategory } from '@/data/mockData';
import { Inbox } from 'lucide-react';

export function SuggestionFeed() {
  const { suggestions } = useApp();
  const [activeFilter, setActiveFilter] = useState<SuggestionCategory | 'all'>('all');

  const pendingSuggestions = useMemo(() =>
    suggestions.filter(s => s.status === 'pending'),
    [suggestions]
  );

  const filtered = useMemo(() =>
    activeFilter === 'all'
      ? pendingSuggestions
      : pendingSuggestions.filter(s => s.category === activeFilter),
    [pendingSuggestions, activeFilter]
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: pendingSuggestions.length };
    pendingSuggestions.forEach(s => { c[s.category] = (c[s.category] || 0) + 1; });
    return c;
  }, [pendingSuggestions]);

  // Sort: critical > high > medium > low
  const urgencyOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  const sorted = useMemo(() =>
    [...filtered].sort((a, b) => urgencyOrder[a.urgency] - urgencyOrder[b.urgency]),
    [filtered]
  );

  return (
    <div className="space-y-5">
      <SuggestionFilters activeFilter={activeFilter} onFilterChange={setActiveFilter} counts={counts} />
      
      {sorted.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
          <Inbox className="h-12 w-12 mb-3 opacity-40" />
          <p className="font-medium">No pending suggestions</p>
          <p className="text-sm mt-1">All caught up! 🎉</p>
        </div>
      ) : (
        <div className="space-y-3">
          <AnimatePresence mode="popLayout">
            {sorted.map(s => (
              <SuggestionCard key={s.id} suggestion={s} />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
