import { useState, useEffect, useCallback } from 'react';
import { authFetch } from '@/lib/auth';

export interface OnboardingState {
  status: 'active' | 'completed' | 'dismissed';
  started_at: string;
  dismissed_at: string | null;
  path_picked: string | null;
  steps: {
    configure_llm: 'pending' | 'done';
    add_property: 'pending' | 'done';
    upload_document: 'pending' | 'done';
    tell_concerns: 'pending' | 'done';
  };
}

export function useOnboarding() {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [llmConfigured, setLlmConfigured] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchState = useCallback(async () => {
    try {
      const res = await authFetch('/onboarding/state');
      const data = await res.json();
      setState(data.onboarding ?? null);
      setLlmConfigured(data.llm_configured ?? false);
    } catch {
      setState(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchState(); }, [fetchState]);

  const dismiss = useCallback(async () => {
    try {
      const res = await authFetch('/onboarding/dismiss', { method: 'POST' });
      const data = await res.json();
      setState(data.onboarding ?? null);
    } catch {
      // Best-effort
    }
  }, []);

  const update = useCallback((newState: OnboardingState) => {
    setState(newState);
  }, []);

  return {
    state,
    loading,
    llmConfigured,
    isActive: state?.status === 'active',
    dismiss,
    update,
    refetch: fetchState,
  };
}
