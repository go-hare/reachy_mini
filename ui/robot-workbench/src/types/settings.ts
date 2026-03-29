/**
 * Shared types for Settings components
 * Preserves ALL existing functionality from SettingsModal.tsx
 */

// ============================
// Core Settings Types
// ============================

export type SettingsTab =
  | "general"
  | "appearance"
  | "code"
  | "chat"
  | "prompts"
  | "agents"
  | "llms"
  | "subagents"
  | "autohand"
  | "docs";

export interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialTab?: SettingsTab;
  workingDir?: string | null;
}

// ============================
// General Settings Types
// ============================

export interface AppSettings {
  show_console_output: boolean;
  projects_folder: string;
  file_mentions_enabled: boolean;
  // UI theme for the app: 'auto' | 'light' | 'dark'
  ui_theme?: string;
  // Show/Hide recent projects on the Welcome screen
  show_welcome_recent_projects?: boolean;
  // Maximum number of chat messages retained per session
  max_chat_history?: number;
  code_settings?: {
    theme: string;
    font_size: number;
    auto_collapse_sidebar?: boolean;
    show_file_explorer?: boolean;
  };
  dashboard_time_range?: number;
  time_saved_multiplier?: number;
  show_dashboard_activity?: boolean;
  dashboard_color_palette?: string;
  dashboard_chart_type?: "scatter" | "knowledge-base";
  show_onboarding_on_start?: boolean;
  docs_auto_sync?: boolean;
  chat_history_style?: "palette" | "sidebar" | "strip";
  robot_settings?: {
    live_status_enabled?: boolean;
    mujoco_live_status_enabled?: boolean;
    daemon_base_url?: string;
  };
}

// ============================
// Agent Settings Types
// ============================

export interface AgentConfig {
  model: string;
  output_format: "markdown" | "json" | "plain" | "code";
  session_timeout_minutes: number;
  max_tokens: number | null;
  temperature: number | null;
  sandbox_mode: boolean;
  auto_approval: boolean;
  debug_mode: boolean;
  transport?: "cli-flags" | "json-rpc" | "acp";
}

export interface AllAgentSettings {
  max_concurrent_sessions: number;
  claude?: AgentConfig;
  codex?: AgentConfig;
  gemini?: AgentConfig;
  autohand?: AgentConfig;
  ollama?: AgentConfig;
  custom_agents?: import("@/components/settings/agent-registry").CustomAgentDefinition[];
}

export interface BasicAgentSettings {
  [agentId: string]: boolean;
}

// Type for Record<string, boolean> used in the main modal
export type AgentSettingsRecord = Record<string, boolean>;

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
}

// ============================
// LLM Settings Types (re-exported from existing types)
// ============================

import type {
  LLMModel as ExistingLLMModel,
  LLMProvider as ExistingLLMProvider,
  LLMSettings as ExistingLLMSettings,
  ProviderStatus as ExistingProviderStatus,
} from "./llm";

export type LLMModel = ExistingLLMModel;
export type LLMProvider = ExistingLLMProvider;
export type LLMSettings = ExistingLLMSettings;
export type ProviderStatus = ExistingProviderStatus;

// ============================
// Chat Settings Types
// ============================

export interface ChatSettings {
  file_mentions_enabled: boolean;
  auto_scroll: boolean;
  message_history_limit: number;
}

// ============================
// State Management Types
// ============================

export interface SettingsState {
  // General
  defaultProjectsFolder: string;
  showConsoleOutput: boolean;
  fileMentionsEnabled: boolean;

  // Temporary states for unsaved changes
  tempDefaultProjectsFolder: string;
  tempShowConsoleOutput: boolean;
  tempFileMentionsEnabled: boolean;

  // Agent settings
  agentSettings: BasicAgentSettings;
  tempAgentSettings: BasicAgentSettings;
  allAgentSettings: AllAgentSettings | null;
  tempAllAgentSettings: AllAgentSettings | null;

  // UI state
  hasUnsavedChanges: boolean;
  showUnsavedChangesDialog: boolean;

  // Loading states
  agentSettingsLoading: boolean;
  // Error states
  agentSettingsError: string | null;
}

// ============================
// Component Props Types
// ============================

