import { useState, useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { AutonomySlider } from '@/components/suggestions/AutonomySlider';
import { SuggestionCategory, AutonomyLevel } from '@/data/mockData';
import { Shield, Bot, Terminal, MessageSquare } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { getToken } from '@/lib/auth';

const categories: SuggestionCategory[] = ['rent', 'maintenance', 'leasing', 'compliance'];

interface LlmConfig {
  apiKey: string;
  model: string;
  baseUrl: string;
}

interface ChannelConfig {
  enabled: boolean;
  token: string;
  botToken: string;
  appToken: string;
  allowFrom: string; // newline-separated
}

const emptyChannel = (): ChannelConfig => ({
  enabled: false, token: '', botToken: '', appToken: '', allowFrom: '',
});

interface IntegrationsState {
  telegram: ChannelConfig;
  whatsapp: ChannelConfig;
}

const SettingsPage = () => {
  const { autonomySettings, setAutonomySettings } = useApp();
  const [llmConfig, setLlmConfig] = useState<LlmConfig>({ apiKey: '', model: '', baseUrl: '' });
  const [integrations, setIntegrations] = useState<IntegrationsState>({
    telegram: emptyChannel(),
    whatsapp: emptyChannel(),
  });

  useEffect(() => {
    const headers = { Authorization: `Bearer ${getToken()}` };
    fetch('/settings', { headers })
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
    fetch('/settings/integrations', { headers })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setIntegrations({
            telegram: {
              enabled: data.telegram?.enabled ?? false,
              token: '',
              botToken: '',
              appToken: '',
              allowFrom: (data.telegram?.allow_from ?? []).join('\n'),
            },
            whatsapp: {
              enabled: data.whatsapp?.enabled ?? false,
              token: data.whatsapp?.bridge_url ?? 'ws://localhost:3001',
              botToken: '',
              appToken: '',
              allowFrom: (data.whatsapp?.allow_from ?? []).join('\n'),
            },
          });
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

  const setChannel = (ch: keyof IntegrationsState, patch: Partial<ChannelConfig>) => {
    setIntegrations(prev => ({ ...prev, [ch]: { ...prev[ch], ...patch } }));
  };

  const parseAllowFrom = (text: string) =>
    text.split('\n').map(s => s.trim()).filter(Boolean);

  const handleSaveIntegrations = async () => {
    try {
      const res = await fetch('/settings/integrations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          telegram: {
            enabled: integrations.telegram.enabled,
            token: integrations.telegram.token || null,
            allow_from: parseAllowFrom(integrations.telegram.allowFrom),
          },
          whatsapp: {
            enabled: integrations.whatsapp.enabled,
            bridge_url: integrations.whatsapp.token || null,
            bridge_token: integrations.whatsapp.botToken || null,
            allow_from: parseAllowFrom(integrations.whatsapp.allowFrom),
          },
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success('Chat integrations saved');
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
          api_key: llmConfig.apiKey || null,
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
              placeholder="Leave blank to keep existing key"
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

      {/* Chat Integrations */}
      <Card className="p-6 rounded-xl">
        <div className="flex items-center gap-2 mb-1">
          <MessageSquare className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-bold">Chat Integrations</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-6">
          Connect RentMate to chat apps so tenants and managers can message the AI directly.
        </p>

        <div className="space-y-6">
          {/* Telegram */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Checkbox
                id="tg-enabled"
                checked={integrations.telegram.enabled}
                onCheckedChange={v => setChannel('telegram', { enabled: !!v })}
              />
              <Label htmlFor="tg-enabled" className="font-semibold cursor-pointer">Telegram</Label>
            </div>
            {integrations.telegram.enabled && (
              <div className="pl-6 space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="tg-token">Bot Token</Label>
                  <Input
                    id="tg-token"
                    type="password"
                    placeholder="Leave blank to keep existing token"
                    value={integrations.telegram.token}
                    onChange={e => setChannel('telegram', { token: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">Get this from @BotFather on Telegram.</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="tg-allow">Allowed Users <span className="text-muted-foreground font-normal">(optional)</span></Label>
                  <Textarea
                    id="tg-allow"
                    placeholder="One Telegram user ID or username per line"
                    value={integrations.telegram.allowFrom}
                    onChange={e => setChannel('telegram', { allowFrom: e.target.value })}
                    rows={3}
                  />
                  <p className="text-xs text-muted-foreground">Leave empty to allow anyone who messages the bot.</p>
                </div>
              </div>
            )}
          </div>

          <div className="border-t" />

          {/* WhatsApp */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Checkbox
                id="wa-enabled"
                checked={integrations.whatsapp.enabled}
                onCheckedChange={v => setChannel('whatsapp', { enabled: !!v })}
              />
              <Label htmlFor="wa-enabled" className="font-semibold cursor-pointer">WhatsApp</Label>
            </div>
            {integrations.whatsapp.enabled && (
              <div className="pl-6 space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="wa-bridge-url">Bridge URL</Label>
                  <Input
                    id="wa-bridge-url"
                    placeholder="ws://localhost:3001"
                    value={integrations.whatsapp.token}
                    onChange={e => setChannel('whatsapp', { token: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">
                    WebSocket URL of your WhatsApp bridge server (<code>@whiskeysockets/baileys</code>).
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="wa-bridge-token">Bridge Token <span className="text-muted-foreground font-normal">(optional)</span></Label>
                  <Input
                    id="wa-bridge-token"
                    type="password"
                    placeholder="Leave blank to keep existing token"
                    value={integrations.whatsapp.botToken}
                    onChange={e => setChannel('whatsapp', { botToken: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">Shared secret for bridge authentication.</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="wa-allow">Allowed Numbers <span className="text-muted-foreground font-normal">(optional)</span></Label>
                  <Textarea
                    id="wa-allow"
                    placeholder="One phone number per line"
                    value={integrations.whatsapp.allowFrom}
                    onChange={e => setChannel('whatsapp', { allowFrom: e.target.value })}
                    rows={3}
                  />
                  <p className="text-xs text-muted-foreground">Leave empty to allow any number.</p>
                </div>
              </div>
            )}
          </div>
        </div>

        <Button onClick={handleSaveIntegrations} className="w-full mt-6">Save Chat Integrations</Button>
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
