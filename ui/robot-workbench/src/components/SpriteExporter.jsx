import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from '@mui/material';
import DownloadRoundedIcon from '@mui/icons-material/DownloadRounded';
import ImageOutlinedIcon from '@mui/icons-material/ImageOutlined';
import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded';
import Viewer3D from './viewer3d';
import useAppStore from '../store/useAppStore';

const FRAME_WAIT_MS = 140;
const MIN_NON_EMPTY_PNG_DATA_URL_LENGTH = 10000;
const CAPTURE_RETRY_COUNT = 20;
const CAPTURE_RETRY_WAIT_MS = 180;

const CAMERA_PRESET = {
  position: [-0.16, 0.3, 0.62],
  target: [0, 0.14, 0],
  fov: 40,
  minDistance: 0.45,
  maxDistance: 0.8,
};

const POSES = [
  {
    id: 'idle',
    label: 'Idle',
    description: '默认待机姿态，做桌宠主循环底帧。',
    headJoints: [0, 0, 0, 0, 0, 0, 0],
    antennas: [0.08, -0.08],
  },
  {
    id: 'listen',
    label: 'Listen',
    description: '轻微抬头并竖起耳朵，适合监听中状态。',
    headJoints: [0.05, 0.02, -0.015, 0.015, -0.02, 0.012, -0.01],
    antennas: [0.22, -0.22],
  },
  {
    id: 'think',
    label: 'Think',
    description: '轻微偏头，适合思考和处理中状态。',
    headJoints: [0.11, 0.018, -0.025, 0.024, -0.012, 0.006, -0.01],
    antennas: [0.02, -0.16],
  },
  {
    id: 'speak',
    label: 'Speak',
    description: '轻微前倾，适合说话或播报状态。',
    headJoints: [0, -0.028, 0.016, -0.016, 0.02, -0.012, 0.012],
    antennas: [0.14, -0.06],
  },
  {
    id: 'sleep',
    label: 'Sleep',
    description: '低头休眠姿态，适合待机与安静场景。',
    headJoints: [0, -0.08, 0.038, -0.03, 0.03, -0.02, 0.018],
    antennas: [-0.22, 0.22],
  },
  {
    id: 'drag',
    label: 'Drag',
    description: '被拖拽时的微偏头姿态，可作为抓取反馈。',
    headJoints: [0.16, 0.01, -0.01, 0.01, -0.01, 0.005, -0.005],
    antennas: [0, 0],
  },
];

function isTauriRuntime() {
  return Boolean(window.__TAURI__?.core?.invoke) && !window.mockGetCurrentWindow;
}

function isAutoExportRequested() {
  const params = new URLSearchParams(window.location.search);
  return params.get('sprite-auto-export') === '1';
}

function wait(ms) {
  return new Promise(resolve => {
    window.setTimeout(resolve, ms);
  });
}

function nextFrame() {
  return new Promise(resolve => {
    window.requestAnimationFrame(() => resolve());
  });
}

function dataUrlToBlob(dataUrl) {
  const [header, payload] = dataUrl.split(',');
  const mimeMatch = header.match(/data:(.*?);base64/);
  const mime = mimeMatch?.[1] || 'image/png';
  const binary = window.atob(payload);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mime });
}

function downloadDataUrl(fileName, dataUrl) {
  const blob = dataUrlToBlob(dataUrl);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.setTimeout(() => URL.revokeObjectURL(url), 500);
}

