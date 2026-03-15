import { useState, useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { AutonomySlider } from '@/components/suggestions/AutonomySlider';
import { SuggestionCategory, AutonomyLevel } from '@/data/mockData';
import { Shield, Bot, Terminal } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { getToken } from '@/lib/auth';

const categories: SuggestionCategory[] = ['rent', 'maintenance', 'leasing', 'compliance'];

interface LlmConfig {
  apiKey: string;
  model: string;
  baseUrl: string;
}

const SettingsPage = () => {
  const { autonomySettings, setAutonomySettings } = useApp();
  const [llmConfig, setLlmConfig] = useState<LlmConfig>({ apiKey: '', model: '', baseUrl: '' });

  useEffect(() => {
    fetch('/settings', { headers: { Authorization: `Bearer ${getToken()}` } })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setLlmConfig({
            apiKey: data.api_key ?? '',
            model: data.model ?? '',
            baseUrl: data.base_url ?? '',
          });
          if (data.autonomy) {
            setAutonomySettings(data.autonomy);
          }
        }
      })
      .catch(() => {});
  }, []);

  const handleChange = (category: SuggestionCategory, level: AutonomyLevel) => {
    setAutonomySettings({ ...autonomySettings, [category]: level });
  };

  const handleSaveAutonomy = async () => {
    try {
      const res = await fetch('/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({ autonomy: autonomySettings }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success('Autonomy settings saved');
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`);
    }
  };

  const handleSaveLlmConfig = async () => {
    try {
      const res = await fetch('/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({
          api_key: llmConfig.apiKey,
          model: llmConfig.model,
          base_url: llmConfig.baseUrl,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success('LLM configuration saved');
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`);
    }
  };

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-sm text-muted-foreground">Configure RentMate</p>
      </div>

      {/* Autonomy Controls */}
      <Card className="p-6 rounded-xl">
        <div className="flex items-center gap-2 mb-1">
          <Shield className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-bold">Autonomy Levels</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-6">
          Control how much independence your AI agent has for each category.
        </p>

        <div className="space-y-6">
          {categories.map((cat) =>
            <AutonomySlider
              key={cat}
              category={cat}
              value={autonomySettings[cat]}
              onChange={(level) => handleChange(cat, level)}
              maxLevel={cat === 'compliance' ? 'suggest' : undefined}
              maxLevelReason={cat === 'compliance' ? 'Compliance actions require human review for legal and safety reasons.' : undefined}
            />
          )}
        </div>
        <Button onClick={handleSaveAutonomy} className="w-full mt-6">Save Autonomy Settings</Button>
      </Card>

      {/* LLM Backend Config */}
      <Card className="p-6 rounded-xl">
        <div className="flex items-center gap-2 mb-1">
          <Bot className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-bold">LLM Backend</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-6">
          Configure the language model backend for your AI property manager.
        </p>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="llm-api-key">API Key</Label>
            <Input
              id="llm-api-key"
              type="password"
              placeholder="sk-..."
              value={llmConfig.apiKey}
              onChange={(e) => setLlmConfig(prev => ({ ...prev, apiKey: e.target.value }))}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="llm-model">LiteLLM Model String</Label>
            <Input
              id="llm-model"
              placeholder="e.g. openai/gpt-4o-mini"
              value={llmConfig.model}
              onChange={(e) => setLlmConfig(prev => ({ ...prev, model: e.target.value }))}
            />
            <p className="text-xs text-muted-foreground">
              LiteLLM model string, e.g. openai/gpt-4o-mini, anthropic/claude-haiku-4-5, groq/llama-3.1-8b-instant.
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="llm-base-url">Base URL <span className="text-muted-foreground font-normal">(optional)</span></Label>
            <Input
              id="llm-base-url"
              placeholder="https://api.openai.com/v1"
              value={llmConfig.baseUrl}
              onChange={(e) => setLlmConfig(prev => ({ ...prev, baseUrl: e.target.value }))}
            />
          </div>
          <Button onClick={handleSaveLlmConfig} className="w-full">Save LLM Configuration</Button>
        </div>
      </Card>

      {/* Developer Tools link */}
      <div className="pt-2">
        <Link
          to="/dev"
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <Terminal className="h-4 w-4" />
          Developer Tools →
        </Link>
      </div>
    </div>
  );
};

export default SettingsPage;
