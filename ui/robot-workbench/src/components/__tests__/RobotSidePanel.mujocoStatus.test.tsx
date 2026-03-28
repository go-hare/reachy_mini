import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { MujocoPanel } from "@/components/workbench/RobotSidePanel";

const settingsMock = vi.hoisted(() => ({
  useSettings: vi.fn(),
}));

const reachyStatusMock = vi.hoisted(() => ({
  useReachyStatus: vi.fn(),
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

    return null;
  }),
}));

vi.mock("@/contexts/settings-context", () => settingsMock);
vi.mock("@/hooks/use-reachy-status", () => reachyStatusMock);
vi.mock("@tauri-apps/api/core", () => tauriCoreMock);

function mockWorkbenchSettings(robotSettings: Record<string, unknown>) {
  settingsMock.useSettings.mockReturnValue({
    settings: {
      robot_settings: robotSettings,
    },
  });
}

function mockReachyStatus(
  overrides: Partial<ReturnType<typeof reachyStatusMock.useReachyStatus>> = {},
) {
  reachyStatusMock.useReachyStatus.mockReturnValue({
    connectionState: "offline",
    daemonBaseUrl: "http://localhost:8000",
    snapshot: null,
    error: null,
    lastUpdatedAt: null,
    ...overrides,
  });
}

if (typeof document !== "undefined")
  describe("MujocoPanel live status", () => {
    beforeEach(() => {
      vi.stubGlobal("fetch", vi.fn());
      vi.mocked(tauriCoreMock.invoke).mockClear();
      mockReachyStatus();
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
      expect(
        screen.getByTestId("reachy-simulation-viewport"),
      ).toBeInTheDocument();
    });

    it("renders daemon-backed MuJoCo status and the embedded 3D viewport", async () => {
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
      mockReachyStatus({
        connectionState: "live",
        snapshot: {
          control_mode: "enabled",
          body_yaw: 0.2,
          antennas_position: [0.15, -0.15],
          head_joints: [0.2, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
          passive_joints: new Array(21).fill(0),
          timestamp: "2026-03-28T08:15:30.000Z",
        },
        lastUpdatedAt: "2026-03-28T08:15:30.000Z",
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
      expect(screen.getByText("enabled")).toBeInTheDocument();
      expect(screen.getAllByText("Embedded 3D").length).toBeGreaterThan(0);
      expect(
        screen.getByTestId("reachy-simulation-viewport"),
      ).toBeInTheDocument();
      expect(screen.getByText("Live Pose")).toBeInTheDocument();
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
      expect(screen.getAllByText("Embedded 3D").length).toBeGreaterThan(0);
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
      mockReachyStatus({
        connectionState: "connecting",
        error: "Waiting for state stream",
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
  });
