import { Check, X, MessageCircle, Sparkles, ChevronRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useApp } from '@/context/AppContext';
import { Suggestion, categoryLabels, categoryColors, urgencyColors, autonomyLabels } from '@/data/mockData';

interface Props {
  suggestion: Suggestion;
}

export function SuggestionCard({ suggestion }: Props) {
  const { updateSuggestionStatus, openChat } = useApp();

  const handleApprove = () => updateSuggestionStatus(suggestion.id, 'approved');
  const handleDismiss = () => updateSuggestionStatus(suggestion.id, 'dismissed');
  const handleDiscuss = () => openChat({ suggestionId: suggestion.id });

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, x: -20, height: 0 }}
      transition={{ duration: 0.25 }}
    >
      <Card className="p-5 rounded-xl border border-border/60 shadow-sm hover:shadow-md transition-shadow bg-card">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Badge variant="secondary" className={`text-xs font-medium rounded-lg px-2.5 py-0.5 ${categoryColors[suggestion.category]}`}>
              {categoryLabels[suggestion.category]}
            </Badge>
            <Badge variant="secondary" className={`text-xs rounded-lg px-2.5 py-0.5 ${urgencyColors[suggestion.urgency]}`}>
              {suggestion.urgency}
            </Badge>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground shrink-0">
            <Sparkles className="h-3 w-3" />
            <span>{Math.round(suggestion.confidence * 100)}% confident</span>
          </div>
        </div>

        {/* Title */}
        <h3 className="font-semibold text-base mb-2">{suggestion.title}</h3>

        {/* Description */}
        <p className="text-sm text-muted-foreground mb-3 leading-relaxed">{suggestion.description}</p>

        {/* Recommended Action */}
        <div className="bg-muted/50 rounded-lg p-3 mb-4">
          <div className="flex items-center gap-1.5 mb-1">
            <ChevronRight className="h-3.5 w-3.5 text-primary" />
            <span className="text-xs font-medium text-primary">Recommended Action</span>
          </div>
          <p className="text-sm">{suggestion.recommendedAction}</p>
        </div>

        {/* Autonomy indicator */}
        <div className="flex items-center justify-between mb-4">
          <span className="text-xs text-muted-foreground">
            Autonomy: <span className="font-medium text-foreground">{autonomyLabels[suggestion.autonomyLevel]}</span>
          </span>
          {suggestion.chatThread.length > 0 && (
            <span className="text-xs text-muted-foreground">
              {suggestion.chatThread.length} messages in thread
            </span>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5">
          <Button size="sm" onClick={handleApprove} className="rounded-lg gap-1.5 bg-accent hover:bg-accent/90 text-accent-foreground h-8 px-2 sm:px-3">
            <Check className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Approve</span>
          </Button>
          <Button size="sm" variant="outline" onClick={handleDismiss} className="rounded-lg gap-1.5 h-8 px-2 sm:px-3 hover:bg-destructive/10 hover:text-destructive hover:border-destructive/30">
            <X className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Dismiss</span>
          </Button>
          <Button size="sm" variant="ghost" onClick={handleDiscuss} className="rounded-lg gap-1.5 ml-auto text-primary hover:text-primary hover:bg-primary/5 h-8 px-2 sm:px-3">
            <MessageCircle className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Discuss</span>
          </Button>
        </div>
      </Card>
    </motion.div>
  );
}
