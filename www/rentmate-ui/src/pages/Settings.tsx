import { useState, useEffect } from 'react';
import { useApp } from '@/context/AppContext';
import { Card } from '@/components/ui/card';
import { AutonomySlider } from '@/components/suggestions/AutonomySlider';
import { ActionPolicyLevel } from '@/data/mockData';
import { Shield, Bot, Terminal, MessageSquare, Lock, Puzzle, Globe, Phone, Loader2, CheckCircle2, XCircle, HardDrive, Download, Upload } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { toast } from 'sonner';
import { authFetch } from '@/lib/auth';

const actionPolicies = [
  {
    key: 'entity_changes',
    label: 'Entity Changes',
    help: 'How confident the agent must be before directly creating or updating properties, tenants, units, and leases.',
  },
  {
    key: 'outbound_messages',
    label: 'Outbound Messages',
    help: 'How risky a tenant or vendor message can be before the agent must route it to review instead of sending directly.',
  },
  {
    key: 'suggestion_fallback',
    label: 'Suggestion Fallback',
    help: 'How quickly the agent falls back to a PM suggestion when it is uncertain or blocked from acting directly.',
  },
] as const;

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

interface QuoConfig {
  enabled: boolean;
  apiKey: string;
  fromNumber: string;
  phoneWhitelist: string;
  webhookUrl?: string;
  webhookCanRegister?: boolean;
  webhookReason?: string;
}

interface IntegrationsState {
  quo: QuoConfig;
  telegram: ChannelConfig;
  whatsapp: ChannelConfig;
}

interface AgentFile {
  filename: string;
  content: string;
  readonly: boolean;
}

interface SettingsPageProps {
  hideLlmConfig?: boolean;
}

const AGENT_FILE_LABELS: Record<string, string> = {
  'SOUL.md': 'Soul',
  'AGENTS.md': 'Agents',
  'IDENTITY.md': 'Identity',
  'ROUTINE.md': 'Routine',
  'memory/MEMORY.md': 'Memory',
  'USER.md': 'User',
  'TOOLS.md': 'Tools',
};

function labelForAgentFile(filename: string): string {
  return AGENT_FILE_LABELS[filename] ?? filename;
}

