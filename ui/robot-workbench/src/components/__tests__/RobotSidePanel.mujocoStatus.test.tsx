import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { MujocoPanel } from "@/components/workbench/RobotSidePanel";

const settingsMock = vi.hoisted(() => ({
  useSettings: vi.fn(),
}));

const openerMock = vi.hoisted(() => ({
  openUrl: vi.fn(async () => null),
}));

const viewerProbeMock = vi.hoisted(() => ({
  result: {
    ok: true,
    status: 200,
    error: null,
  },
}));

const tauriCoreMock = vi.hoisted(() => ({
  invoke: vi.fn(async (cmd: string) => {
    if (cmd === "get_robot_sim_daemon_status") {
      return {
        lifecycle: "stopped",
        pid: null,
        command: "reachy-mini-daemon --sim",
        working_dir: "/projects/sample",
        started_at: null,
        exit_code: null,
        last_error: null,
        recent_logs: [],
      };
    }

    if (cmd === "get_mujoco_viewer_service_status") {
      return {
        lifecycle: "stopped",
        pid: null,
        command: "",
        working_dir: "/projects/sample",
        started_at: null,
        exit_code: null,
        last_error: null,
        recent_logs: [],
      };
    }

    if (cmd === "start_robot_sim_daemon") {
      return {
        lifecycle: "running",
        pid: 4242,
        command: "reachy-mini-daemon --sim",
        working_dir: "/projects/sample",
        started_at: "2026-03-28T12:00:00Z",
        exit_code: null,
        last_error: null,
        recent_logs: ["Desktop launched reachy-mini-daemon --sim (pid 4242)."],
      };
    }

    if (cmd === "start_mujoco_viewer_service") {
      return {
        lifecycle: "running",
        pid: 5252,
        command: "conda run -n reachy python -m viewer",
        working_dir: "/projects/sample",
        started_at: "2026-03-28T12:01:00Z",
        exit_code: null,
        last_error: null,
        recent_logs: [
          "Desktop launched conda run -n reachy python -m viewer (pid 5252).",
        ],
      };
    }

    if (cmd === "stop_robot_sim_daemon") {
      return {
        lifecycle: "stopped",
        pid: null,
        command: "reachy-mini-daemon --sim",
        working_dir: "/projects/sample",
        started_at: "2026-03-28T12:00:00Z",
        exit_code: 0,
        last_error: null,
        recent_logs: [
          "Desktop-managed reachy-mini-daemon --sim stopped with code 0.",
        ],
      };
    }

    if (cmd === "stop_mujoco_viewer_service") {
      return {
        lifecycle: "stopped",
        pid: null,
        command: "conda run -n reachy python -m viewer",
        working_dir: "/projects/sample",
        started_at: "2026-03-28T12:01:00Z",
        exit_code: 0,
        last_error: null,
        recent_logs: [
          "Desktop-managed conda run -n reachy python -m viewer stopped with code 0.",
        ],
      };
    }

    if (cmd === "probe_mujoco_viewer_url") {
      return viewerProbeMock.result;
    }

    return null;
  }),
}));

vi.mock("@/contexts/settings-context", () => settingsMock);
vi.mock("@tauri-apps/api/core", () => tauriCoreMock);
vi.mock("@tauri-apps/plugin-opener", () => openerMock);

function mockWorkbenchSettings(robotSettings: Record<string, unknown>) {
  const updateSettings = vi.fn(async () => null);

  settingsMock.useSettings.mockReturnValue({
    settings: {
      robot_settings: robotSettings,
    },
    updateSettings,
  });

  return { updateSettings };
}