export default function SpriteExporter() {
  const darkMode = useAppStore(state => state.darkMode);
  const canvasHostRef = useRef(null);
  const autoExportStartedRef = useRef(false);
  const [activePoseId, setActivePoseId] = useState(POSES[0].id);
  const [isExporting, setIsExporting] = useState(false);
  const [status, setStatus] = useState(null);
  const [capturedFrames, setCapturedFrames] = useState([]);
  const [automationResult, setAutomationResult] = useState(null);

  const activePose = useMemo(
    () => POSES.find(pose => pose.id === activePoseId) ?? POSES[0],
    [activePoseId]
  );

  const getCanvas = useCallback(() => {
    return canvasHostRef.current?.querySelector('canvas') ?? null;
  }, []);

  const captureCanvasDataUrl = useCallback(async () => {
    const canvas = getCanvas();
    if (!canvas) {
      throw new Error('没有找到 3D 画布，当前帧无法导出。');
    }

    for (let attempt = 0; attempt < CAPTURE_RETRY_COUNT; attempt += 1) {
      const dataUrl = canvas.toDataURL('image/png');
      if (dataUrl.length >= MIN_NON_EMPTY_PNG_DATA_URL_LENGTH) {
        return dataUrl;
      }

      await nextFrame();
      await wait(CAPTURE_RETRY_WAIT_MS);
    }

    return canvas.toDataURL('image/png');
  }, [getCanvas]);

  const captureActivePose = useCallback(
    async pose => {
      setActivePoseId(pose.id);
      await nextFrame();
      await nextFrame();
      await wait(FRAME_WAIT_MS);

      const dataUrl = await captureCanvasDataUrl();
      return {
        id: pose.id,
        label: pose.label,
        fileName: `${pose.id}.png`,
        dataUrl,
      };
    },
    [captureCanvasDataUrl]
  );

  const captureAllFrames = useCallback(async () => {
    const frames = [];
    for (const pose of POSES) {
      const frame = await captureActivePose(pose);
      frames.push(frame);
    }
    return frames;
  }, [captureActivePose]);

  const exportAllFrames = useCallback(async () => {
    setIsExporting(true);
    setStatus({
      type: 'info',
      message: '正在抓取 6 个动作帧，请稍等……',
    });

    try {
      const frames = await captureAllFrames();

      setCapturedFrames(frames);

      if (isTauriRuntime()) {
        const result = await window.__TAURI__.core.invoke('export_sprite_frames', {
          exportName: `reachy-pet-${new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-')}`,
          frames: frames.map(frame => ({
            fileName: frame.fileName,
            label: frame.label,
            poseId: frame.id,
            dataUrl: frame.dataUrl,
          })),
        });

        setStatus({
          type: 'success',
          message: `6 张 PNG 已导出到 ${result.outputDir}`,
        });
        return;
      }

      frames.forEach(frame => {
        downloadDataUrl(frame.fileName, frame.dataUrl);
      });

      setStatus({
        type: 'success',
        message: '6 张 PNG 已通过浏览器下载导出。',
      });
    } catch (error) {
      setStatus({
        type: 'error',
        message: error?.message || '导出失败，请重试。',
      });
    } finally {
      setIsExporting(false);
    }
  }, [captureAllFrames]);

  useEffect(() => {
    window.__spriteExport = {
      poses: POSES.map(({ id, label, description }) => ({ id, label, description })),
      captureAllFrames: async () => {
        const frames = await captureAllFrames();
        setCapturedFrames(frames);
        return frames;
      },
      capturePoseById: async poseId => {
        const pose = POSES.find(item => item.id === poseId);
        if (!pose) {
          throw new Error(`Unknown pose: ${poseId}`);
        }
        const frame = await captureActivePose(pose);
        setCapturedFrames(current => {
          const next = current.filter(item => item.id !== frame.id);
          next.push(frame);
          return next;
        });
        return frame;
      },
    };

    return () => {
      delete window.__spriteExport;
    };
  }, [captureActivePose, captureAllFrames]);

  useEffect(() => {
    if (!isAutoExportRequested() || autoExportStartedRef.current) {
      return;
    }

    autoExportStartedRef.current = true;
    setIsExporting(true);
    setStatus({
      type: 'info',
      message: '自动导出模式已启动，正在抓取 6 个动作帧……',
    });

    captureAllFrames()
      .then(frames => {
        setCapturedFrames(frames);
        setAutomationResult({
          ok: true,
          exportedAt: new Date().toISOString(),
          frames: frames.map(frame => ({
            id: frame.id,
            label: frame.label,
            fileName: frame.fileName,
            dataUrl: frame.dataUrl,
          })),
        });
        setStatus({
          type: 'success',
          message: '自动导出完成。',
        });
      })
      .catch(error => {
        setAutomationResult({
          ok: false,
          error: error?.message || '自动导出失败',
        });
        setStatus({
          type: 'error',
          message: error?.message || '自动导出失败',
        });
      })
      .finally(() => {
        setIsExporting(false);
      });
  }, [captureAllFrames]);

  return (
    <Box
      sx={{
        minHeight: '100vh',
        width: '100%',
        background: darkMode
          ? 'radial-gradient(circle at top, rgba(255,149,0,0.12), transparent 38%), #0f1115'
          : 'radial-gradient(circle at top, rgba(255,149,0,0.12), transparent 36%), #f5f7fb',
        color: darkMode ? '#fff' : '#111827',
        p: { xs: 2, md: 3 },
      }}
    >
      <Stack spacing={3}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 800, mb: 1 }}>
            Reachy 桌宠帧导出器
          </Typography>
          <Typography
            sx={{
              maxWidth: 860,
              color: darkMode ? 'rgba(255,255,255,0.75)' : 'rgba(17,24,39,0.72)',
              lineHeight: 1.7,
            }}
          >
            这个页面会复用现在的 3D 模型，用固定相机和透明背景把 6 个桌宠动作直接抓成 2D PNG。
            第一版先导单帧，后面你要拼 sprite sheet 或继续补中间帧，就直接在这套姿态上扩。
          </Typography>
        </Box>

        {status && (
          <Alert severity={status.type} variant="filled">
            {status.message}
          </Alert>
        )}

        <Stack direction={{ xs: 'column', xl: 'row' }} spacing={3} alignItems="stretch">
          <Card
            sx={{
              flex: '0 0 360px',
              borderRadius: 3,
              bgcolor: darkMode ? 'rgba(20,23,29,0.92)' : 'rgba(255,255,255,0.9)',
              border: darkMode
                ? '1px solid rgba(255,255,255,0.08)'
                : '1px solid rgba(15,23,42,0.08)',
              backdropFilter: 'blur(14px)',
            }}
          >
            <CardContent>
              <Stack spacing={2}>
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 800, mb: 0.75 }}>
                    动作预设
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{ color: darkMode ? 'rgba(255,255,255,0.66)' : 'rgba(17,24,39,0.62)' }}
                  >
                    先点选左侧姿态看预览，再一键导出全部 6 帧。
                  </Typography>
                </Box>

                <Stack spacing={1.25}>
                  {POSES.map(pose => {
                    const selected = pose.id === activePoseId;
                    return (
                      <Button
                        key={pose.id}
                        variant={selected ? 'contained' : 'outlined'}
                        color={selected ? 'primary' : 'inherit'}
                        startIcon={selected ? <CheckCircleRoundedIcon /> : <ImageOutlinedIcon />}
                        onClick={() => setActivePoseId(pose.id)}
                        sx={{
                          justifyContent: 'flex-start',
                          py: 1.3,
                          px: 1.5,
                          borderRadius: 2.5,
                        }}
                      >
                        <Box sx={{ textAlign: 'left' }}>
                          <Typography sx={{ fontWeight: 700 }}>{pose.label}</Typography>
                          <Typography
                            variant="caption"
                            sx={{
                              display: 'block',
                              opacity: selected ? 0.92 : 0.7,
                              lineHeight: 1.5,
                              whiteSpace: 'normal',
                            }}
                          >
                            {pose.description}
                          </Typography>
                        </Box>
                      </Button>
                    );
                  })}
                </Stack>

                <Divider />

                <Stack spacing={1.25}>
                  <Button
                    variant="contained"
                    size="large"
                    startIcon={
                      isExporting ? (
                        <CircularProgress color="inherit" size={18} />
                      ) : (
                        <DownloadRoundedIcon />
                      )
                    }
                    onClick={exportAllFrames}
                    disabled={isExporting}
                    sx={{
                      py: 1.4,
                      fontWeight: 800,
                    }}
                  >
                    {isExporting ? '导出中…' : '一键导出 6 帧 PNG'}
                  </Button>

                  <Typography
                    variant="caption"
                    sx={{ color: darkMode ? 'rgba(255,255,255,0.62)' : 'rgba(17,24,39,0.56)' }}
                  >
                    桌面端会直接写入 `.codex-runtime/sprite-export/`；浏览器模式会改为下载 6 张
                    PNG。
                  </Typography>
                </Stack>
              </Stack>
            </CardContent>
          </Card>

          <Card
            sx={{
              flex: 1,
              borderRadius: 3,
              bgcolor: darkMode ? 'rgba(20,23,29,0.92)' : 'rgba(255,255,255,0.9)',
              border: darkMode
                ? '1px solid rgba(255,255,255,0.08)'
                : '1px solid rgba(15,23,42,0.08)',
              backdropFilter: 'blur(14px)',
            }}
          >
            <CardContent sx={{ height: '100%' }}>
              <Stack spacing={2} sx={{ height: '100%' }}>
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 800, mb: 0.75 }}>
                    当前预览
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{ color: darkMode ? 'rgba(255,255,255,0.66)' : 'rgba(17,24,39,0.62)' }}
                  >
                    当前动作：{activePose.label}。导出时会按同一取景顺序抓完所有动作。
                  </Typography>
                </Box>

                <Box
                  ref={canvasHostRef}
                  sx={{
                    height: 720,
                    maxHeight: '70vh',
                    minHeight: 560,
                    borderRadius: 3,
                    overflow: 'hidden',
                    background:
                      'linear-gradient(180deg, rgba(255,149,0,0.10) 0%, rgba(255,149,0,0.03) 48%, rgba(0,0,0,0) 100%)',
                    border: darkMode
                      ? '1px solid rgba(255,255,255,0.06)'
                      : '1px solid rgba(15,23,42,0.08)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Box
                    sx={{
                      width: '100%',
                      height: '100%',
                      maxWidth: 820,
                      maxHeight: 820,
                    }}
                  >
                    <Viewer3D
                      isActive={false}
                      forceLoad={true}
                      hideControls={true}
                      hideGrid={true}
                      hideBorder={true}
                      backgroundColor="transparent"
                      headJoints={activePose.headJoints}
                      antennas={activePose.antennas}
                      cameraPreset={CAMERA_PRESET}
                      canvasScale={1.04}
                      canvasTranslateY="2%"
                      hideEffects={true}
                    />
                  </Box>
                </Box>

                <Divider />

                <Box>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                    已捕获帧
                  </Typography>
                  {capturedFrames.length > 0 ? (
                    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                      {capturedFrames.map(frame => (
                        <Chip
                          key={frame.id}
                          label={frame.fileName}
                          color="success"
                          variant="outlined"
                        />
                      ))}
                    </Stack>
                  ) : (
                    <Typography
                      variant="body2"
                      sx={{ color: darkMode ? 'rgba(255,255,255,0.58)' : 'rgba(17,24,39,0.56)' }}
                    >
                      还没有导出。点击左侧按钮后，这里会显示已经抓到的 6 张 PNG。
                    </Typography>
                  )}
                </Box>
              </Stack>
            </CardContent>
          </Card>
        </Stack>

        {automationResult && (
          <textarea
            readOnly
            value={JSON.stringify(automationResult)}
            data-testid="sprite-export-result"
            style={{
              position: 'absolute',
              left: '-99999px',
              top: 0,
              width: '1px',
              height: '1px',
              opacity: 0,
              pointerEvents: 'none',
            }}
          />
        )}
      </Stack>
    </Box>
  );
}
