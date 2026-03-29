import { useCallback, useEffect, useRef, useState } from "react";

export type WorkbenchCameraStreamState =
  | "idle"
  | "loading-api"
  | "disconnected"
  | "connecting"
  | "connected"
  | "error"
  | "unsupported";

const RECONNECT_DELAY_MS = 5_000;

let gstWebRtcApiPromise: Promise<unknown> | null = null;

function isJsdomEnvironment() {
  return (
    typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent)
  );
}

async function ensureGstWebRtcApiLoaded() {
  if (typeof window === "undefined" || isJsdomEnvironment()) {
    return null;
  }

  const existingApi = (window as Window & { GstWebRTCAPI?: unknown })
    .GstWebRTCAPI;
  if (existingApi) {
    return existingApi;
  }

  gstWebRtcApiPromise ??= import("@/lib/gstwebrtc-api.js");
  await gstWebRtcApiPromise;

  return (window as Window & { GstWebRTCAPI?: unknown }).GstWebRTCAPI ?? null;
}

export function useWorkbenchCameraStream({
  signalingUrl,
  enabled,
}: {
  signalingUrl: string | null;
  enabled: boolean;
}) {
  const [state, setState] = useState<WorkbenchCameraStreamState>("idle");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);

  const apiRef = useRef<any>(null);
  const sessionRef = useRef<any>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const producersListenerRef = useRef<any>(null);
  const connectionListenerRef = useRef<any>(null);
  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);

  const cleanup = useCallback(() => {
    if (reconnectTimerRef.current != null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    if (sessionRef.current) {
      try {
        sessionRef.current.close();
      } catch {
        // Ignore teardown errors from the third-party WebRTC bridge.
      }
      sessionRef.current = null;
    }

    if (apiRef.current) {
      try {
        if (producersListenerRef.current) {
          apiRef.current.unregisterProducersListener(
            producersListenerRef.current,
          );
          producersListenerRef.current = null;
        }
        if (connectionListenerRef.current) {
          apiRef.current.unregisterConnectionListener(
            connectionListenerRef.current,
          );
          connectionListenerRef.current = null;
        }
      } catch {
        // Ignore teardown errors from the third-party WebRTC bridge.
      }

      apiRef.current = null;
    }

    setStream(null);
  }, []);

  const connect = useCallback(async () => {
    if (!mountedRef.current || !enabledRef.current) {
      return;
    }

    if (!signalingUrl) {
      setError("Missing WebRTC signaling URL");
      setState("error");
      return;
    }

    if (
      typeof window === "undefined" ||
      typeof RTCPeerConnection === "undefined" ||
      isJsdomEnvironment()
    ) {
      setError("WebRTC unavailable in this environment");
      setState("unsupported");
      return;
    }

    cleanup();
    setError(null);
    setState("loading-api");

    try {
      const GstWebRTCAPI = await ensureGstWebRtcApiLoaded();

      if (!mountedRef.current || !enabledRef.current) {
        return;
      }

      if (!GstWebRTCAPI) {
        throw new Error("GstWebRTCAPI failed to load");
      }

      setState("connecting");

      const api = new (GstWebRTCAPI as any)({
        signalingServerUrl: signalingUrl,
        reconnectionTimeout: 0,
        meta: { name: "reachy-workbench" },
        webrtcConfig: {
          iceServers: [
            { urls: "stun:stun.l.google.com:19302" },
            { urls: "stun:stun1.l.google.com:19302" },
          ],
        },
      });

      apiRef.current = api;

      connectionListenerRef.current = {
        connected: () => {
          if (!mountedRef.current) return;
        },
        disconnected: () => {
          if (!mountedRef.current) return;

          setStream(null);
          setState("disconnected");

          if (enabledRef.current && reconnectTimerRef.current == null) {
            reconnectTimerRef.current = window.setTimeout(() => {
              reconnectTimerRef.current = null;
              void connect();
            }, RECONNECT_DELAY_MS);
          }
        },
      };

      producersListenerRef.current = {
        producerAdded: (producer: { id: string }) => {
          if (!mountedRef.current || sessionRef.current) {
            return;
          }

          const session = api.createConsumerSession(producer.id);
          if (!session) {
            setError("Failed to create camera session");
            setState("error");
            return;
          }

          sessionRef.current = session;

          session.addEventListener("error", (event: { message?: string }) => {
            if (!mountedRef.current) return;
            setError(event.message || "Camera stream error");
            setState("error");
          });

          session.addEventListener("closed", () => {
            if (!mountedRef.current) return;
            sessionRef.current = null;
            setStream(null);
            setState((prevState: WorkbenchCameraStreamState) =>
              prevState === "connected" ? "disconnected" : prevState,
            );
          });

          session.addEventListener("streamsChanged", () => {
            if (!mountedRef.current) return;

            const nextStreams = session.streams as MediaStream[] | undefined;
            const nextStream = nextStreams?.[0] ?? null;

            setStream(nextStream);
            setState(nextStream ? "connected" : "connecting");
          });

          session.connect();
        },
        producerRemoved: () => {
          if (!mountedRef.current || !sessionRef.current) {
            return;
          }

          try {
            sessionRef.current.close();
          } catch {
            // Ignore teardown errors from the third-party WebRTC bridge.
          }
          sessionRef.current = null;
          setStream(null);
          setState("disconnected");
        },
      };

      api.registerConnectionListener(connectionListenerRef.current);
      api.registerProducersListener(producersListenerRef.current);
    } catch (streamError) {
      if (!mountedRef.current) return;

      const message =
        streamError instanceof Error
          ? streamError.message
          : "Unable to connect camera stream";

      setError(message);
      setState("error");
    }
  }, [cleanup, signalingUrl]);

  const disconnect = useCallback(() => {
    cleanup();
    setError(null);
    setState("disconnected");
  }, [cleanup]);

  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;
      cleanup();
    };
  }, [cleanup]);

  useEffect(() => {
    if (!enabled) {
      disconnect();
      return;
    }

    void connect();
  }, [connect, disconnect, enabled]);

  return {
    state,
    stream,
    error,
    connect,
    disconnect,
    isConnected: state === "connected",
    isConnecting: state === "loading-api" || state === "connecting",
    isUnsupported: state === "unsupported",
  };
}

export default useWorkbenchCameraStream;