if (typeof document !== "undefined")
  describe("MujocoPanel live status", () => {
    beforeEach(() => {
      vi.stubGlobal("fetch", vi.fn());
      vi.mocked(tauriCoreMock.invoke).mockClear();
      vi.mocked(openerMock.openUrl).mockClear();
      viewerProbeMock.result = {
        ok: true,
        status: 200,
        error: null,
      };
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      vi.clearAllMocks();
    });

    it("shows a disabled state and skips polling when live status is turned off", async () => {
      mockWorkbenchSettings({
        mujoco_live_status_enabled: false,
        daemon_base_url: "http://localhost:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      await waitFor(() => {
        expect(tauriCoreMock.invoke).toHaveBeenCalledWith(
          "get_robot_sim_daemon_status",
        );
      });

      expect(screen.getAllByText("Disabled").length).toBeGreaterThan(0);
      expect(vi.mocked(fetch)).not.toHaveBeenCalled();
    });

    it("renders daemon-backed MuJoCo status when simulation mode is active", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: true,
          mockup_sim_enabled: false,
          no_media: false,
          media_released: false,
          backend_status: {
            motor_control_mode: "enabled",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);

      mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        daemon_base_url: "http://localhost:8000/",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      await waitFor(() => {
        expect(vi.mocked(fetch)).toHaveBeenCalled();
      });

      expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe(
        "http://localhost:8000/api/daemon/status",
      );

      await waitFor(() => {
        expect(screen.getByText("Live")).toBeInTheDocument();
      });

      expect(screen.getByText("MuJoCo")).toBeInTheDocument();
      expect(screen.getByText("MuJoCo Runtime")).toBeInTheDocument();
      expect(screen.getByText("Running")).toBeInTheDocument();
      expect(screen.getByText("enabled")).toBeInTheDocument();
    });

    it("shows an idle simulator panel when the daemon is running a physical backend", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: false,
          mockup_sim_enabled: false,
          no_media: false,
          media_released: false,
          backend_status: {
            motor_control_mode: "gravity_compensation",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);

      mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        daemon_base_url: "http://reachy-mini.local:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      await waitFor(() => {
        expect(screen.getByText("Idle")).toBeInTheDocument();
      });

      expect(screen.getByText("Physical Robot")).toBeInTheDocument();
      expect(screen.getByText("gravity_compensation")).toBeInTheDocument();
    });

    it("renders an embedded viewer iframe when a MuJoCo viewer url is configured", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: true,
          mockup_sim_enabled: false,
          backend_status: {
            motor_control_mode: "enabled",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);

      mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        mujoco_viewer_url: "http://localhost:9001/viewer",
        daemon_base_url: "http://localhost:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      await waitFor(() => {
        expect(tauriCoreMock.invoke).toHaveBeenCalledWith(
          "probe_mujoco_viewer_url",
          { url: "http://localhost:9001/viewer" },
        );
      });

      expect(await screen.findByTitle("MuJoCo Viewer")).toHaveAttribute(
        "src",
        "http://localhost:9001/viewer",
      );
      expect(screen.getAllByText("Web Viewer").length).toBeGreaterThan(0);
    });

    it("shows an offline placeholder instead of rendering an iframe when the viewer url is unreachable", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: true,
          mockup_sim_enabled: false,
          backend_status: {
            motor_control_mode: "enabled",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);
      viewerProbeMock.result = {
        ok: false,
        status: null,
        error: "connection refused",
      };

      mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        mujoco_viewer_url: "http://localhost:9001/viewer",
        daemon_base_url: "http://localhost:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      expect(
        await screen.findByText("Viewer 服务未启动或地址不可达。"),
      ).toBeInTheDocument();
      expect(screen.getByText("connection refused")).toBeInTheDocument();
      expect(screen.queryByTitle("MuJoCo Viewer")).not.toBeInTheDocument();
    });

    it("shows the native-window hint when no MuJoCo viewer url is configured", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: true,
          mockup_sim_enabled: false,
          backend_status: {
            motor_control_mode: "enabled",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);

      const { updateSettings } = mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        mujoco_viewer_url: "",
        daemon_base_url: "http://localhost:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      expect(
        await screen.findByText("MuJoCo 当前走原生窗口。"),
      ).toBeInTheDocument();
      expect(screen.queryByTitle("MuJoCo Viewer")).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Use Local Preset" }));

      await waitFor(() => {
        expect(updateSettings).toHaveBeenCalledWith({
          robot_settings: {
            live_status_enabled: false,
            mujoco_live_status_enabled: true,
            mujoco_viewer_url: "http://127.0.0.1:9001/viewer",
            mujoco_viewer_launch_command:
              "conda run -n reachy python -m your_web_viewer --host 127.0.0.1 --port 9001",
            daemon_base_url: "http://localhost:8000",
          },
        });
      });
    });

    it("starts the desktop-managed simulator with the current project path", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: true,
          mockup_sim_enabled: false,
          backend_status: {
            motor_control_mode: "enabled",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);

      mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        daemon_base_url: "http://localhost:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      fireEvent.click(
        await screen.findByRole("button", { name: "Start Simulation" }),
      );

      await waitFor(() => {
        expect(tauriCoreMock.invoke).toHaveBeenCalledWith(
          "start_robot_sim_daemon",
          {
            workingDir: "/projects/sample",
          },
        );
      });

      expect(
        await screen.findByText("Desktop Runtime Live"),
      ).toBeInTheDocument();
      expect(screen.getByText("4242")).toBeInTheDocument();
    });

    it("starts the desktop-managed viewer service with the configured launch command", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: true,
          mockup_sim_enabled: false,
          backend_status: {
            motor_control_mode: "enabled",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);

      mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        mujoco_viewer_url: "http://localhost:9001/viewer",
        mujoco_viewer_launch_command: "conda run -n reachy python -m viewer",
        daemon_base_url: "http://localhost:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      fireEvent.click(
        await screen.findByRole("button", { name: "Start Viewer" }),
      );

      await waitFor(() => {
        expect(tauriCoreMock.invoke).toHaveBeenCalledWith(
          "start_mujoco_viewer_service",
          {
            workingDir: "/projects/sample",
            launchCommand: "conda run -n reachy python -m viewer",
          },
        );
      });

      expect(
        await screen.findByText("Viewer Service Live"),
      ).toBeInTheDocument();
      expect(screen.getByText("5252")).toBeInTheDocument();
    });

    it("opens the configured viewer url in the system browser", async () => {
      vi.mocked(fetch).mockResolvedValue({
        ok: true,
        json: async () => ({
          robot_name: "reachy_mini",
          state: "running",
          simulation_enabled: true,
          mockup_sim_enabled: false,
          backend_status: {
            motor_control_mode: "enabled",
            error: null,
          },
          error: null,
          version: "1.2.3",
        }),
      } as Response);

      mockWorkbenchSettings({
        mujoco_live_status_enabled: true,
        mujoco_viewer_url: "http://localhost:9001/viewer",
        daemon_base_url: "http://localhost:8000",
      });

      render(<MujocoPanel projectPath="/projects/sample" />);

      fireEvent.click(
        await screen.findByRole("button", { name: "Open in Browser" }),
      );

      await waitFor(() => {
        expect(openerMock.openUrl).toHaveBeenCalledWith(
          "http://localhost:9001/viewer",
        );
      });
    });
  });
