import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

interface PageLoaderProps {
  /** Number of skeleton rows to render. Defaults to 5. */
  count?: number;
  className?: string;
}

/**
 * Generic full-page loading state. Drop this in place of page content
 * while data is being fetched from the backend.
 */
export function PageLoader({ count = 5, className }: PageLoaderProps) {
  return (
    <div className={cn('p-6 space-y-3', className)}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-xl border bg-card p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <Skeleton className="h-4 w-2/5" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
          <Skeleton className={cn('h-3', i % 3 === 0 ? 'w-3/4' : i % 3 === 1 ? 'w-1/2' : 'w-2/3')} />
          <Skeleton className="h-3 w-1/4" />
        </div>
      ))}
    </div>
  );
}
