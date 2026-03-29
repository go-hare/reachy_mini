import { CameraOff, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import useWorkbenchCameraStream from "@/hooks/use-workbench-camera-stream";
import { buildReachyWebRtcSignalingUrl } from "@/lib/reachy-daemon";

function CameraOverlayMessage({
  title,
  detail,
  actionLabel,
  onAction,
}: {
  title: string;
  detail?: string | null;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-3 text-center">
      <CameraOff className="size-5 text-white/35" />
      <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/75">
        {title}
      </p>
      {detail ? (
        <p className="max-w-[112px] text-[10px] leading-4 text-white/45">
          {detail}
        </p>
      ) : null}
      {onAction && actionLabel ? (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-full border border-white/15 bg-white/10 px-2.5 py-1 text-[10px] font-medium text-white/80 transition hover:bg-white/15"
          onClick={onAction}
        >
          <RefreshCw className="size-3" />
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}

export default function WorkbenchCameraFeed({
  daemonBaseUrl,
  enabled,
  unavailableReason,
}: {
  daemonBaseUrl: string;
  enabled: boolean;
  unavailableReason?: string | null;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const signalingUrl = useMemo(
    () =>
      daemonBaseUrl ? buildReachyWebRtcSignalingUrl(daemonBaseUrl) : null,
    [daemonBaseUrl],
  );
  const {
    state,
    stream,
    error,
    connect,
    isConnected,
    isConnecting,
    isUnsupported,
  } = useWorkbenchCameraStream({
    signalingUrl,
    enabled: enabled && !unavailableReason,
  });

  useEffect(() => {
    if (!videoRef.current || !stream) {
      return;
    }

    videoRef.current.srcObject = stream;
    void videoRef.current.play().catch(() => {
      // The preview is muted, but autoplay can still be rejected temporarily.
    });
  }, [stream]);

  return (
    <div
      className="relative h-full w-full overflow-hidden rounded-[12px] bg-slate-950"
      data-testid="robot-workbench-camera-feed"
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className={`h-full w-full object-cover ${
          isConnected ? "block" : "hidden"
        }`}
        data-testid="robot-workbench-camera-video"
      />

      <div className="pointer-events-none absolute inset-x-0 top-0 h-8 bg-gradient-to-b from-black/35 to-transparent" />

      {unavailableReason ? (
        <CameraOverlayMessage title={unavailableReason} />
      ) : isUnsupported ? (
        <CameraOverlayMessage title="WebRTC unavailable" />
      ) : isConnecting ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
          <LoaderCircle className="size-5 animate-spin text-amber-400" />
          <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/75">
            Connecting...
          </p>
        </div>
      ) : state === "error" ? (
        <CameraOverlayMessage
          title="Camera failed"
          detail={error}
          actionLabel="Retry"
          onAction={() => void connect()}
        />
      ) : state === "disconnected" && enabled ? (
        <CameraOverlayMessage
          title="Waiting for video"
          actionLabel="Reconnect"
          onAction={() => void connect()}
        />
      ) : !isConnected ? (
        <CameraOverlayMessage title="Start runtime to stream" />
      ) : null}
    </div>
  );
}
