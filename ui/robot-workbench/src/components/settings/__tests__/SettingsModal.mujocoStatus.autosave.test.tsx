import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { SettingsModal } from "@/components/SettingsModal";
import { SettingsProvider } from "@/contexts/settings-context";

const defaultLoadAppSettings = () => ({
  show_console_output: true,
  projects_folder: "",
  file_mentions_enabled: true,
  ui_theme: "auto",
  chat_send_shortcut: "mod+enter",
  show_welcome_recent_projects: true,
  code_settings: {
    theme: "github",
    font_size: 14,
    auto_collapse_sidebar: false,
  },
  robot_settings: {
    live_status_enabled: true,
    mujoco_live_status_enabled: true,
    daemon_base_url: "http://localhost:8000",
  },
});

let loadAppSettingsState = defaultLoadAppSettings();

const invokeMock = vi.fn(async (cmd: string) => {
  switch (cmd) {
    case "load_app_settings":
      return loadAppSettingsState;
    case "set_window_theme":
      return null;
    case "save_app_settings":
      return null;
    case "get_default_projects_folder":
      return "";
    case "load_agent_settings":
      return { claude: true, codex: true, gemini: true };
    case "load_all_agent_settings":
      return { max_concurrent_sessions: 10 };
    default:
      return null;
  }
});

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: any[]) => invokeMock(...args),
}));
vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => {}),
}));
vi.mock("@/components/settings", () => ({
  GeneralSettings: ({
    tempMujocoLiveStatusEnabled,
    onMujocoLiveStatusEnabledChange,
  }: any) => (
    <div>
      <label htmlFor="mujoco-live-status-toggle">
        Enable MuJoCo Live Status
      </label>
      <input
        id="mujoco-live-status-toggle"
        aria-label="Enable MuJoCo Live Status"
        type="checkbox"
        checked={!!tempMujocoLiveStatusEnabled}
        onChange={(event) =>
          onMujocoLiveStatusEnabledChange?.(event.target.checked)
        }
      />
      <p>
        {tempMujocoLiveStatusEnabled
          ? "MuJoCo live daemon panel enabled."
          : "MuJoCo live daemon panel disabled"}
      </p>
    </div>
  ),
  AppearanceSettings: () => null,
  ChatSettings: () => null,
  AgentSettings: () => null,
  LLMSettings: () => null,
  CodeSettings: () => null,
  SubAgentsSettings: () => null,
  PromptsUISettings: () => null,
}));
vi.mock("@/components/settings/DocsSettings", () => ({
  DocsSettings: () => null,
}));
vi.mock("@/components/ToastProvider", () => ({
  ToastProvider: ({ children }: { children: any }) => children,
  useToast: () => ({
    showSuccess: vi.fn(),
    showError: vi.fn(),
    showToast: vi.fn(),
  }),
}));

if (typeof document !== "undefined")
  describe("SettingsModal MuJoCo status autosave", () => {
    beforeEach(() => {
      vi.clearAllMocks();
      loadAppSettingsState = defaultLoadAppSettings();
    });

    it("persists the MuJoCo live status toggle through app settings", async () => {
      render(
        <SettingsProvider>
          <SettingsModal
            isOpen={true}
            onClose={() => {}}
            initialTab="general"
          />
        </SettingsProvider>,
      );

      fireEvent.click(
        await screen.findByLabelText("Enable MuJoCo Live Status"),
      );

      await screen.findByText("MuJoCo live daemon panel disabled");

      await waitFor(() => {
        const saveCalls = invokeMock.mock.calls.filter(
          ([cmd]) => cmd === "save_app_settings",
        );
        expect(saveCalls.length).toBeGreaterThan(0);
        const matchingCall = [...saveCalls]
          .reverse()
          .find(
            ([, args]) =>
              args?.settings?.robot_settings?.mujoco_live_status_enabled ===
              false,
          );
        expect(matchingCall).toBeTruthy();
        expect(
          matchingCall?.[1]?.settings?.robot_settings?.mujoco_viewer_url,
        ).toBeUndefined();
        expect(
          matchingCall?.[1]?.settings?.robot_settings
            ?.mujoco_viewer_launch_command,
        ).toBeUndefined();
      });
    });

    it("does not render obsolete MuJoCo viewer settings in General Settings", async () => {
      loadAppSettingsState = {
        ...defaultLoadAppSettings(),
        robot_settings: {
          live_status_enabled: true,
          mujoco_live_status_enabled: true,
          daemon_base_url: "http://localhost:8000",
        },
      };

      render(
        <SettingsProvider>
          <SettingsModal
            isOpen={true}
            onClose={() => {}}
            initialTab="general"
          />
        </SettingsProvider>,
      );

      expect(screen.queryByLabelText("MuJoCo Viewer URL")).toBeNull();
      expect(screen.queryByLabelText("MuJoCo Viewer Launch Command")).toBeNull();
    });
  });