export const SettingsPage = ({ hideLlmConfig = false }: SettingsPageProps) => {
  const { actionPolicySettings, setActionPolicySettings } = useApp();
  const [llmConfig, setLlmConfig] = useState<LlmConfig>({ apiKey: '', model: '', baseUrl: '' });
  // Hosted backends report ``llm_managed: true`` from /api/settings.
  // Treat that the same as the explicit ``hideLlmConfig`` prop so PMs
  // never see a form whose POSTs the server silently ignores.
  const [llmManagedByHost, setLlmManagedByHost] = useState(false);
  const llmConfigHidden = hideLlmConfig || llmManagedByHost;
  const [integrations, setIntegrations] = useState<IntegrationsState>({
    quo: { enabled: false, apiKey: '', fromNumber: '', phoneWhitelist: '' },
    telegram: emptyChannel(),
    whatsapp: emptyChannel(),
  });
  const [agentIntegrations, setAgentIntegrations] = useState<{ braveApiKey: string; webSearchEnabled: boolean }>({
    braveApiKey: '', webSearchEnabled: false,
  });
  const [agentFiles, setAgentFiles] = useState<AgentFile[]>([]);
  const [agentFileContents, setAgentFileContents] = useState<Record<string, string>>({});
  const [savingFile, setSavingFile] = useState<string | null>(null);
  const [llmTesting, setLlmTesting] = useState(false);
  const [llmTestResult, setLlmTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    authFetch('/api/settings')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setLlmManagedByHost(Boolean(data.llm_managed));
          setLlmConfig({
            apiKey: data.api_key ?? '',
            model: data.model ?? '',
            baseUrl: data.base_url ?? '',
          });
          if (data.action_policy) {
            setActionPolicySettings(data.action_policy);
          }
        }
      })
      .catch(() => {});
    authFetch('/api/settings/integrations')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setIntegrations(prev => ({
            ...prev,
            quo: {
              enabled: data.quo?.enabled ?? false,
              apiKey: data.quo?.api_key ?? '',
              fromNumber: data.quo?.from_number ?? '',
              phoneWhitelist: (data.quo?.phone_whitelist ?? []).join(', '),
              webhookUrl: data.quo?.webhook_url ?? '',
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
    authFetch('/api/settings/agent/integrations')
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
    authFetch('/api/settings/integrations/quo/webhook')
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setIntegrations(prev => ({
            ...prev,
            quo: {
              ...prev.quo,
              webhookUrl: data.webhook_url ?? prev.quo.webhookUrl,
              webhookCanRegister: data.can_register ?? true,
              webhookReason: data.reason ?? undefined,
            },
          }));
        }
      })
      .catch(() => {});
    authFetch('/api/settings/agent/files')
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

  const handleChange = (policyKey: keyof typeof actionPolicySettings, level: ActionPolicyLevel) => {
    setActionPolicySettings({ ...actionPolicySettings, [policyKey]: level });
  };

  const handleSaveActionPolicy = async () => {
    try {
      const res = await authFetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action_policy: actionPolicySettings }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success('Action policy settings saved');
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
      const res = await authFetch('/api/settings/integrations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          quo: {
            enabled: integrations.quo.enabled,
            api_key: secretOrNull(integrations.quo.apiKey),
            from_number: integrations.quo.fromNumber || null,
            phone_whitelist: integrations.quo.phoneWhitelist
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
      const res = await authFetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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

  const handleTestLlm = async () => {
    setLlmTesting(true);
    setLlmTestResult(null);
    try {
      const res = await authFetch('/api/settings/llm/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (data.ok) {
        setLlmTestResult({ ok: true, message: `Connected — model replied "${data.reply}" in ${data.elapsed}s` });
      } else {
        setLlmTestResult({ ok: false, message: data.error || 'Connection failed' });
      }
    } catch {
      setLlmTestResult({ ok: false, message: 'Request failed — is the server running?' });
    } finally {
      setLlmTesting(false);
    }
  };

  const handleSaveAgentIntegrations = async () => {
    try {
      const res = await authFetch('/api/settings/agent/integrations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
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
      const res = await authFetch(`/api/settings/agent/files/${filename}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
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

      {llmConfigHidden && llmManagedByHost && (
        <Card className="p-6 rounded-xl bg-muted/30">
          <div className="flex items-center gap-2 mb-1">
            <Bot className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-bold">AI Model</h2>
          </div>
          <p className="text-sm text-muted-foreground">
            AI configuration is managed by RentMate. The hosted service
            picks the model and credentials for your account; there's
            nothing to set up here.
          </p>
        </Card>
      )}

      {!llmConfigHidden && (
        <Card className="p-6 rounded-xl">
          <div className="flex items-center gap-2 mb-1">
            <Bot className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-bold">AI Model</h2>
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
              <Label htmlFor="llm-model">Model</Label>
              <Input
                id="llm-model"
                placeholder="e.g. anthropic/claude-haiku-4-5-20251001"
                value={llmConfig.model}
                onChange={(e) => setLlmConfig(prev => ({ ...prev, model: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">
                Format: <code className="text-[11px]">provider/model-name</code>. For known providers
                (openai, anthropic, deepseek) the API endpoint is resolved automatically.
                For custom or local servers, set the Base URL below and use just the model name.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="llm-base-url">Base URL <span className="text-muted-foreground font-normal">(optional — for self-hosted or custom endpoints)</span></Label>
              <Input
                id="llm-base-url"
                placeholder="e.g. http://localhost:11434/v1"
                value={llmConfig.baseUrl}
                onChange={(e) => setLlmConfig(prev => ({ ...prev, baseUrl: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">
                Leave blank for cloud providers. Set this for Ollama, vLLM, or any OpenAI-compatible server.
              </p>
            </div>
            <div className="flex gap-2">
              <Button onClick={handleSaveLlmConfig} className="flex-1">Save</Button>
              <Button variant="outline" onClick={handleTestLlm} disabled={llmTesting} className="gap-1.5">
                {llmTesting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Test Model
              </Button>
            </div>
            {llmTestResult && (
              <div className={`flex items-start gap-2 p-3 rounded-lg text-sm ${llmTestResult.ok ? 'bg-accent/10 text-accent' : 'bg-destructive/10 text-destructive'}`}>
                {llmTestResult.ok ? <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" /> : <XCircle className="h-4 w-4 mt-0.5 shrink-0" />}
                <span className="break-all">{llmTestResult.message}</span>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Autonomy Controls */}
      <Card className="p-6 rounded-xl">
        <div className="flex items-center gap-2 mb-1">
          <Shield className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-bold">Action Policy</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-6">
          Control how aggressively the agent acts on internal records, outbound messages, and suggestion fallback.
        </p>

        <div className="space-y-6">
          {actionPolicies.map((policy) =>
            <AutonomySlider
              key={policy.key}
              label={policy.label}
              value={actionPolicySettings[policy.key]}
              onChange={(level) => handleChange(policy.key, level)}
            />
          )}
          <div className="grid gap-2 text-xs text-muted-foreground">
            {actionPolicies.map((policy) => (
              <p key={`${policy.key}-help`}><span className="font-medium text-foreground">{policy.label}:</span> {policy.help}</p>
            ))}
          </div>
        </div>
        <Button onClick={handleSaveActionPolicy} className="w-full mt-6">Save Action Policy</Button>
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
          {/* Quo */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Checkbox
                id="dp-enabled"
                checked={integrations.quo.enabled}
                onCheckedChange={v => setIntegrations(prev => ({ ...prev, quo: { ...prev.quo, enabled: !!v } }))}
              />
              <Phone className="h-4 w-4 text-muted-foreground" />
              <Label htmlFor="dp-enabled" className="font-semibold cursor-pointer">Quo SMS</Label>
            </div>
            {integrations.quo.enabled && (
              <div className="pl-6 space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="dp-key">API Key</Label>
                  <Input
                    id="dp-key"
                    type="password"
                    placeholder="Leave blank to keep existing key"
                    value={integrations.quo.apiKey}
                    onChange={e => setIntegrations(prev => ({ ...prev, quo: { ...prev.quo, apiKey: e.target.value } }))}
                  />
                  <p className="text-xs text-muted-foreground">Found in Quo Workspace Settings &gt; Integrations &gt; API</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs gap-1.5"
                    onClick={async () => {
                      try {
                        const res = await authFetch('/api/settings/integrations/quo/test', {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ api_key: integrations.quo.apiKey || null }),
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
                    value={integrations.quo.fromNumber}
                    onChange={e => setIntegrations(prev => ({ ...prev, quo: { ...prev.quo, fromNumber: e.target.value } }))}
                  />
                  <p className="text-xs text-muted-foreground">Your Quo number used to send outbound SMS</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="dp-whitelist">Phone Whitelist <span className="text-muted-foreground font-normal">(optional)</span></Label>
                  <Input
                    id="dp-whitelist"
                    placeholder="+12065551234, +12065555678"
                    value={integrations.quo.phoneWhitelist}
                    onChange={e => setIntegrations(prev => ({ ...prev, quo: { ...prev.quo, phoneWhitelist: e.target.value } }))}
                  />
                  <p className="text-xs text-muted-foreground">Comma-separated phone numbers RentMate should respond to. Leave empty for all.</p>
                </div>
                <div className="space-y-2">
                  <Label>Webhook</Label>
                  {integrations.quo.webhookUrl ? (
                    <div className="flex items-center gap-2">
                      <code className="flex-1 text-[11px] bg-muted px-2.5 py-1.5 rounded truncate select-all">
                        {integrations.quo.webhookUrl}
                      </code>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">No webhook registered yet.</p>
                  )}
                  {integrations.quo.webhookCanRegister === false ? (
                    <p className="text-xs text-amber-600 dark:text-amber-400">
                      {integrations.quo.webhookReason || 'Webhook registration not available in this environment.'}
                    </p>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs gap-1.5"
                      onClick={async () => {
                        try {
                          const res = await authFetch('/api/settings/integrations/quo/webhook', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({}),
                          });
                          const data = await res.json();
                          if (data.ok) {
                            setIntegrations(prev => ({ ...prev, quo: { ...prev.quo, webhookUrl: data.webhook_url } }));
                            toast.success(data.message || 'Webhook URL saved');
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
                    Configure this webhook URL in Quo Workspace Settings &gt; Integrations &gt; Webhooks.
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
            Inspect the full agent workspace for this account. Only supported files can be edited here.
          </p>
          <Tabs defaultValue={agentFiles[0]?.filename}>
            <TabsList className="flex flex-wrap h-auto gap-1 mb-4">
              {agentFiles.map(f => (
                <TabsTrigger key={f.filename} value={f.filename} className="text-xs">
                  {f.readonly && <Lock className="h-3 w-3 mr-1 opacity-50" />}
                  {labelForAgentFile(f.filename)}
                </TabsTrigger>
              ))}
            </TabsList>
            {agentFiles.map(f => (
              <TabsContent key={f.filename} value={f.filename} className="space-y-3">
                {f.readonly && (
                  <p className="text-xs text-muted-foreground">Read only.</p>
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
                  {savingFile === f.filename ? 'Saving…' : `Save ${labelForAgentFile(f.filename)}`}
                </Button>
              </TabsContent>
            ))}
          </Tabs>
        </Card>
      )}

      {/* Data Portability */}
      <Card className="p-6 rounded-xl">
        <div className="flex items-center gap-2 mb-1">
          <HardDrive className="h-5 w-5 text-primary" />
          <h2 className="text-lg font-bold">Data Portability</h2>
        </div>
        <p className="text-sm text-muted-foreground mb-6">
          Export all data for backup or migrate to another RentMate instance.
        </p>
        <div className="flex flex-col sm:flex-row gap-3">
          <Button
            variant="outline"
            onClick={async () => {
              try {
                const res = await authFetch('/api/export');
                if (!res.ok) {
                  const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
                  toast.error(err.detail || 'Export failed');
                  return;
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `rentmate-export-${new Date().toISOString().slice(0, 10)}.zip`;
                a.click();
                URL.revokeObjectURL(url);
                toast.success('Export downloaded');
              } catch {
                toast.error('Export failed');
              }
            }}
          >
            <Download className="h-4 w-4 mr-2" />
            Export Data
          </Button>
          <div className="flex items-center gap-2">
            <input
              type="file"
              accept=".zip,.json"
              id="import-file"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                if (!window.confirm('Import will only work on a fresh instance with no existing data. Continue?')) {
                  e.target.value = '';
                  return;
                }
                try {
                  const form = new FormData();
                  form.append('file', file);
                  const res = await authFetch('/api/import', { method: 'POST', body: form });
                  const data = await res.json();
                  if (!res.ok) {
                    toast.error(data.detail || 'Import failed');
                  } else {
                    const total = Object.values(data.summary as Record<string, number>).reduce((a: number, b: number) => a + b, 0);
                    toast.success(`Imported ${total} records across ${Object.keys(data.summary).length} tables`);
                  }
                } catch {
                  toast.error('Import failed');
                } finally {
                  e.target.value = '';
                }
              }}
            />
            <Button variant="outline" onClick={() => document.getElementById('import-file')?.click()}>
              <Upload className="h-4 w-4 mr-2" />
              Import Data
            </Button>
          </div>
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
