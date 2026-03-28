import { FolderOpen } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import type { GeneralSettingsProps } from "@/types/settings";
import { useState } from "react";
import { useToast } from "@/components/ToastProvider";
import {
  DEFAULT_MUJOCO_WEB_VIEWER_LAUNCH_COMMAND,
  DEFAULT_MUJOCO_WEB_VIEWER_URL,
} from "@/lib/reachy-daemon";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export function GeneralSettings({
  tempDefaultProjectsFolder,
  tempShowConsoleOutput,
  systemPrompt,
  saving,
  tempShowWelcomeRecentProjects = true,
  onFolderChange,
  onSelectFolder,
  onConsoleOutputChange,
  onSystemPromptChange,
  onClearRecentProjects,
  onShowWelcomeRecentProjectsChange,
  tempShowOnboardingOnStart = false,
  onShowOnboardingOnStartChange,
  maxConcurrentSessions = 10,
  onMaxConcurrentSessionsChange,
  tempReachyLiveStatusEnabled = false,
  onReachyLiveStatusEnabledChange,
  tempMujocoLiveStatusEnabled = false,
  onMujocoLiveStatusEnabledChange,
  tempMujocoViewerUrl = "",
  onMujocoViewerUrlChange,
  onApplyMujocoViewerPreset,
  onClearMujocoViewerUrl,
  tempMujocoViewerLaunchCommand = "",
  onMujocoViewerLaunchCommandChange,
  onApplyMujocoViewerLaunchCommandPreset,
  tempReachyDaemonBaseUrl = "http://localhost:8000",
  onReachyDaemonBaseUrlChange,
}: GeneralSettingsProps) {
  const { showSuccess, showError } = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [confirmWelcomeToggleOpen, setConfirmWelcomeToggleOpen] =
    useState(false);
  const [pendingWelcomeToggle, setPendingWelcomeToggle] = useState<
    boolean | null
  >(null);

  const handleConfirmClear = async () => {
    try {
      setClearing(true);
      await onClearRecentProjects();
      showSuccess("Recent projects cleared", "Success");
      setConfirmOpen(false);
    } catch (e) {
      showError("Failed to clear recent projects", "Error");
    } finally {
      setClearing(false);
    }
  };

  const requestToggleWelcome = (enabled: boolean) => {
    setPendingWelcomeToggle(enabled);
    setConfirmWelcomeToggleOpen(true);
  };

  const handleConfirmWelcomeToggle = async () => {
    if (pendingWelcomeToggle == null) return;
    try {
      onShowWelcomeRecentProjectsChange?.(pendingWelcomeToggle);
      showSuccess(
        pendingWelcomeToggle
          ? "Recent projects will be shown on Welcome"
          : "Recent projects will be hidden on Welcome",
        "Preference Updated",
      );
    } catch (e) {
      showError("Failed to update preference", "Error");
    } finally {
      setConfirmWelcomeToggleOpen(false);
      setPendingWelcomeToggle(null);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-medium mb-4">General Settings</h3>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="projects-folder">Default Projects Folder</Label>
            <div className="flex gap-2">
              <Input
                id="projects-folder"
                placeholder="/Users/username/Projects"
                value={tempDefaultProjectsFolder}
                onChange={(e) => onFolderChange(e.target.value)}
              />
              <Button variant="outline" size="sm" onClick={onSelectFolder}>
                <FolderOpen className="h-4 w-4" />
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              This folder will be used as the default location for cloning
              repositories.
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="max-concurrent-sessions">
              Maximum Concurrent Sessions
            </Label>
            <Input
              id="max-concurrent-sessions"
              type="number"
              min={1}
              max={20}
              value={maxConcurrentSessions}
              onChange={(e) =>
                onMaxConcurrentSessionsChange?.(
                  parseInt(e.target.value, 10) || 10,
                )
              }
              className="max-w-xs"
            />
            <p className="text-xs text-muted-foreground">
              Maximum number of CLI sessions that can run simultaneously across
              all configured agents.
            </p>
          </div>
          <div className="space-y-4">
            <h4 className="text-sm font-medium">Welcome Screen</h4>
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="welcome-recent-toggle">
                  Show Recent Projects
                </Label>
                <p className="text-xs text-muted-foreground">
                  Display up to 5 projects opened in the last 30 days on the
                  Welcome screen.
                </p>
              </div>
              <Switch
                id="welcome-recent-toggle"
                checked={!!tempShowWelcomeRecentProjects}
                onCheckedChange={(val) => requestToggleWelcome(val)}
              />
            </div>
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="onboarding-on-start-toggle">
                  Show Onboarding on Start
                </Label>
                <p className="text-xs text-muted-foreground">
                  Show the onboarding guide every time the app starts, even if
                  already completed.
                </p>
              </div>
              <Switch
                id="onboarding-on-start-toggle"
                checked={!!tempShowOnboardingOnStart}
                onCheckedChange={(val) => onShowOnboardingOnStartChange?.(val)}
              />
            </div>
          </div>
          <div className="space-y-4">
            <h4 className="text-sm font-medium">Robot Workbench</h4>
            <div className="flex items-center justify-between rounded-md border border-border/60 p-4">
              <div className="space-y-0.5">
                <Label htmlFor="reachy-live-status-toggle">
                  Enable Reachy Live Status
                </Label>
                <p className="text-xs text-muted-foreground">
                  {tempReachyLiveStatusEnabled
                    ? "Robot status live stream enabled for the configured daemon."
                    : "Robot status live stream disabled."}
                </p>
              </div>
              <Switch
                id="reachy-live-status-toggle"
                checked={!!tempReachyLiveStatusEnabled}
                onCheckedChange={(value) =>
                  onReachyLiveStatusEnabledChange?.(value)
                }
                aria-label="Enable Reachy Live Status"
              />
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/60 p-4">
              <div className="space-y-0.5">
                <Label htmlFor="mujoco-live-status-toggle">
                  Enable MuJoCo Live Status
                </Label>
                <p className="text-xs text-muted-foreground">
                  {tempMujocoLiveStatusEnabled
                    ? "MuJoCo daemon polling enabled for the configured Reachy endpoint."
                    : "MuJoCo daemon polling disabled."}
                </p>
              </div>
              <Switch
                id="mujoco-live-status-toggle"
                checked={!!tempMujocoLiveStatusEnabled}
                onCheckedChange={(value) =>
                  onMujocoLiveStatusEnabledChange?.(value)
                }
                aria-label="Enable MuJoCo Live Status"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="mujoco-viewer-url">MuJoCo Web Viewer URL</Label>
              <Input
                id="mujoco-viewer-url"
                placeholder={DEFAULT_MUJOCO_WEB_VIEWER_URL}
                value={tempMujocoViewerUrl}
                onChange={(e) => onMujocoViewerUrlChange?.(e.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    onApplyMujocoViewerPreset?.() ??
                    onMujocoViewerUrlChange?.(DEFAULT_MUJOCO_WEB_VIEWER_URL)
                  }
                >
                  Use Local Preset
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    onClearMujocoViewerUrl?.() ?? onMujocoViewerUrlChange?.("")
                  }
                  disabled={!tempMujocoViewerUrl.trim()}
                >
                  Clear
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                这里是 MuJoCo Web Viewer 接入口。默认预设是{" "}
                <code>{DEFAULT_MUJOCO_WEB_VIEWER_URL}</code>；
                <code>reachy-mini-daemon --sim</code>{" "}
                现在仍然会弹原生窗口，只有你单独起了网页
                viewer，右侧工作台才会嵌这个地址。
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="mujoco-viewer-launch-command">
                MuJoCo Web Viewer Launch Command
              </Label>
              <Input
                id="mujoco-viewer-launch-command"
                placeholder={DEFAULT_MUJOCO_WEB_VIEWER_LAUNCH_COMMAND}
                value={tempMujocoViewerLaunchCommand}
                onChange={(e) =>
                  onMujocoViewerLaunchCommandChange?.(e.target.value)
                }
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    onApplyMujocoViewerLaunchCommandPreset?.() ??
                    onMujocoViewerLaunchCommandChange?.(
                      DEFAULT_MUJOCO_WEB_VIEWER_LAUNCH_COMMAND,
                    )
                  }
                >
                  Use Launch Preset
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => onMujocoViewerLaunchCommandChange?.("")}
                  disabled={!tempMujocoViewerLaunchCommand.trim()}
                >
                  Clear Launch Command
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                默认预填的是一个模板命令：
                <code>{DEFAULT_MUJOCO_WEB_VIEWER_LAUNCH_COMMAND}</code>。
                它已经按当前 <code>reachy</code> conda 环境和 <code>9001</code>{" "}
                端口对齐了，但你需要把
                <code>your_web_viewer</code> 换成你真正的 Python viewer 入口。
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="reachy-daemon-url">Reachy Daemon URL</Label>
              <Input
                id="reachy-daemon-url"
                placeholder="http://localhost:8000"
                value={tempReachyDaemonBaseUrl}
                onChange={(e) => onReachyDaemonBaseUrlChange?.(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Lite usually runs on `http://localhost:8000`. Wireless commonly
                uses `http://reachy-mini.local:8000`.
              </p>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="system-prompt">Global System Prompt</Label>
            <Textarea
              id="system-prompt"
              placeholder="Enter a global system prompt that will be used across all LLM providers..."
              value={systemPrompt || ""}
              onChange={(e) => onSystemPromptChange(e.target.value)}
              rows={4}
              className="resize-vertical"
            />
            <p className="text-xs text-muted-foreground">
              This prompt is sent to all LLM providers as the system message.
              When opening a project, Commander tries to seed this from
              `AGENTS.md`, `CLAUDE.md`, or `GEMINI.md` when present.
            </p>
          </div>
          <div className="space-y-4">
            <h4 className="text-sm font-medium">Console Output</h4>
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="console-output">Show Console Output</Label>
                <p className="text-xs text-muted-foreground">
                  Display real-time console output during git operations like
                  cloning repositories.
                </p>
              </div>
              <Switch
                id="console-output"
                checked={tempShowConsoleOutput}
                onCheckedChange={onConsoleOutputChange}
              />
            </div>
          </div>

          <div className="space-y-4">
            <h4 className="text-sm font-medium">Development Tools</h4>
            <div className="p-4 bg-muted/30 rounded-lg space-y-3">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Clear Recent Projects</Label>
                  <p className="text-xs text-muted-foreground">
                    Clear all recent projects from local storage. This action is
                    irreversible.
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirmOpen(true)}
                  disabled={saving}
                >
                  Clear
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Confirmation dialog for clearing recent projects */}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear recent projects?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently remove all recent projects from local
              storage. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearing}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmClear} disabled={clearing}>
              {clearing ? "Clearing…" : "Yes, clear them"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirmation dialog for welcome recent projects toggle */}
      <AlertDialog
        open={confirmWelcomeToggleOpen}
        onOpenChange={setConfirmWelcomeToggleOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingWelcomeToggle
                ? "Show recent projects on Welcome?"
                : "Hide recent projects on Welcome?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingWelcomeToggle
                ? "This will display a list of up to 5 projects opened in the last 30 days on the Welcome screen."
                : "This will hide the recent projects list from the Welcome screen. You can re-enable it anytime."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setPendingWelcomeToggle(null)}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirmWelcomeToggle}>
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