export interface GeneralSettingsProps {
  tempDefaultProjectsFolder: string;
  tempShowConsoleOutput: boolean;
  systemPrompt?: string;
  saving: boolean;
  // Welcome screen recent projects toggle (temporary for unsaved changes)
  tempShowWelcomeRecentProjects?: boolean;
  onFolderChange: (folder: string) => void;
  onSelectFolder: () => Promise<void>;
  onConsoleOutputChange: (enabled: boolean) => void;
  onSystemPromptChange: (prompt: string) => void;
  onClearRecentProjects: () => Promise<void>;
  // Welcome screen toggle change handler
  onShowWelcomeRecentProjectsChange?: (enabled: boolean) => void;
  // Show onboarding on every app start
  tempShowOnboardingOnStart?: boolean;
  onShowOnboardingOnStartChange?: (enabled: boolean) => void;
  maxConcurrentSessions?: number;
  onMaxConcurrentSessionsChange?: (value: number) => void;
  tempReachyLiveStatusEnabled?: boolean;
  onReachyLiveStatusEnabledChange?: (enabled: boolean) => void;
  tempMujocoLiveStatusEnabled?: boolean;
  onMujocoLiveStatusEnabledChange?: (enabled: boolean) => void;
  tempReachyDaemonBaseUrl?: string;
  onReachyDaemonBaseUrlChange?: (value: string) => void;
}

export interface AppearanceSettingsProps {
  // UI theme
  tempUiTheme?: string;
  onUiThemeChange?: (theme: string) => void;
  // Dashboard color palette
  tempDashboardColorPalette?: string;
  onDashboardColorPaletteChange?: (palette: string) => void;
  // Dashboard activity visibility
  tempShowDashboardActivity?: boolean;
  onShowDashboardActivityChange?: (enabled: boolean) => void;
  // Dashboard chart type
  tempDashboardChartType?: "scatter" | "knowledge-base";
  onDashboardChartTypeChange?: (type: "scatter" | "knowledge-base") => void;
  // Code viewer theme
  tempCodeTheme?: string;
  onCodeThemeChange?: (theme: string) => void;
  // Code viewer font size
  tempCodeFontSize?: number;
  onCodeFontSizeChange?: (size: number) => void;
  // Chat history style
  tempChatHistoryStyle?: "palette" | "sidebar" | "strip";
  onChatHistoryStyleChange?: (style: "palette" | "sidebar" | "strip") => void;
}

export interface ChatSettingsProps {
  tempFileMentionsEnabled: boolean;
  onFileMentionsChange: (enabled: boolean) => void;
  tempChatSendShortcut?: "enter" | "mod+enter";
  onChatSendShortcutChange?: (shortcut: "enter" | "mod+enter") => void;
  tempMaxChatHistory?: number;
  onMaxChatHistoryChange?: (limit: number) => void;
  tempDefaultCliAgent: string;
  onDefaultCliAgentChange?: (agentId: string) => void;
}

export interface AgentSettingsProps {
  agentSettings: AgentSettingsRecord;
  tempAgentSettings: AgentSettingsRecord;
  allAgentSettings: AllAgentSettings | null;
  tempAllAgentSettings: AllAgentSettings | null;
  agentModels: Record<string, string[]>;
  fetchingAgentModels: Record<string, boolean>;
  agentSettingsLoading: boolean;
  agentSettingsError: string | null;
  onToggleAgent: (agentId: string, enabled: boolean) => void;
  onUpdateAgentSetting: (agentId: string, key: string, value: any) => void;
  onFetchAgentModels: (agentId: string) => Promise<void>;
  onCreateCustomAgent: (
    agent: import("@/components/settings/agent-registry").CustomAgentDefinition,
  ) => void;
  onUpdateCustomAgent: (
    agentId: string,
    updater:
      | Partial<
          import("@/components/settings/agent-registry").CustomAgentDefinition
        >
      | ((
          agent: import("@/components/settings/agent-registry").CustomAgentDefinition,
        ) => import("@/components/settings/agent-registry").CustomAgentDefinition),
  ) => void;
  onDeleteCustomAgent: (agentId: string) => void;
  workingDir?: string | null;
}

export interface LLMSettingsProps {
  settings: LLMSettings | null;
  providerStatuses: Record<string, ProviderStatus>;
  loading: boolean;
  saving: boolean;
  error: string | null;
  tempApiKeys: Record<string, string>;
  fetchingModels: Record<string, boolean>;
  onUpdateProvider: (
    providerId: string,
    updates: Partial<LLMProvider>,
  ) => Promise<void>;
  onSetActiveProvider: (providerId: string) => void;
  onFetchModels: (providerId: string) => Promise<void>;
  onRefreshStatuses: () => Promise<void>;
  onOpenOllamaWebsite: () => void;
  onUpdateSelectedModel: (providerId: string, modelId: string) => void;
  onSaveApiKey: (providerId: string) => Promise<void>;
  onTempApiKeyChange: (providerId: string, key: string) => void;
  onUpdateSystemPrompt: (prompt: string) => void;
}
