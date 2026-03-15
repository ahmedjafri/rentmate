import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Wrench, Sparkles, Clock } from 'lucide-react';
import { format } from 'date-fns';

const priorityStyles: Record<string, string> = {
  emergency: 'bg-destructive/15 text-destructive',
  urgent: 'bg-warning/15 text-warning-foreground',
  routine: 'bg-primary/10 text-primary',
  low: 'bg-muted text-muted-foreground',
};

const statusStyles: Record<string, string> = {
  open: 'bg-warning/15 text-warning-foreground',
  in_progress: 'bg-primary/10 text-primary',
  resolved: 'bg-accent/15 text-accent',
  closed: 'bg-muted text-muted-foreground',
};

const Maintenance = () => {
  const { tickets, properties } = useApp();

  const sorted = [...tickets].sort((a, b) => {
    const order = { emergency: 0, urgent: 1, routine: 2, low: 3 };
    return (order[a.priority] ?? 3) - (order[b.priority] ?? 3);
  });

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Maintenance</h1>
        <p className="text-sm text-muted-foreground">
          {tickets.filter(t => t.status === 'open').length} open · {tickets.filter(t => t.status === 'in_progress').length} in progress
        </p>
      </div>

      <div className="space-y-3">
        {sorted.map((ticket) => {
          const property = properties.find(p => p.id === ticket.propertyId);
          return (
            <Card key={ticket.id} className="p-5 rounded-xl">
              <div className="flex items-start justify-between gap-3 mb-2">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" className={`text-xs rounded-lg ${priorityStyles[ticket.priority]}`}>
                    {ticket.priority}
                  </Badge>
                  <Badge variant="secondary" className={`text-xs rounded-lg ${statusStyles[ticket.status]}`}>
                    {ticket.status.replace('_', ' ')}
                  </Badge>
                </div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  {format(new Date(ticket.createdAt), 'MMM d')}
                </div>
              </div>

              <h3 className="font-semibold text-sm mb-1">{ticket.description}</h3>
              <p className="text-xs text-muted-foreground">
                {ticket.tenantName} · Unit {ticket.unit}{property ? ` · ${property.name}` : ''}
              </p>

              {ticket.aiTriageSuggestion && (
                <div className="mt-3 bg-muted/50 rounded-lg p-3 flex items-start gap-2">
                  <Sparkles className="h-3.5 w-3.5 text-primary mt-0.5 shrink-0" />
                  <p className="text-xs">{ticket.aiTriageSuggestion}</p>
                </div>
              )}

              {ticket.vendorAssigned && (
                <div className="mt-2 flex items-center gap-1.5">
                  <Wrench className="h-3 w-3 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">Vendor: <span className="font-medium text-foreground">{ticket.vendorAssigned}</span></span>
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
};

export default Maintenance;
