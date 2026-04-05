import { useState, useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { AutonomySlider } from '@/components/suggestions/AutonomySlider';
import { SuggestionCategory, AutonomyLevel } from '@/data/mockData';
import { Shield, Bot, Terminal, MessageSquare, Lock, Puzzle, Globe, Phone } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
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

interface DialpadConfig {
  enabled: boolean;
  apiKey: string;
  fromNumber: string;
  phoneWhitelist: string;
  webhookUrl?: string;
  webhookCanRegister?: boolean;
  webhookReason?: string;
}

interface IntegrationsState {
  dialpad: DialpadConfig;
  telegram: ChannelConfig;
  whatsapp: ChannelConfig;
}

interface AgentFile {
  filename: string;
  content: string;
  readonly: boolean;
}

const AGENT_FILE_LABELS: Record<string, string> = {
  'SOUL.md': 'Soul',
  'AGENTS.md': 'Agents',
  'IDENTITY.md': 'Identity',
  'HEARTBEAT.md': 'Heartbeat',
  'memory/MEMORY.md': 'Memory',
  'USER.md': 'User',
  'TOOLS.md': 'Tools',
};

const SettingsPage = () => {
  const { autonomySettings, setAutonomySettings } = useApp();
  const [llmConfig, setLlmConfig] = useState<LlmConfig>({ apiKey: '', model: '', baseUrl: '' });
  const [integrations, setIntegrations] = useState<IntegrationsState>({
    dialpad: { enabled: false, apiKey: '', fromNumber: '', phoneWhitelist: '' },
    telegram: emptyChannel(),
    whatsapp: emptyChannel(),
  });
  const [agentIntegrations, setAgentIntegrations] = useState<{ braveApiKey: string; webSearchEnabled: boolean }>({
    braveApiKey: '', webSearchEnabled: false,
  });
  const [agentFiles, setAgentFiles] = useState<AgentFile[]>([]);
  const [agentFileContents, setAgentFileContents] = useState<Record<string, string>>({});
  const [savingFile, setSavingFile] = useState<string | null>(null);

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
          setIntegrations(prev => ({
            ...prev,
            dialpad: {
              enabled: data.dialpad?.enabled ?? false,
              apiKey: data.dialpad?.api_key ?? '',
              fromNumber: data.dialpad?.from_number ?? '',
              phoneWhitelist: (data.dialpad?.phone_whitelist ?? []).join(', '),
              webhookUrl: data.dialpad?.webhook_url ?? '',
            },
            telegram: {
              enabled: data.telegram?.enabled ?? false,
              token: data.telegram?.token ?? '',
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
          }));
        }
      })
      .catch(() => {});
    fetch('/settings/agent/integrations', { headers })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setAgentIntegrations({
            braveApiKey: '',
            webSearchEnabled: data.web_search_enabled ?? false,
          });
        }
      })
      .catch(() => {});
    fetch('/settings/integrations/dialpad/webhook', { headers })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setIntegrations(prev => ({
            ...prev,
            dialpad: {
              ...prev.dialpad,
              webhookUrl: data.webhook_url ?? prev.dialpad.webhookUrl,
              webhookCanRegister: data.can_register ?? true,
              webhookReason: data.reason ?? undefined,
            },
          }));
        }
      })
      .catch(() => {});
    fetch('/settings/agent/files', { headers })
      .then(r => r.ok ? r.json() : null)
      .then((files: AgentFile[] | null) => {
        if (files) {
          setAgentFiles(files);
          const contents: Record<string, string> = {};
          for (const f of files) contents[f.filename] = f.content;
          setAgentFileContents(contents);
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

  // Don't send the masked placeholder back as a real secret value
  const secretOrNull = (val: string) => val && !val.match(/^\u2022+$/) ? val : null;

  const handleSaveIntegrations = async () => {
    try {
      const res = await fetch('/settings/integrations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({
          dialpad: {
            enabled: integrations.dialpad.enabled,
            api_key: secretOrNull(integrations.dialpad.apiKey),
            from_number: integrations.dialpad.fromNumber || null,
            phone_whitelist: integrations.dialpad.phoneWhitelist
              .split(',').map(s => s.trim()).filter(Boolean),
          },
          telegram: {
            enabled: integrations.telegram.enabled,
            token: secretOrNull(integrations.telegram.token),
            allow_from: parseAllowFrom(integrations.telegram.allowFrom),
          },
          whatsapp: {
            enabled: integrations.whatsapp.enabled,
            bridge_url: integrations.whatsapp.token || null,
            bridge_token: secretOrNull(integrations.whatsapp.botToken),
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
          api_key: secretOrNull(llmConfig.apiKey),
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

  const handleSaveAgentIntegrations = async () => {
    try {
      const res = await fetch('/settings/agent/integrations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${getToken()}`,
        },
        body: JSON.stringify({
          brave_api_key: agentIntegrations.braveApiKey || null,
          web_search_enabled: agentIntegrations.webSearchEnabled,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success('Agent integrations saved — agent restarted');
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`);
    }
  };

  const handleSaveAgentFile = async (filename: string) => {
    setSavingFile(filename);
    try {
      const res = await fetch(`/settings/agent/files/${filename}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ content: agentFileContents[filename] ?? '' }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success(`${AGENT_FILE_LABELS[filename] ?? filename} saved`);
    } catch (err) {
      toast.error(`Failed to save: ${(err as Error).message}`);
    } finally {
      setSavingFile(null);
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
          {/* Dialpad */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Checkbox
                id="dp-enabled"
                checked={integrations.dialpad.enabled}
                onCheckedChange={v => setIntegrations(prev => ({ ...prev, dialpad: { ...prev.dialpad, enabled: !!v } }))}
              />
              <Phone className="h-4 w-4 text-muted-foreground" />
              <Label htmlFor="dp-enabled" className="font-semibold cursor-pointer">Dialpad SMS</Label>
            </div>
            {integrations.dialpad.enabled && (
              <div className="pl-6 space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="dp-key">API Key</Label>
                  <Input
                    id="dp-key"
                    type="password"
                    placeholder="Leave blank to keep existing key"
                    value={integrations.dialpad.apiKey}
                    onChange={e => setIntegrations(prev => ({ ...prev, dialpad: { ...prev.dialpad, apiKey: e.target.value } }))}
                  />
                  <p className="text-xs text-muted-foreground">Found in Dialpad Admin &gt; Company Settings &gt; API Keys</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1.5"
                    onClick={async () => {
                      try {
                        const res = await fetch('/settings/integrations/dialpad/test', {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
                          body: JSON.stringify({ api_key: integrations.dialpad.apiKey || null }),
                        });
                        const data = await res.json();
                        if (data.ok) {
                          toast.success(`Connected to ${data.company} (${data.status})`);
                        } else {
                          toast.error(data.error || 'Connection failed');
                        }
                      } catch {
                        toast.error('Connection test failed');
                      }
                    }}
                  >
                    Test Connection
                  </Button>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="dp-from">Outbound Phone Number</Label>
                  <Input
                    id="dp-from"
                    placeholder="+12065551234"
                    value={integrations.dialpad.fromNumber}
                    onChange={e => setIntegrations(prev => ({ ...prev, dialpad: { ...prev.dialpad, fromNumber: e.target.value } }))}
                  />
                  <p className="text-xs text-muted-foreground">Your Dialpad number used to send outbound SMS</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="dp-whitelist">Phone Whitelist <span className="text-muted-foreground font-normal">(optional)</span></Label>
                  <Input
                    id="dp-whitelist"
                    placeholder="+12065551234, +12065555678"
                    value={integrations.dialpad.phoneWhitelist}
                    onChange={e => setIntegrations(prev => ({ ...prev, dialpad: { ...prev.dialpad, phoneWhitelist: e.target.value } }))}
                  />
                  <p className="text-xs text-muted-foreground">Comma-separated phone numbers RentMate should respond to. Leave empty for all.</p>
                </div>
                <div className="space-y-2">
                  <Label>Webhook</Label>
                  {integrations.dialpad.webhookUrl ? (
                    <div className="flex items-center gap-2">
                      <code className="flex-1 text-[11px] bg-muted px-2.5 py-1.5 rounded truncate select-all">
                        {integrations.dialpad.webhookUrl}
                      </code>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">No webhook registered yet.</p>
                  )}
                  {integrations.dialpad.webhookCanRegister === false ? (
                    <p className="text-xs text-amber-600 dark:text-amber-400">
                      {integrations.dialpad.webhookReason || 'Webhook registration not available in this environment.'}
                    </p>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs gap-1.5"
                      onClick={async () => {
                        try {
                          const res = await fetch('/settings/integrations/dialpad/webhook', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
                            body: JSON.stringify({}),
                          });
                          const data = await res.json();
                          if (data.ok) {
                            setIntegrations(prev => ({ ...prev, dialpad: { ...prev.dialpad, webhookUrl: data.webhook_url } }));
                            toast.success('Webhook registered with Dialpad');
                          } else {
                            toast.error(data.error || 'Failed to register webhook');
                          }
                        } catch {
                          toast.error('Failed to register webhook');
                        }
                      }}
                    >
                      Register Webhook
                    </Button>
                  )}
                  <p className="text-xs text-muted-foreground">
                    Registers this server's URL with Dialpad for inbound SMS delivery.
                  </p>
                </div>
              </div>
            )}
          </div>

          <div className="border-t" />

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

      {/* Agent Integrations */}
      <Card className="p-6 rounded-xl">
        <div className="flex items-center gap-2 mb-1">
          <Puzzle className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-bold">Agent Integrations</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-6">
          Enable tools and capabilities for the AI agent.
        </p>

        <div className="space-y-4">
          {/* Web Search */}
          <div className="flex items-start gap-3 p-3 rounded-lg border">
            <Checkbox
              id="web-search"
              checked={agentIntegrations.webSearchEnabled}
              onCheckedChange={(checked) =>
                setAgentIntegrations(prev => ({ ...prev, webSearchEnabled: !!checked }))
              }
            />
            <div className="flex-1 space-y-2">
              <div className="flex items-center gap-2">
                <Globe className="h-4 w-4 text-muted-foreground" />
                <Label htmlFor="web-search" className="font-medium cursor-pointer">Web Search</Label>
              </div>
              <p className="text-xs text-muted-foreground">
                Allow the agent to search the web using Brave Search API.
              </p>
              {agentIntegrations.webSearchEnabled && (
                <div className="pt-1">
                  <Label htmlFor="brave-key" className="text-xs">Brave API Key</Label>
                  <Input
                    id="brave-key"
                    type="password"
                    placeholder="Leave blank to keep existing key"
                    value={agentIntegrations.braveApiKey}
                    onChange={e => setAgentIntegrations(prev => ({ ...prev, braveApiKey: e.target.value }))}
                    className="mt-1"
                  />
                  <p className="text-[10px] text-muted-foreground mt-1">
                    Get a key at api.search.brave.com
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>

        <Button onClick={handleSaveAgentIntegrations} className="w-full mt-6">Save Agent Integrations</Button>
      </Card>

      {/* AI Agent workspace */}
      {agentFiles.length > 0 && (
        <Card className="p-6 rounded-xl">
          <div className="flex items-center gap-2 mb-1">
            <Bot className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-bold">AI Agent</h2>
          </div>
          <p className="text-sm text-muted-foreground mb-6">
            Edit the agent's identity, behaviour, and long-term memory.
          </p>
          <Tabs defaultValue={agentFiles[0]?.filename}>
            <TabsList className="flex flex-wrap h-auto gap-1 mb-4">
              {agentFiles.map(f => (
                <TabsTrigger key={f.filename} value={f.filename} className="text-xs">
                  {f.readonly && <Lock className="h-3 w-3 mr-1 opacity-50" />}
                  {AGENT_FILE_LABELS[f.filename] ?? f.filename}
                </TabsTrigger>
              ))}
            </TabsList>
            {agentFiles.map(f => (
              <TabsContent key={f.filename} value={f.filename} className="space-y-3">
                {f.readonly && (
                  <p className="text-xs text-muted-foreground">Auto-generated on startup — read only.</p>
                )}
                <Textarea
                  className="font-mono text-xs min-h-96 resize-y"
                  value={agentFileContents[f.filename] ?? ''}
                  readOnly={f.readonly}
                  onChange={e => !f.readonly && setAgentFileContents(prev => ({ ...prev, [f.filename]: e.target.value }))}
                />
                <Button
                  onClick={() => handleSaveAgentFile(f.filename)}
                  disabled={f.readonly || savingFile === f.filename}
                  className="w-full"
                >
                  {savingFile === f.filename ? 'Saving…' : `Save ${AGENT_FILE_LABELS[f.filename] ?? f.filename}`}
                </Button>
              </TabsContent>
            ))}
          </Tabs>
        </Card>
      )}

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
