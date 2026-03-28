import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useState } from "react";

import type { RobotDaemonProcessStatus } from "@/lib/reachy-daemon";

const POLL_INTERVAL_MS = 2_000;

const EMPTY_STATUS: RobotDaemonProcessStatus = {
  lifecycle: "stopped",
  pid: null,
  command: "",
  working_dir: null,
  started_at: null,
  exit_code: null,
  last_error: null,
  recent_logs: [],
};

function normalizeStatus(
  status?: RobotDaemonProcessStatus | null,
): RobotDaemonProcessStatus {
  return {
    ...EMPTY_STATUS,
    ...(status || {}),
    lifecycle: status?.lifecycle === "running" ? "running" : "stopped",
    recent_logs: Array.isArray(status?.recent_logs) ? status?.recent_logs : [],
  };
}

function toErrorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "MuJoCo viewer service command failed";
}

export function useRobotViewerProcess(
  projectPath?: string,
  launchCommand?: string,
) {
  const [status, setStatus] = useState<RobotDaemonProcessStatus>(EMPTY_STATUS);
  const [actionState, setActionState] = useState<
    "idle" | "starting" | "stopping"
  >("idle");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await invoke<RobotDaemonProcessStatus>(
      "get_mujoco_viewer_service_status",
    );
    setStatus(normalizeStatus(next));
    return next;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;

    const poll = async () => {
      try {
        const next = await invoke<RobotDaemonProcessStatus>(
          "get_mujoco_viewer_service_status",
        );
        if (cancelled) return;
        setStatus(normalizeStatus(next));
        setError(null);
      } catch (nextError) {
        if (cancelled) return;
        setError(toErrorMessage(nextError));
      } finally {
        if (cancelled) return;
        timer = window.setTimeout(() => {
          timer = null;
          void poll();
        }, POLL_INTERVAL_MS);
      }
    };

    void poll();

    return () => {
      cancelled = true;
      if (timer != null) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  const start = useCallback(async () => {
    setActionState("starting");
    setError(null);

    try {
      const next = await invoke<RobotDaemonProcessStatus>(
        "start_mujoco_viewer_service",
        {
          workingDir: projectPath ?? null,
          launchCommand: launchCommand ?? "",
        },
      );
      setStatus(normalizeStatus(next));
      return next;
    } catch (nextError) {
      const message = toErrorMessage(nextError);
      setError(message);
      throw nextError;
    } finally {
      setActionState("idle");
    }
  }, [launchCommand, projectPath]);

  const stop = useCallback(async () => {
    setActionState("stopping");
    setError(null);

    try {
      const next = await invoke<RobotDaemonProcessStatus>(
        "stop_mujoco_viewer_service",
      );
      setStatus(normalizeStatus(next));
      return next;
    } catch (nextError) {
      const message = toErrorMessage(nextError);
      setError(message);
      throw nextError;
    } finally {
      setActionState("idle");
    }
  }, []);

  return {
    status,
    error,
    refresh,
    start,
    stop,
    isStarting: actionState === "starting",
    isStopping: actionState === "stopping",
    isBusy: actionState !== "idle",
  };
}
