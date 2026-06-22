document.addEventListener("DOMContentLoaded", () => {
    const THREAD_ID = "app:main";
    const queryParams = new URLSearchParams(window.location.search);
    const isDesktopPetView = queryParams.get("view") === "desktop-pet";
    const status = document.getElementById("status");
    const statusDot = document.getElementById("status-dot");
    const appLayout = document.getElementById("app-layout");
    const petModeStack = document.getElementById("pet-mode-stack");
    const petStageCard = document.getElementById("pet-stage-card");
    const petSprite = document.getElementById("pet-sprite");
    const petFigure = document.getElementById("pet-figure");
    const petSpeechBubble = document.getElementById("pet-speech-bubble");
    const consolePetBubble = document.getElementById("console-pet-bubble");
    const consolePetSprite = document.getElementById("console-pet-sprite");
    const consolePetFigure = document.getElementById("console-pet-figure");
    const avatarLive2dStage = document.getElementById("avatar-live2d-stage");
    const avatarLive2dCanvas = document.getElementById("avatar-live2d-canvas");
    const avatarLive2dPlaceholder = document.getElementById("avatar-live2d-placeholder");
    const consolePetRuntimeChip = document.getElementById("console-pet-runtime-chip");
    const consolePetAttentionChip = document.getElementById("console-pet-attention-chip");
    const petStateLabel = document.getElementById("pet-state-label");
    const petStateCopy = document.getElementById("pet-state-copy");
    const petRuntimeChip = document.getElementById("pet-runtime-chip");
    const petAttentionChip = document.getElementById("pet-attention-chip");
    const petConnectionText = document.getElementById("pet-connection-text");
    const petVoiceText = document.getElementById("pet-voice-text");
    const petVisionText = document.getElementById("pet-vision-text");
    const petLastUpdated = document.getElementById("pet-last-updated");
    const petFocusInputButton = document.getElementById("pet-focus-input");
    const chatCard = document.getElementById("chat-card");
    const chatTitle = document.getElementById("chat-title");
    const chatSubtitle = document.getElementById("chat-subtitle");
    const chatLog = document.getElementById("chat-log");
    const chatForm = document.getElementById("chat-form");
    const messageInput = document.getElementById("message-input");
    const sendButton = document.getElementById("send-button");
    const micButton = document.getElementById("mic-button");
    const micStatus = document.getElementById("mic-status");
    const composerHint = document.getElementById("composer-hint");
    const speechPreview = document.getElementById("speech-preview");
    const cameraPreview = document.getElementById("camera-preview");
    const cameraOverlay = document.getElementById("camera-overlay");
    const cameraOverlayCanvas = document.getElementById("camera-overlay-canvas");
    const detectionBox = document.getElementById("detection-box");
    const detectionLabel = document.getElementById("detection-label");
    const cameraPlaceholder = document.getElementById("camera-placeholder");
    const cameraStatus = document.getElementById("camera-status");
    const cameraToggle = document.getElementById("camera-toggle");
    const cameraTargetChip = document.getElementById("camera-target-chip");
    const cameraFps = document.getElementById("camera-fps");
    const cameraDirection = document.getElementById("camera-direction");
    const cameraYaw = document.getElementById("camera-yaw");
    const cameraPitch = document.getElementById("camera-pitch");
    const cameraDistance = document.getElementById("camera-distance");
    const cameraIdentity = document.getElementById("camera-identity");
    const visionStatus = document.getElementById("vision-status");
    const visionSource = document.getElementById("vision-source");
    const visionDirectionCard = document.getElementById("vision-direction-card");
    const visionDirectionLabel = document.getElementById("vision-direction-label");
    const visionDirectionSubtitle = document.getElementById("vision-direction-subtitle");
    const visionEventName = document.getElementById("vision-event-name");
    const visionTrackingEnabled = document.getElementById("vision-tracking-enabled");
    const visionEmotion = document.getElementById("vision-emotion");
    const visionIdentity = document.getElementById("vision-identity");
    const visionReleaseReason = document.getElementById("vision-release-reason");
    const visionLastUpdated = document.getElementById("vision-last-updated");
    const visionLog = document.getElementById("vision-log");
    const visionLogEmpty = document.getElementById("vision-log-empty");
    const emotionCompareSummary = document.getElementById("emotion-compare-summary");
    const emotionCompareGrid = document.getElementById("emotion-compare-grid");
    const identityConfidence = document.getElementById("identity-confidence");
    const footerFps = document.getElementById("footer-fps");
    const footerResolution = document.getElementById("footer-resolution");
    const footerConfidence = document.getElementById("footer-confidence");
    const turnViews = new Map();
    const RecognitionCtor =
        window.SpeechRecognition || window.webkitSpeechRecognition || null;
    const recognitionSupported = typeof RecognitionCtor === "function";
    const cameraSupported =
        Boolean(navigator.mediaDevices) &&
        typeof navigator.mediaDevices.getUserMedia === "function";
    const DESKTOP_PET_POSITION_KEY = "reachy-mini.desktop-pet.position.v4";
    const DESKTOP_PET_MARGIN = 24;
    const CAMERA_FRAME_INTERVAL_MS = 200;
    const CAMERA_FRAME_WIDTH = 320;
    const CAMERA_FRAME_HEIGHT = 180;
    const CAMERA_FRAME_QUALITY = 0.55;
    const PRIMARY_EMOTION_MODEL = "POSTER-Var";
    const EMOTION_LABELS_BY_MODEL = Object.freeze({
        "POSTER-Var": ["Neutral", "Happy", "Sad", "Surprise", "Fear", "Disgust", "Anger", "Contempt"],
        default: ["Neutral", "Happy", "Sad", "Surprise", "Fear", "Disgust", "Anger", "Contempt"],
    });
    const EMOTION_LABELS_ZH = Object.freeze({
        Anger: "生气",
        Contempt: "轻蔑",
        Disgust: "厌恶",
        Fear: "害怕",
        Happy: "开心",
        Neutral: "平静",
        Sad: "难过",
        Surprise: "惊讶",
    });
    const PET_SPRITES = Object.freeze({
        idle: "/static/assets/desktop-pet/idle.png?v=20260331-desktop-pet-3",
        listen: "/static/assets/desktop-pet/listen.png?v=20260331-desktop-pet-3",
        think: "/static/assets/desktop-pet/think.png?v=20260331-desktop-pet-3",
        speak: "/static/assets/desktop-pet/speak.png?v=20260331-desktop-pet-3",
        sleep: "/static/assets/desktop-pet/sleep.png?v=20260331-desktop-pet-3",
        drag: "/static/assets/desktop-pet/drag.png?v=20260331-desktop-pet-3",
    });
    const AVATAR_CONFIG_URL = "/static/avatar.config.json?v=20260621-live2d-visible-actions-3";
    const AVATAR_MODES = Object.freeze(["sprite", "live2d"]);
    const LIVE2D_DEBUG_PARAMETER_IDS = Object.freeze([
        "Param58",
        "Param59",
        "Param31",
        "Param32",
        "Param33",
        "Param34",
        "Param35",
        "Param36",
        "Param37",
        "Param41",
        "Param53",
        "Param60",
        "ParamBrowLForm",
        "ParamBrowRForm",
        "ParamMouthForm",
        "ParamEyeLOpen",
        "ParamEyeROpen",
    ]);

    let socket = null;
    let socketReady = false;
    let runtimeReady = false;
    let reconnectTimer = null;
    let recognition = null;
    let recognitionActive = false;
    let recognitionFinalText = "";
    let recognitionInterimText = "";
    let recognitionError = "";
    let speechLifecycleActive = false;
    let lastPartialSentText = "";
    let lastStoppedText = "";
    let speechCaptureEnded = false;
    let turnCompleted = false;
    let cameraStream = null;
    let cameraActive = false;
    let cameraFrameTimer = null;
    let cameraFrameCanvas = null;
    let latestVisionOverlay = null;
    let lastVisionLogKey = "";
    let petIdleTimer = null;
    let desktopPetLayer = null;
    let petShell = null;
    let desktopPetDrag = null;
    let desktopPetHoverActive = false;
    let desktopPetChatHideTimer = null;
    let consolePetMotionTimer = null;
    let avatarConfig = {};
    let avatarMode = "sprite";
    let live2dApp = null;
    let live2dModel = null;
    let live2dResizeHandler = null;
    let live2dManifest = null;
    let live2dDebugUnsubscribe = null;
    let live2dDebugEventHandler = null;
    let live2dMotionResetTimer = null;
    let live2dExpressionResetTimer = null;
    let live2dActiveExpressions = new Set();
    let live2dExpressionUpdateHandler = null;
    let live2dMotionUpdateHandler = null;
    let live2dModelUpdateHandler = null;
    let live2dActiveMotion = null;
    let live2dMotionTicker = null;
    let live2dUpdateTick = 0;
    let live2dLastMotionStartedAtMs = 0;
    const DETECTION_BOX_SCALE_X = 1.14;
    const DETECTION_BOX_SCALE_Y = 1.18;

    function compactText(text) {
        return String(text || "")
            .replace(/\s+/g, " ")
            .trim();
    }

    function joinText(left, right) {
        const normalizedLeft = compactText(left);
        const normalizedRight = compactText(right);
        if (!normalizedLeft) {
            return normalizedRight;
        }
        if (!normalizedRight) {
            return normalizedLeft;
        }
        return `${normalizedLeft} ${normalizedRight}`;
    }

    function currentRecognitionText() {
        return joinText(recognitionFinalText, recognitionInterimText);
    }

    function isSocketOpen() {
        return Boolean(socket && socket.readyState === WebSocket.OPEN);
    }

    function setStatus(text, ready) {
        status.textContent = text;
        statusDot.dataset.ready = ready ? "true" : "false";
        if (petConnectionText) {
            petConnectionText.textContent = text;
        }
        if (avatarMode === "sprite") {
            setPetRuntimeState(ready ? "已连接" : "连接中", ready ? "ready" : "starting");
        }
    }

    function setMicStatus(text, state = "idle") {
        if (!micStatus) {
            return;
        }
        micStatus.textContent = text;
        micStatus.dataset.state = state;
        if (petVoiceText) {
            petVoiceText.textContent = text;
        }
    }

    function setSpeechPreview(text) {
        if (!speechPreview) {
            return;
        }
        const normalized = compactText(text);
        speechPreview.hidden = !normalized;
        speechPreview.textContent = normalized ? `识别中：${normalized}` : "";
    }

    function setCameraStatus(text, state = "idle") {
        if (!cameraStatus) {
            return;
        }
        cameraStatus.textContent = text;
        cameraStatus.dataset.state = state;
    }

    function setVisionStatus(text, state = "idle") {
        if (!visionStatus) {
            return;
        }
        visionStatus.textContent = text;
        visionStatus.dataset.state = state;
        if (petVisionText) {
            petVisionText.textContent = text;
        }
    }

    function formatClockTime(value) {
        const date = value instanceof Date ? value : new Date(value);
        if (Number.isNaN(date.getTime())) {
            return "未知";
        }
        return new Intl.DateTimeFormat("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        }).format(date);
    }

    function truncatePetText(text, maxLength = 88) {
        const normalized = compactText(text);
        if (!normalized) {
            return "";
        }
        if (normalized.length <= maxLength) {
            return normalized;
        }
        return `${normalized.slice(0, maxLength - 1)}…`;
    }

    function summarizePetSpeech(text) {
        const normalized = compactText(text);
        if (!normalized) {
            return "";
        }

        if (normalized.length <= 42) {
            return normalized;
        }

        const lower = normalized.toLowerCase();
        if (lower.includes("error") || normalized.includes("失败") || normalized.includes("出错")) {
            return "动作遇到问题，我把详情放在聊天记录里。";
        }
        if (normalized.includes("抱歉") || normalized.includes("对不起")) {
            return "抱歉，我把完整说明放在聊天记录里。";
        }
        if (normalized.includes("可以") || normalized.includes("方案") || normalized.includes("建议")) {
            return "我整理了一个方案，详情在下面。";
        }
        if (normalized.includes("看到") || normalized.includes("检测") || normalized.includes("识别")) {
            return "我看到了一些信息，详情在下面。";
        }

        return "我有一段较长回复，完整内容在下面。";
    }

    function shouldShowAntennaMotion(text) {
        const normalized = compactText(text).toLowerCase();
        if (!normalized) {
            return false;
        }
        return (
            normalized.includes("set_antenna") ||
            normalized.includes("antenna") ||
            normalized.includes("耳朵") ||
            normalized.includes("天线") ||
            normalized.includes("摆摆") ||
            normalized.includes("动完")
        );
    }

    function normalizeAvatarMode(mode) {
        const normalized = String(mode || "").toLowerCase().trim();
        return AVATAR_MODES.includes(normalized) ? normalized : "sprite";
    }

    function numberOrDefault(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function setElementHidden(element, hidden) {
        if (element) {
            element.hidden = hidden;
        }
    }

    function setLive2dPlaceholderVisible(visible) {
        setElementHidden(avatarLive2dPlaceholder, !visible);
        setElementHidden(avatarLive2dCanvas, visible);
    }

    function resetLive2dRenderer() {
        if (live2dMotionResetTimer !== null) {
            window.clearTimeout(live2dMotionResetTimer);
            live2dMotionResetTimer = null;
        }
        if (live2dExpressionResetTimer !== null) {
            window.clearTimeout(live2dExpressionResetTimer);
            live2dExpressionResetTimer = null;
        }
        live2dActiveExpressions = new Set();
        live2dActiveMotion = null;
        stopLive2dNativeMotionDriver();
        live2dLastMotionStartedAtMs = 0;
        live2dUpdateTick = 0;
        if (live2dDebugUnsubscribe) {
            live2dDebugUnsubscribe();
            live2dDebugUnsubscribe = null;
        }
        if (live2dExpressionUpdateHandler && live2dModel) {
            live2dModel.internalModel?.off?.("beforeModelUpdate", live2dExpressionUpdateHandler);
            live2dExpressionUpdateHandler = null;
        }
        if (live2dMotionUpdateHandler && live2dModel) {
            live2dModel.internalModel?.off?.("afterMotionUpdate", live2dMotionUpdateHandler);
            live2dMotionUpdateHandler = null;
        }
        if (live2dModelUpdateHandler && live2dModel) {
            live2dModel.internalModel?.off?.("beforeModelUpdate", live2dModelUpdateHandler);
            live2dModelUpdateHandler = null;
        }
        if (live2dDebugEventHandler) {
            window.removeEventListener("reachy-live2d-debug-action", live2dDebugEventHandler);
            live2dDebugEventHandler = null;
        }
        if (window.__reachyLive2dDebug) {
            delete window.__reachyLive2dDebug;
        }
        if (window.__reachyLive2dDebugModel) {
            delete window.__reachyLive2dDebugModel;
        }
        if (live2dResizeHandler) {
            window.removeEventListener("resize", live2dResizeHandler);
            live2dResizeHandler = null;
        }
        if (live2dApp) {
            live2dApp.destroy(true, { children: true, texture: false, baseTexture: false });
            live2dApp = null;
        }
        live2dModel = null;
        live2dManifest = null;
        if (avatarLive2dCanvas) {
            // Do not call getContext("2d") here: a canvas cannot switch from 2D to WebGL later.
            avatarLive2dCanvas.width = avatarLive2dCanvas.width;
        }
    }

    function fitLive2dModel() {
        if (!live2dApp || !live2dModel || !avatarLive2dStage) {
            return;
        }

        const rect = avatarLive2dStage.getBoundingClientRect();
        const width = Math.max(1, Math.floor(rect.width));
        const height = Math.max(1, Math.floor(rect.height));
        live2dApp.renderer.resize(width, height);

        const bounds = live2dModel.getLocalBounds();
        const modelWidth = Math.max(1, bounds.width);
        const modelHeight = Math.max(1, bounds.height);
        const live2dOptions = avatarConfig?.live2d || {};
        const scale = Math.min(width / modelWidth, height / modelHeight) *
            numberOrDefault(live2dOptions.zoom, 1);
        const offsetX = numberOrDefault(live2dOptions.offset_x, 0);
        const offsetY = numberOrDefault(live2dOptions.offset_y, 0);
        live2dModel.scale.set(scale);
        live2dModel.x = width * (0.5 + offsetX);
        live2dModel.y = height * (0.5 + offsetY);
    }

    function joinLive2dPath(root, file) {
        const cleanRoot = String(root || "").replace(/\/+$/, "");
        const cleanFile = String(file || "").replace(/^\/+/, "");
        return `${cleanRoot}/${cleanFile}`;
    }

    async function fetchJson(url) {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) {
            throw new Error(`${url} ${response.status}`);
        }
        return response.json();
    }

    function stripLive2dExtension(file) {
        return String(file || "")
            .replace(/\.motion3\.json$/i, "")
            .replace(/\.exp3\.json$/i, "");
    }

    function buildLive2dModelSettings(modelSettings, vtubeSettings, root) {
        const normalized = JSON.parse(JSON.stringify(modelSettings || {}));
        const fileReferences = normalized.FileReferences || {};
        const hotkeys = Array.isArray(vtubeSettings?.Hotkeys) ? vtubeSettings.Hotkeys : [];
        const motions = {};
        const motionFiles = {};
        const motionDurations = {};
        const motionLoops = {};
        const motionOptions = {};
        const expressions = [];
        const expressionFiles = {};
        const expressionOptions = {};
        const seenExpressions = new Set();
        const idleAnimation = String(vtubeSettings?.FileReferences?.IdleAnimation || "").trim();

        if (idleAnimation) {
            const name = stripLive2dExtension(idleAnimation);
            motions[name] = [{ File: idleAnimation }];
            motionFiles[name] = idleAnimation;
            motionDurations[name] = 0;
            motionLoops[name] = false;
            motionOptions[name] = {
                fadeSeconds: 0,
                deactivateAfterSeconds: false,
                deactivateAfterSecondsAmount: 0,
                stopsOnLastFrame: false,
            };
        }

        for (const hotkey of hotkeys) {
            const action = String(hotkey?.Action || "");
            const file = String(hotkey?.File || "").trim();
            if (!file) {
                continue;
            }
            const name = stripLive2dExtension(file);
            if (action === "TriggerAnimation") {
                const fadeSeconds = Number(hotkey?.FadeSecondsAmount || 0);
                motions[name] = [{
                    File: file,
                    FadeInTime: fadeSeconds,
                    FadeOutTime: fadeSeconds,
                }];
                motionFiles[name] = file;
                motionDurations[name] = Number(hotkey?.Duration || 0);
                motionLoops[name] = false;
                motionOptions[name] = {
                    fadeSeconds,
                    deactivateAfterSeconds: Boolean(hotkey?.DeactivateAfterSeconds),
                    deactivateAfterSecondsAmount: Number(hotkey?.DeactivateAfterSecondsAmount || 0),
                    stopsOnLastFrame: Boolean(hotkey?.StopsOnLastFrame),
                };
                continue;
            }
            if (action === "ToggleExpression" && !seenExpressions.has(name)) {
                const fadeSeconds = Number(hotkey?.FadeSecondsAmount || 0);
                expressions.push({ Name: name, File: file, FadeInTime: fadeSeconds, FadeOutTime: fadeSeconds });
                expressionFiles[name] = file;
                expressionOptions[name] = {
                    fadeSeconds,
                    deactivateAfterKeyUp: Boolean(hotkey?.DeactivateAfterKeyUp),
                    deactivateAfterSeconds: Boolean(hotkey?.DeactivateAfterSeconds),
                    deactivateAfterSecondsAmount: Number(hotkey?.DeactivateAfterSecondsAmount || 0),
                };
                seenExpressions.add(name);
            }
        }

        fileReferences.Motions = motions;
        fileReferences.Expressions = expressions;
        normalized.FileReferences = fileReferences;
        normalized.url = joinLive2dPath(root, vtubeSettings?.FileReferences?.Model || "model3.json");
        return {
            settings: normalized,
            manifest: {
                model: vtubeSettings?.FileReferences?.Model || "",
                idle: stripLive2dExtension(idleAnimation),
                motions: Object.keys(motions),
                motionFiles,
                motionDurations,
                motionLoops,
                motionOptions,
                expressions: expressions.map((item) => item.Name),
                expressionFiles,
                expressionOptions,
            },
        };
    }

    async function readLive2dMotionMetadata(root, manifest) {
        const motionFiles = manifest?.motionFiles || {};
        const durations = { ...(manifest?.motionDurations || {}) };
        const loops = { ...(manifest?.motionLoops || {}) };
        const definitions = {};
        const entries = Object.entries(motionFiles);
        await Promise.all(entries.map(async ([name, file]) => {
            if (!file) {
                return;
            }
            try {
                const motion = await fetchJson(joinLive2dPath(root, file));
                if (!durations[name]) {
                    durations[name] = Number(motion?.Meta?.Duration || 0);
                }
                const idleName = String(manifest?.idle || "").trim();
                loops[name] = name === idleName && Boolean(motion?.Meta?.Loop);
                definitions[name] = parseLive2dMotionDefinition(motion);
                if (name !== idleName && definitions[name]) {
                    definitions[name].loop = false;
                }
            } catch (error) {
                console.warn("Failed to read Live2D motion metadata", name, error);
            }
        }));
        return { durations, loops, definitions };
    }

    function parseLive2dMotionDefinition(motion) {
        const meta = motion?.Meta || {};
        const duration = Number(meta.Duration || 0);
        const loop = Boolean(meta.Loop);
        const curves = Array.isArray(motion?.Curves)
            ? motion.Curves
                .filter((curve) => curve?.Target === "Parameter" && curve?.Id)
                .map((curve) => ({
                    id: String(curve.Id),
                    segments: normalizeLive2dMotionSegments(curve.Segments),
                }))
                .filter((curve) => curve.segments.length)
            : [];
        return {
            duration,
            loop,
            curves,
        };
    }

    function normalizeLive2dMotionSegments(rawSegments) {
        if (!Array.isArray(rawSegments) || rawSegments.length < 2) {
            return [];
        }
        const result = [];
        let previous = {
            time: Number(rawSegments[0]),
            value: Number(rawSegments[1]),
        };
        if (!Number.isFinite(previous.time) || !Number.isFinite(previous.value)) {
            return [];
        }
        let index = 2;
        while (index < rawSegments.length) {
            const type = Number(rawSegments[index]);
            if (type === 0 && index + 2 < rawSegments.length) {
                const point = {
                    time: Number(rawSegments[index + 1]),
                    value: Number(rawSegments[index + 2]),
                };
                if (Number.isFinite(point.time) && Number.isFinite(point.value)) {
                    result.push({ type: "linear", from: previous, to: point });
                    previous = point;
                }
                index += 3;
                continue;
            }
            if (type === 1 && index + 6 < rawSegments.length) {
                const c1 = {
                    time: Number(rawSegments[index + 1]),
                    value: Number(rawSegments[index + 2]),
                };
                const c2 = {
                    time: Number(rawSegments[index + 3]),
                    value: Number(rawSegments[index + 4]),
                };
                const point = {
                    time: Number(rawSegments[index + 5]),
                    value: Number(rawSegments[index + 6]),
                };
                if (
                    Number.isFinite(c1.time) &&
                    Number.isFinite(c1.value) &&
                    Number.isFinite(c2.time) &&
                    Number.isFinite(c2.value) &&
                    Number.isFinite(point.time) &&
                    Number.isFinite(point.value)
                ) {
                    result.push({ type: "bezier", from: previous, c1, c2, to: point });
                    previous = point;
                }
                index += 7;
                continue;
            }
            if ((type === 2 || type === 3) && index + 2 < rawSegments.length) {
                const point = {
                    time: Number(rawSegments[index + 1]),
                    value: Number(rawSegments[index + 2]),
                };
                if (Number.isFinite(point.time) && Number.isFinite(point.value)) {
                    result.push({
                        type: type === 2 ? "stepped" : "inverseStepped",
                        from: previous,
                        to: point,
                    });
                    previous = point;
                }
                index += 3;
                continue;
            }
            break;
        }
        return result;
    }

    async function readLive2dExpressionDefinitions(root, manifest) {
        const expressionFiles = manifest?.expressionFiles || {};
        const definitions = {};
        const entries = Object.entries(expressionFiles);
        await Promise.all(entries.map(async ([name, file]) => {
            if (!file) {
                return;
            }
            try {
                const expression = await fetchJson(joinLive2dPath(root, file));
                const parameters = Array.isArray(expression?.Parameters)
                    ? expression.Parameters
                    : [];
                definitions[name] = parameters
                    .map((parameter) => ({
                        id: String(parameter?.Id || "").trim(),
                        value: Number(parameter?.Value || 0),
                        blend: String(parameter?.Blend || "Add").trim() || "Add",
                    }))
                    .filter((parameter) => parameter.id && Number.isFinite(parameter.value));
            } catch (error) {
                console.warn("Failed to read Live2D expression definition", name, error);
            }
        }));
        return definitions;
    }

    async function loadLive2dNativeSettings(live2dOptions) {
        const root = String(live2dOptions?.root || "").replace(/\/+$/, "");
        const vtubeFile = String(live2dOptions?.vtube || "").trim();
        if (!root || !vtubeFile) {
            throw new Error("Live2D root/vtube is missing");
        }

        const vtubeUrl = joinLive2dPath(root, vtubeFile);
        const vtubeSettings = await fetchJson(vtubeUrl);
        const modelFile = String(vtubeSettings?.FileReferences?.Model || "").trim();
        if (!modelFile) {
            throw new Error("VTube settings does not reference a model3 file");
        }

        const modelSettings = await fetchJson(joinLive2dPath(root, modelFile));
        const built = buildLive2dModelSettings(modelSettings, vtubeSettings, root);
        const motionMetadata = await readLive2dMotionMetadata(root, built.manifest);
        built.manifest.motionDurations = motionMetadata.durations;
        built.manifest.motionLoops = motionMetadata.loops;
        built.manifest.motionDefinitions = motionMetadata.definitions;
        built.manifest.expressionDefinitions =
            await readLive2dExpressionDefinitions(root, built.manifest);
        return built;
    }

    function markLive2dManifest(manifest) {
        live2dManifest = manifest || null;
        if (!consolePetFigure) {
            return;
        }
        consolePetFigure.dataset.live2dSource = "vtube";
        consolePetFigure.dataset.live2dIdle = manifest?.idle || "";
        consolePetFigure.dataset.live2dMotions = (manifest?.motions || []).join(",");
        consolePetFigure.dataset.live2dExpressions = (manifest?.expressions || []).join(",");
    }

    function readLive2dRendererMotions() {
        const definitions = live2dModel?.internalModel?.motionManager?.definitions;
        if (!definitions || typeof definitions !== "object" || Array.isArray(definitions)) {
            return [];
        }
        return Object.keys(definitions);
    }

    function readLive2dRendererExpressions() {
        const definitions =
            live2dModel?.internalModel?.motionManager?.expressionManager?.definitions;
        if (!Array.isArray(definitions)) {
            return [];
        }
        return definitions
            .map((item) => String(item?.Name || "").trim())
            .filter(Boolean);
    }

    function markLive2dRendererDefinitions() {
        if (!consolePetFigure) {
            return;
        }
        consolePetFigure.dataset.live2dLoadedMotions =
            readLive2dRendererMotions().join(",");
        consolePetFigure.dataset.live2dLoadedExpressions =
            readLive2dRendererExpressions().join(",");
    }

    function setLive2dActionStatus(kind, name, status, message = "", source = "") {
        if (!consolePetFigure) {
            return;
        }
        consolePetFigure.dataset.live2dLastAction = `${kind}:${name}`;
        consolePetFigure.dataset.live2dLastActionStatus = status;
        if (source) {
            consolePetFigure.dataset.live2dLastActionSource = source.slice(0, 120);
        }
        if (avatarMode === "live2d") {
            const kindLabel = kind === "expression" ? "表情" : "动作";
            const statusLabel =
                status === "pending" ? "准备" :
                    status === "playing" ? "播放中" :
                        status === "ok" ? "完成" :
                            status === "fallback" ? "参数驱动" :
                                status === "missing" ? "未找到" :
                                    status === "error" ? "出错" :
                                        status || "未知";
            const readableName = name || "(empty)";
            const sourceLabel = source ? ` · ${source}` : "";
            setPetRuntimeState(`Live2D ${kindLabel}: ${readableName} ${statusLabel}${sourceLabel}`, status === "error" ? "error" : "ready");
            if (consolePetBubble && status !== "pending") {
                consolePetBubble.textContent = `Live2D ${kindLabel} ${readableName}: ${statusLabel}${sourceLabel}`;
            }
        }
        if (message) {
            consolePetFigure.dataset.live2dLastActionError = message.slice(0, 180);
        } else {
            delete consolePetFigure.dataset.live2dLastActionError;
        }
    }

    function applyLive2dExpressions() {
        if (!live2dModel || !live2dManifest || !live2dActiveExpressions.size) {
            return;
        }
        const coreModel = live2dModel.internalModel?.coreModel;
        const definitions = live2dManifest.expressionDefinitions || {};
        if (!coreModel || typeof coreModel.setParameterValueById !== "function") {
            return;
        }
        for (const name of live2dActiveExpressions) {
            const parameters = definitions[name] || [];
            for (const parameter of parameters) {
                const blend = String(parameter.blend || "Add").toLowerCase();
                if (blend === "multiply") {
                    coreModel.multiplyParameterValueById(parameter.id, parameter.value);
                    continue;
                }
                if (blend === "overwrite" || blend === "replace") {
                    coreModel.setParameterValueById(parameter.id, parameter.value);
                    continue;
                }
                coreModel.addParameterValueById(parameter.id, parameter.value);
            }
        }
    }

    function readLive2dParameterValues(ids = LIVE2D_DEBUG_PARAMETER_IDS) {
        const coreModel = live2dModel?.internalModel?.coreModel;
        const values = {};
        if (!coreModel || typeof coreModel.getParameterValueById !== "function") {
            return values;
        }
        for (const id of ids) {
            try {
                values[id] = Number(coreModel.getParameterValueById(id)).toFixed(4);
            } catch (_error) {
                values[id] = "unavailable";
            }
        }
        return values;
    }

    function markLive2dParameterSnapshot(label = "sample") {
        if (!consolePetFigure) {
            return {};
        }
        const snapshot = readLive2dParameterValues();
        consolePetFigure.dataset.live2dLastParameterSample = label;
        consolePetFigure.dataset.live2dParameterSnapshot = JSON.stringify(snapshot);
        return snapshot;
    }

    function sampleLive2dMotionSegment(segment, seconds) {
        const start = segment.from;
        const end = segment.to;
        if (!start || !end) {
            return 0;
        }
        if (seconds <= start.time) {
            return start.value;
        }
        if (seconds >= end.time) {
            return end.value;
        }
        const span = Math.max(0.0001, end.time - start.time);
        const t = Math.min(Math.max((seconds - start.time) / span, 0), 1);
        if (segment.type === "stepped") {
            return start.value;
        }
        if (segment.type === "inverseStepped") {
            return end.value;
        }
        if (segment.type === "bezier" && segment.c1 && segment.c2) {
            const oneMinusT = 1 - t;
            return (
                oneMinusT ** 3 * start.value +
                3 * oneMinusT ** 2 * t * segment.c1.value +
                3 * oneMinusT * t ** 2 * segment.c2.value +
                t ** 3 * end.value
            );
        }
        return start.value + (end.value - start.value) * t;
    }

    function sampleLive2dMotionCurve(curve, seconds) {
        if (!curve?.segments?.length) {
            return null;
        }
        for (const segment of curve.segments) {
            if (seconds <= segment.to.time) {
                return sampleLive2dMotionSegment(segment, seconds);
            }
        }
        const last = curve.segments[curve.segments.length - 1];
        return last?.to?.value ?? null;
    }

    function live2dMotionVisibleStartOffsetMs(motionName, definition) {
        const idleName = String(live2dManifest?.idle || "").trim();
        if (!motionName || motionName === idleName || !definition?.curves?.length) {
            return 0;
        }
        const candidates = new Set([0.2, 0.45, 0.75, 1.0, 1.35]);
        for (const curve of definition.curves) {
            for (const segment of curve.segments || []) {
                const from = Number(segment?.from?.time);
                const to = Number(segment?.to?.time);
                if (Number.isFinite(from)) {
                    candidates.add(from);
                }
                if (Number.isFinite(to)) {
                    candidates.add(to);
                }
            }
        }
        const duration = Math.max(0.0001, Number(definition.duration || 0));
        let bestSecond = 0;
        let bestScore = -1;
        for (const candidate of candidates) {
            if (!Number.isFinite(candidate) || candidate < 0 || candidate > duration) {
                continue;
            }
            let score = 0;
            for (const curve of definition.curves) {
                const value = sampleLive2dMotionCurve(curve, candidate);
                if (Number.isFinite(value)) {
                    score += Math.abs(value);
                }
            }
            if (score > bestScore) {
                bestScore = score;
                bestSecond = candidate;
            }
        }
        return Math.round(bestSecond * 1000);
    }

    function startLive2dNativeMotionDriver(name) {
        const motionName = String(name || "").trim();
        const definition = live2dManifest?.motionDefinitions?.[motionName];
        stopLive2dNativeMotionDriver();
        if (!motionName || !definition?.curves?.length) {
            live2dActiveMotion = null;
            if (consolePetFigure) {
                consolePetFigure.dataset.live2dNativeDriver = "missing";
            }
            return false;
        }
        const visibleOffsetMs = live2dMotionVisibleStartOffsetMs(motionName, definition);
        live2dActiveMotion = {
            name: motionName,
            definition,
            startedAtMs: performance.now() - visibleOffsetMs,
        };
        if (consolePetFigure) {
            consolePetFigure.dataset.live2dNativeDriver = motionName;
            consolePetFigure.dataset.live2dNativeDriverOffsetMs = String(visibleOffsetMs);
        }
        live2dMotionTicker = () => updateLive2dNativeMotionDriver();
        live2dApp?.ticker?.add?.(live2dMotionTicker);
        return true;
    }

    function stopLive2dNativeMotionDriver() {
        if (live2dMotionTicker && live2dApp?.ticker?.remove) {
            live2dApp.ticker.remove(live2dMotionTicker);
        }
        live2dMotionTicker = null;
        live2dActiveMotion = null;
    }

    function updateLive2dNativeMotionDriver() {
        if (!live2dActiveMotion || !live2dModel) {
            return;
        }
        const coreModel = live2dModel.internalModel?.coreModel;
        if (!coreModel || typeof coreModel.setParameterValueById !== "function") {
            return;
        }
        const { name, definition, startedAtMs } = live2dActiveMotion;
        const duration = Math.max(0.0001, Number(definition.duration || 0));
        let seconds = (performance.now() - startedAtMs) / 1000;
        if (definition.loop) {
            seconds %= duration;
        } else if (seconds > duration) {
            stopLive2dNativeMotionDriver();
            return;
        }
        const written = {};
        for (const curve of definition.curves) {
            const value = sampleLive2dMotionCurve(curve, seconds);
            if (!Number.isFinite(value)) {
                continue;
            }
            coreModel.setParameterValueById(curve.id, value);
            written[curve.id] = Number(value).toFixed(4);
        }
        live2dUpdateTick += 1;
        if (consolePetFigure && live2dUpdateTick % 10 === 0) {
            consolePetFigure.dataset.live2dNativeDriver = name;
            consolePetFigure.dataset.live2dNativeDriverTime = seconds.toFixed(3);
            consolePetFigure.dataset.live2dNativeDriverValues = JSON.stringify(written);
            consolePetFigure.dataset.live2dLastParameterSample = "native_driver_update";
            consolePetFigure.dataset.live2dParameterSnapshot =
                JSON.stringify(readLive2dParameterValues());
        }
    }

    function installLive2dDebugBridge() {
        if (!live2dModel) {
            return;
        }
        window.__reachyLive2dDebugModel = live2dModel;
        window.__reachyLive2dDebug = {
            playMotion: (name) => playLive2dNativeMotion(name),
            applyExpression: (name) => applyLive2dNativeExpression(name),
            sample: (label = "manual") => markLive2dParameterSnapshot(label),
            state: () => ({
                ready: consolePetFigure?.dataset.live2dReady || "",
                loadedMotions: readLive2dRendererMotions(),
                loadedExpressions: readLive2dRendererExpressions(),
                lastAction: consolePetFigure?.dataset.live2dLastAction || "",
                lastActionStatus: consolePetFigure?.dataset.live2dLastActionStatus || "",
                nativeDriver: consolePetFigure?.dataset.live2dNativeDriver || "",
                nativeDriverValues: consolePetFigure?.dataset.live2dNativeDriverValues || "",
                parameters: readLive2dParameterValues(),
            }),
        };
        live2dDebugEventHandler = (event) => {
            const detail = event?.detail || {};
            const type = String(detail.type || "").trim();
            const name = String(detail.name || "").trim();
            if (type === "motion") {
                playLive2dNativeMotion(name);
                return;
            }
            if (type === "expression") {
                applyLive2dNativeExpression(name);
            }
        };
        window.addEventListener("reachy-live2d-debug-action", live2dDebugEventHandler);
        const updateAfterMotion = () => markLive2dParameterSnapshot("after_motion_update");
        const updateBeforeModel = () => markLive2dParameterSnapshot("before_model_update");
        live2dModel.internalModel?.on?.("afterMotionUpdate", updateAfterMotion);
        live2dModel.internalModel?.on?.("beforeModelUpdate", updateBeforeModel);
        live2dDebugUnsubscribe = () => {
            live2dModel?.internalModel?.off?.("afterMotionUpdate", updateAfterMotion);
            live2dModel?.internalModel?.off?.("beforeModelUpdate", updateBeforeModel);
        };
        markLive2dParameterSnapshot("ready");
    }

    function isLive2dLoopMotion(name) {
        const motionName = String(name || "").trim();
        return Boolean(motionName && live2dManifest?.motionLoops?.[motionName]);
    }

    async function configureLive2dMotion(name) {
        const motionName = String(name || "").trim();
        const manager = live2dModel?.internalModel?.motionManager;
        if (!motionName || !manager || typeof manager.loadMotion !== "function") {
            return;
        }
        try {
            const motion = await manager.loadMotion(motionName, 0);
            if (!motion) {
                return;
            }
            const shouldLoop = isLive2dLoopMotion(motionName);
            motion.setIsLoop?.(shouldLoop);
            motion.setIsLoopFadeIn?.(!shouldLoop);
        } catch (error) {
            console.warn("Failed to configure Live2D motion", motionName, error);
        }
    }

    async function configureLive2dMotions() {
        const motionNames = Array.isArray(live2dManifest?.motions)
            ? live2dManifest.motions
            : [];
        await Promise.all(motionNames.map((name) => configureLive2dMotion(name)));
    }

    function startLive2dNativeIdle(options = {}) {
        const idle = live2dManifest?.idle;
        if (!idle || !live2dModel) {
            return;
        }
        const force = Boolean(options.force);
        const currentAction = consolePetFigure?.dataset.live2dLastAction || "";
        const currentStatus = consolePetFigure?.dataset.live2dLastActionStatus || "";
        if (
            !force &&
            currentAction &&
            currentAction !== `motion:${idle}` &&
            ["pending", "playing", "ok"].includes(currentStatus)
        ) {
            return;
        }
        const priority = window.PIXI?.live2d?.MotionPriority?.IDLE ?? 1;
        void configureLive2dMotion(idle)
            .then(() => live2dModel.motion(idle, 0, priority))
            .then((result) => {
                if (result === false) {
                    setLive2dActionStatus("motion", idle, "missing");
                }
                markLive2dParameterSnapshot(`motion:${idle}`);
            })
            .catch((error) => {
                console.warn("Failed to start Live2D native idle", error);
                setLive2dActionStatus("motion", idle, "error", error?.message || String(error));
            });
    }

    function live2dPriority(name, fallback) {
        return window.PIXI?.live2d?.MotionPriority?.[name] ?? fallback;
    }

    function playLive2dNativeMotion(name, options = {}) {
        const motionName = String(name || "").trim();
        const source = String(options.source || "").trim();
        if (!motionName || !live2dModel) {
            setLive2dActionStatus("motion", motionName || "(empty)", "not_ready", "", source);
            return false;
        }
        const priority = live2dPriority("FORCE", 3);
        const durationMs = Math.max(
            800,
            Math.round((Number(live2dManifest?.motionDurations?.[motionName]) || 0) * 1000)
        );
        const isLoopMotion = isLive2dLoopMotion(motionName);
        const idleName = String(live2dManifest?.idle || "").trim();
        const shouldAutoReset = Boolean(motionName && motionName !== idleName && !isLoopMotion);
        if (live2dMotionResetTimer !== null) {
            window.clearTimeout(live2dMotionResetTimer);
            live2dMotionResetTimer = null;
        }
        setLive2dActionStatus("motion", motionName, "pending", "", source);
        if (consolePetFigure) {
            consolePetFigure.dataset.live2dLastMotion = motionName;
        }
        if (options.trackTrigger !== false) {
            live2dLastMotionStartedAtMs = performance.now();
        }
        startLive2dNativeMotionDriver(motionName);
        try {
            live2dModel.internalModel?.motionManager?.stopAllMotions?.();
        } catch (error) {
            console.warn("Failed to stop previous Live2D motion", error);
        }
        window.setTimeout(() => {
            const current = consolePetFigure?.dataset.live2dLastAction || "";
            const status = consolePetFigure?.dataset.live2dLastActionStatus || "";
            if (current === `motion:${motionName}` && status === "pending") {
                setLive2dActionStatus("motion", motionName, "playing", "", source);
                markLive2dParameterSnapshot(`motion:${motionName}:playing`);
            }
        }, 120);
        void configureLive2dMotion(motionName)
            .then(() => live2dModel.motion(motionName, 0, priority))
            .then((result) => {
                if (result === false) {
                    setLive2dActionStatus("motion", motionName, "missing", "", source);
                    return;
                }
                setLive2dActionStatus(
                    "motion",
                    motionName,
                    isLoopMotion ? "playing" : "playing",
                    "",
                    source
                );
                markLive2dParameterSnapshot(`motion:${motionName}`);
                if (shouldAutoReset) {
                    live2dMotionResetTimer = window.setTimeout(() => {
                        live2dMotionResetTimer = null;
                        setLive2dActionStatus("motion", motionName, "ok", "", source);
                        try {
                            live2dModel.internalModel?.motionManager?.stopAllMotions?.();
                        } catch (error) {
                            console.warn("Failed to stop Live2D motion after duration", error);
                        }
                        live2dActiveMotion = null;
                        startLive2dNativeIdle({ force: true });
                    }, durationMs);
                }
            })
            .catch((error) => {
                console.warn("Failed to play Live2D native motion", motionName, error);
                setLive2dActionStatus(
                    "motion",
                    motionName,
                    "error",
                    error?.message || String(error),
                    source
                );
            });
        return true;
    }

    function applyLive2dNativeExpression(name) {
        const expressionName = String(name || "").trim();
        const source = String(arguments[1]?.source || "").trim();
        if (!expressionName || !live2dModel) {
            setLive2dActionStatus("expression", expressionName || "(empty)", "not_ready", "", source);
            return false;
        }
        if (live2dExpressionResetTimer !== null) {
            window.clearTimeout(live2dExpressionResetTimer);
            live2dExpressionResetTimer = null;
        }
        setLive2dActionStatus("expression", expressionName, "pending", "", source);
        const expressionOptions = live2dManifest?.expressionOptions?.[expressionName] || {};
        const clearDelayMs = expressionOptions.deactivateAfterSeconds
            ? Math.max(200, Number(expressionOptions.deactivateAfterSecondsAmount || 0) * 1000)
            : expressionOptions.deactivateAfterKeyUp
                ? 1200
                : 0;
        void Promise.resolve(live2dModel.expression(expressionName))
            .then((result) => {
                if (result === false) {
                    live2dActiveExpressions = new Set([expressionName]);
                } else {
                    live2dActiveExpressions = new Set();
                }
                setLive2dActionStatus("expression", expressionName, result === false ? "fallback" : "ok", "", source);
                window.requestAnimationFrame(() => markLive2dParameterSnapshot(`expression:${expressionName}`));
                if (clearDelayMs > 0) {
                    live2dExpressionResetTimer = window.setTimeout(() => {
                        live2dExpressionResetTimer = null;
                        live2dActiveExpressions.delete(expressionName);
                        live2dModel.internalModel?.motionManager?.expressionManager?.stopAllExpressions?.();
                    }, clearDelayMs);
                }
            })
            .catch((error) => {
                console.warn("Failed to apply Live2D native expression", expressionName, error);
                live2dActiveExpressions = new Set([expressionName]);
                setLive2dActionStatus(
                    "expression",
                    expressionName,
                    "fallback",
                    error?.message || String(error),
                    source
                );
                window.requestAnimationFrame(() => markLive2dParameterSnapshot(`expression:${expressionName}`));
            });
        if (consolePetFigure) {
            consolePetFigure.dataset.live2dLastExpression = expressionName;
        }
        return true;
    }

    async function initLive2dRenderer(live2dOptions) {
        resetLive2dRenderer();
        if (!avatarLive2dCanvas || !avatarLive2dStage) {
            throw new Error("Live2D canvas is missing");
        }
        if (!window.PIXI || !window.PIXI.live2d?.Live2DModel) {
            throw new Error("Live2D runtime is not loaded");
        }

        setLive2dPlaceholderVisible(false);
        if (consolePetFigure) {
            delete consolePetFigure.dataset.live2dError;
        }
        live2dApp = new window.PIXI.Application({
            view: avatarLive2dCanvas,
            autoStart: true,
            resizeTo: avatarLive2dStage,
            backgroundAlpha: 0,
            antialias: true,
        });

        const { settings, manifest } = await loadLive2dNativeSettings(live2dOptions);
        markLive2dManifest(manifest);
        live2dModel = await window.PIXI.live2d.Live2DModel.from(settings, {
            autoInteract: false,
        });
        live2dModel.anchor?.set?.(0.5, 0.5);
        live2dApp.stage.addChild(live2dModel);
        markLive2dRendererDefinitions();
        installLive2dDebugBridge();
        live2dMotionUpdateHandler = () => updateLive2dNativeMotionDriver();
        live2dModel.internalModel?.on?.("afterMotionUpdate", live2dMotionUpdateHandler);
        live2dModelUpdateHandler = () => {
            applyLive2dExpressions();
        };
        live2dModel.internalModel?.on?.("beforeModelUpdate", live2dModelUpdateHandler);
        await configureLive2dMotions();
        fitLive2dModel();
        window.requestAnimationFrame(() => fitLive2dModel());
        live2dResizeHandler = () => fitLive2dModel();
        window.addEventListener("resize", live2dResizeHandler);
    }

    function restoreLive2dConsoleLayout() {
        if (!isDesktopPetView || !appLayout || !chatCard) {
            return;
        }
        if (chatCard.parentElement !== appLayout) {
            const referenceNode = document.getElementById("side-panel");
            appLayout.insertBefore(chatCard, referenceNode || null);
        }
        if (petModeStack && petModeStack.parentElement !== appLayout) {
            appLayout.insertBefore(petModeStack, chatCard);
        }
        if (petModeStack) {
            petModeStack.hidden = true;
        }
        if (petShell) {
            petShell.hidden = true;
            petShell.dataset.chatVisible = "false";
        }
    }

    function applyAvatarMode(nextMode, config = {}) {
        const normalizedMode = normalizeAvatarMode(nextMode);
        const safeConfig = config && typeof config === "object" ? config : {};
        avatarMode = normalizedMode;
        avatarConfig = safeConfig;
        document.body.dataset.avatarMode = normalizedMode;

        if (consolePetFigure) {
            consolePetFigure.dataset.avatarMode = normalizedMode;
        }
        if (petFigure) {
            petFigure.dataset.avatarMode = normalizedMode;
        }

        setElementHidden(consolePetSprite, normalizedMode !== "sprite");
        setElementHidden(avatarLive2dStage, normalizedMode !== "live2d");

        if (normalizedMode === "live2d") {
            restoreLive2dConsoleLayout();
            if (chatCard && isDesktopPetView) {
                chatCard.dataset.open = "true";
            }
            if (consolePetFigure) {
                delete consolePetFigure.dataset.pose;
                delete consolePetFigure.dataset.motion;
            }
            if (petFigure) {
                delete petFigure.dataset.pose;
            }
            const live2dOptions = safeConfig.live2d || {};
            const hasModel = Boolean(
                String(live2dOptions.root || "").trim() &&
                String(live2dOptions.vtube || "").trim()
            );
            if (consolePetFigure) {
                consolePetFigure.dataset.live2dReady = hasModel ? "true" : "placeholder";
            }
            if (!hasModel) {
                resetLive2dRenderer();
                setLive2dPlaceholderVisible(true);
                setPetRuntimeState("Live2D 占位", runtimeReady ? "ready" : "starting");
                return;
            }
            setPetRuntimeState("Live2D 加载中", "starting");
            initLive2dRenderer(live2dOptions)
                .then(() => {
                    if (consolePetFigure) {
                        consolePetFigure.dataset.live2dReady = "true";
                    }
                    setPetRuntimeState("Live2D", runtimeReady ? "ready" : "starting");
                    startLive2dNativeIdle();
                })
                .catch((error) => {
                    console.error("Failed to initialize Live2D renderer", error);
                    const message = error?.message || String(error);
                    if (consolePetFigure) {
                        consolePetFigure.dataset.live2dReady = "error";
                        consolePetFigure.dataset.live2dError = message.slice(0, 180);
                    }
                    resetLive2dRenderer();
                    setLive2dPlaceholderVisible(true);
                    setPetRuntimeState("Live2D 加载失败", "error");
                });
            return;
        }

        resetLive2dRenderer();
        if (petShell) {
            petShell.hidden = false;
        }
        setPetRuntimeState(runtimeReady ? "已连接" : "连接中", runtimeReady ? "ready" : "starting");
    }

    async function loadAvatarConfig() {
        try {
            const response = await fetch(AVATAR_CONFIG_URL, { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`avatar config ${response.status}`);
            }
            const config = await response.json();
            applyAvatarMode(config?.mode || config?.fallback_mode, config);
        } catch (error) {
            console.warn("Failed to load avatar config, falling back to sprite", error);
            applyAvatarMode("sprite", {});
        }
    }

    function updatePetTimestamp(value = new Date()) {
        if (!petLastUpdated) {
            return;
        }
        petLastUpdated.textContent = formatClockTime(value);
    }

    function setPetSpeech(text) {
        const normalized = truncatePetText(text);
        const fallback = normalized || "我会在这里等你下一句。";
        if (petSpeechBubble) {
            petSpeechBubble.textContent = fallback;
        }
        if (consolePetBubble) {
            consolePetBubble.textContent = fallback;
        }
        updatePetTimestamp();
    }

    function setPetRuntimeState(text, state = "starting") {
        if (petRuntimeChip) {
            petRuntimeChip.textContent = text;
            petRuntimeChip.dataset.state = state;
        }
        if (consolePetRuntimeChip) {
            consolePetRuntimeChip.textContent = text;
            consolePetRuntimeChip.dataset.state = state;
        }
    }

    function setPetAttention(direction = "front", detail = null) {
        const normalizedDirection = ["left", "right", "up", "down", "front"].includes(
            String(direction || "").toLowerCase()
        )
            ? String(direction).toLowerCase()
            : "front";
        if (petAttentionChip) {
            petAttentionChip.dataset.state = normalizedDirection;
            petAttentionChip.textContent = humanizeDirection(normalizedDirection);
        }
        if (consolePetAttentionChip) {
            consolePetAttentionChip.dataset.state = normalizedDirection;
            consolePetAttentionChip.textContent = humanizeDirection(normalizedDirection);
        }
        if (petVisionText && detail) {
            petVisionText.textContent = detail;
        }
        updatePetTimestamp();
    }

    function setPetPose(pose = "idle", options = {}) {
        const normalizedPose = PET_SPRITES[pose] ? pose : "idle";

        if (avatarMode === "sprite") {
            if (petSprite) {
                petSprite.src = PET_SPRITES[normalizedPose];
                petSprite.dataset.pose = normalizedPose;
            }
            if (consolePetSprite) {
                consolePetSprite.src = PET_SPRITES[normalizedPose];
                consolePetSprite.dataset.pose = normalizedPose;
            }

            if (petFigure) {
                petFigure.dataset.pose = normalizedPose;
            }
            if (consolePetFigure) {
                consolePetFigure.dataset.pose = normalizedPose;
            }
        }

        if (petStateLabel && options.label) {
            petStateLabel.textContent = options.label;
        }

        if (petStateCopy && options.copy) {
            petStateCopy.textContent = options.copy;
        }

        if (typeof options.speech === "string") {
            setPetSpeech(options.speech);
        }

        updatePetTimestamp();
    }

    function pulseConsolePetMotion(motion = "celebrate", durationMs = 2800) {
        if (!consolePetFigure) {
            return;
        }
        if (avatarMode === "live2d") {
            return;
        }
        if (consolePetMotionTimer !== null) {
            window.clearTimeout(consolePetMotionTimer);
        }
        consolePetFigure.dataset.motion = motion;
        consolePetMotionTimer = window.setTimeout(() => {
            delete consolePetFigure.dataset.motion;
            consolePetMotionTimer = null;
        }, durationMs);
    }

    function clearPetIdleTimer() {
        if (petIdleTimer !== null) {
            window.clearTimeout(petIdleTimer);
            petIdleTimer = null;
        }
    }

    function schedulePetIdle(delayMs = 1200) {
        if (!isDesktopPetView) {
            return;
        }
        clearPetIdleTimer();
        petIdleTimer = window.setTimeout(() => {
            if (!runtimeReady) {
                setPetPose("sleep", {
                    label: "连接中",
                    copy: "桌宠窗口正在等待 runtime 恢复。",
                });
                return;
            }
            if (recognitionActive) {
                return;
            }
            setPetPose("idle", {
                label: "桌宠待命",
                copy: "我会在这里等你下一句，也会继续盯着 runtime 状态。",
            });
        }, delayMs);
    }

    function clearDesktopPetChatHideTimer() {
        if (desktopPetChatHideTimer !== null) {
            window.clearTimeout(desktopPetChatHideTimer);
            desktopPetChatHideTimer = null;
        }
    }

    function hasDesktopPetDraft() {
        return Boolean(messageInput && compactText(messageInput.value));
    }

    function isDesktopPetChatFocused() {
        if (!chatCard) {
            return false;
        }
        const activeElement = document.activeElement;
        return Boolean(activeElement && chatCard.contains(activeElement));
    }

    function shouldKeepDesktopPetChatOpen() {
        return Boolean(
            desktopPetHoverActive ||
            recognitionActive ||
            hasDesktopPetDraft() ||
            isDesktopPetChatFocused()
        );
    }

    function setDesktopPetChatVisible(visible, options = {}) {
        if (!isDesktopPetView || !chatCard) {
            return;
        }

        const nextVisible = Boolean(visible);
        chatCard.dataset.open = nextVisible ? "true" : "false";
        if (petShell) {
            petShell.dataset.chatVisible = nextVisible ? "true" : "false";
        }

        if (
            nextVisible &&
            options.focusInput &&
            messageInput &&
            !messageInput.disabled
        ) {
            window.setTimeout(() => {
                if (chatCard.dataset.open === "true") {
                    messageInput.focus();
                }
            }, 0);
        }
    }

    function scheduleDesktopPetChatHide(delayMs = 1400) {
        if (!isDesktopPetView || !chatCard) {
            return;
        }
        if (avatarMode === "live2d") {
            clearDesktopPetChatHideTimer();
            setDesktopPetChatVisible(true);
            return;
        }

        clearDesktopPetChatHideTimer();
        if (shouldKeepDesktopPetChatOpen()) {
            setDesktopPetChatVisible(true);
            return;
        }

        desktopPetChatHideTimer = window.setTimeout(() => {
            desktopPetChatHideTimer = null;
            if (shouldKeepDesktopPetChatOpen()) {
                setDesktopPetChatVisible(true);
                return;
            }
            setDesktopPetChatVisible(false);
        }, delayMs);
    }

    function syncDesktopPetChatVisibility(options = {}) {
        if (!isDesktopPetView || !chatCard) {
            return;
        }
        if (avatarMode === "live2d") {
            clearDesktopPetChatHideTimer();
            setDesktopPetChatVisible(true, { focusInput: Boolean(options.focusInput) });
            return;
        }

        const preferOpen = Boolean(options.preferOpen);
        if (preferOpen || shouldKeepDesktopPetChatOpen()) {
            clearDesktopPetChatHideTimer();
            setDesktopPetChatVisible(true, { focusInput: Boolean(options.focusInput) });
            return;
        }

        scheduleDesktopPetChatHide(
            typeof options.hideDelay === "number" ? options.hideDelay : 900
        );
    }

    function trimDesktopPetMessages(maxCount = 4) {
        if (!isDesktopPetView || !chatLog) {
            return;
        }

        const messages = Array.from(chatLog.children).filter(
            (node) => node instanceof HTMLElement && node.classList.contains("message")
        );
        while (messages.length > maxCount) {
            const oldest = messages.shift();
            oldest?.remove();
        }
    }

    function humanizeDirection(direction) {
        switch (String(direction || "").toLowerCase()) {
            case "left":
                return "左侧";
            case "right":
                return "右侧";
            case "up":
                return "上方";
            case "down":
                return "下方";
            case "front":
                return "正前方";
            default:
                return "未知";
        }
    }

    function humanizeReleaseReason(reason) {
        switch (String(reason || "").toLowerCase()) {
            case "lost":
                return "目标丢失";
            case "disabled":
                return "跟踪已关闭";
            case "released":
                return "已释放";
            default:
                return reason ? String(reason) : "-";
        }
    }

    function humanizeHeadMotion(headTargetDeg) {
        const pitch = Number(headTargetDeg?.pitch);
        const yaw = Number(headTargetDeg?.yaw);
        const parts = [];

        if (!Number.isNaN(yaw) && Math.abs(yaw) >= 2) {
            parts.push(`${yaw > 0 ? "左转" : "右转"} ${Math.abs(yaw).toFixed(1)}°`);
        }
        if (!Number.isNaN(pitch) && Math.abs(pitch) >= 2) {
            parts.push(`${pitch > 0 ? "低头" : "抬头"} ${Math.abs(pitch).toFixed(1)}°`);
        }
        if (!parts.length) {
            return "头部基本保持中位";
        }
        return `机器人头部目标：${parts.join("，")}`;
    }

    function humanizeEmotion(emotion) {
        if (!emotion || typeof emotion !== "object") {
            return "";
        }
        const label = String(emotion.label_zh || emotion.label || "").trim();
        const model = String(emotion.model || "").trim();
        const confidence = Number(emotion.confidence);
        if (!label) {
            return "";
        }
        const prefix = model ? `${model}：` : "表情：";
        if (!Number.isFinite(confidence)) {
            return `${prefix}${label}`;
        }
        return `${prefix}${label} ${(confidence * 100).toFixed(0)}%`;
    }

    function humanizeEmotionVersions(emotion) {
        const items = emotionVersionsForCompare(emotion)
            .map((item) => humanizeEmotion(item))
            .filter(Boolean);
        if (items.length) {
            return items.join("");
        }
        return humanizeEmotion(emotion);
    }

    function emotionLabelZh(label) {
        const normalized = String(label || "").trim();
        return EMOTION_LABELS_ZH[normalized] || normalized || "未知";
    }

    function emotionScoreText(value) {
        const score = Number(value);
        if (!Number.isFinite(score)) {
            return "-";
        }
        return score.toFixed(2);
    }

    function modelEmotionLabels(model) {
        const modelName = String(model || "").trim();
        return EMOTION_LABELS_BY_MODEL[modelName] || EMOTION_LABELS_BY_MODEL.default;
    }

    function emotionVersionsForCompare(emotion) {
        const versions = Array.isArray(emotion?.versions)
            ? emotion.versions
            : emotion && typeof emotion === "object"
                ? [emotion]
                : [];
        return versions.filter((item) => {
            if (!item || typeof item !== "object") {
                return false;
            }
            return String(item.model || "").trim() === PRIMARY_EMOTION_MODEL;
        });
    }

    function emptyEmotionVersions() {
        return [PRIMARY_EMOTION_MODEL].map((model) => ({
            model,
            index: 0,
            probabilities: [],
            label_zh: "等待结果",
        }));
    }

    function renderEmotionCompare(emotion) {
        if (!emotionCompareGrid && !emotionCompareSummary) {
            return;
        }

        const versions = emotionVersionsForCompare(emotion);
        if (!versions.length) {
            if (emotionCompareSummary) {
                emotionCompareSummary.textContent = "等待结果";
            }
            renderEmotionModelCards(emptyEmotionVersions(), true);
            return;
        }

        const primary = versions[versions.length - 1];
        const primaryLabel = String(primary.label_zh || primary.label || "").trim();
        const primaryConfidence = Number(primary.confidence);
        if (emotionCompareSummary) {
            emotionCompareSummary.textContent = [
                String(primary.model || "表情").trim(),
                primaryLabel,
                Number.isFinite(primaryConfidence)
                    ? emotionScoreText(primaryConfidence)
                    : "",
            ].filter(Boolean).join(" · ");
        }

        renderEmotionModelCards(versions, false);
    }

    function renderEmotionModelCards(versions, waiting) {
        if (!emotionCompareGrid) {
            return;
        }
        emotionCompareGrid.replaceChildren();
        versions.forEach((version) => {
            const model = String(version.model || "Model").trim();
            const labels = modelEmotionLabels(model);
            const probabilities = Array.isArray(version.probabilities)
                ? version.probabilities
                : [];
            const activeIndex = Number.isInteger(version.index)
                ? Number(version.index)
                : probabilities.reduce((best, value, index) => {
                    const score = Number(value);
                    if (!Number.isFinite(score)) {
                        return best;
                    }
                    return score > Number(probabilities[best] || -1) ? index : best;
                }, 0);

            const card = document.createElement("section");
            card.className = "emotion-model-card";

            const title = document.createElement("div");
            title.className = "emotion-model-title";
            title.textContent = model;
            card.appendChild(title);

            labels
                .map((label, index) => ({
                    index,
                    label,
                    score: Number(probabilities[index]),
                }))
                .forEach((item) => {
                const row = document.createElement("div");
                row.className = "emotion-row";
                if (!waiting && item.index === activeIndex) {
                    row.dataset.active = "true";
                }

                const name = document.createElement("span");
                name.textContent = emotionLabelZh(item.label);

                const score = document.createElement("strong");
                score.textContent = waiting ? "--" : emotionScoreText(item.score);

                row.append(name, score);
                card.appendChild(row);
            });

            emotionCompareGrid.appendChild(card);
        });
    }

    function humanizeIdentity(identity) {
        if (!identity || typeof identity !== "object") {
            return "";
        }
        const name = String(identity.name || "").trim();
        if (!name) {
            return "";
        }
        const confidence = Number(identity.confidence);
        const known = Boolean(identity.known);
        const prefix = known ? "身份：" : "";
        if (!Number.isFinite(confidence) || confidence <= 0) {
            return `${prefix}${name}`;
        }
        return `${prefix}${name} ${(confidence * 100).toFixed(0)}%`;
    }

    function normalizeBbox(bboxNorm) {
        if (!Array.isArray(bboxNorm) || bboxNorm.length !== 4) {
            return null;
        }
        const [x, y, width, height] = bboxNorm.map((value) => Number(value));
        if ([x, y, width, height].some((value) => Number.isNaN(value))) {
            return null;
        }
        if (width <= 0 || height <= 0) {
            return null;
        }
        return {
            x: Math.min(Math.max(x, 0), 1),
            y: Math.min(Math.max(y, 0), 1),
            width: Math.min(Math.max(width, 0), 1),
            height: Math.min(Math.max(height, 0), 1),
        };
    }

    function clearDetectionOverlay() {
        latestVisionOverlay = null;
        clearDetectionCanvas();
        setPetAttention("front", "等待检测链事件");
        if (cameraOverlay) {
            cameraOverlay.hidden = true;
            cameraOverlay.style.display = "none";
        }
        if (detectionBox) {
            detectionBox.hidden = true;
            detectionBox.style.display = "none";
            detectionBox.style.left = "";
            detectionBox.style.top = "";
            detectionBox.style.width = "";
            detectionBox.style.height = "";
            detectionBox.dataset.direction = "front";
        }
        if (detectionLabel) {
            detectionLabel.textContent = "等待检测";
            detectionLabel.style.display = "none";
        }
    }

    function clearDetectionCanvas() {
        if (!cameraOverlayCanvas) {
            return;
        }
        const context = cameraOverlayCanvas.getContext("2d");
        if (!context) {
            return;
        }
        context.clearRect(0, 0, cameraOverlayCanvas.width, cameraOverlayCanvas.height);
    }

    function syncDetectionCanvasSize(width, height) {
        if (!cameraOverlayCanvas) {
            return null;
        }
        const dpr = Math.max(window.devicePixelRatio || 1, 1);
        const targetWidth = Math.max(1, Math.round(width * dpr));
        const targetHeight = Math.max(1, Math.round(height * dpr));
        if (
            cameraOverlayCanvas.width !== targetWidth ||
            cameraOverlayCanvas.height !== targetHeight
        ) {
            cameraOverlayCanvas.width = targetWidth;
            cameraOverlayCanvas.height = targetHeight;
        }
        const context = cameraOverlayCanvas.getContext("2d");
        if (!context) {
            return null;
        }
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        context.clearRect(0, 0, width, height);
        return context;
    }

    function drawDetectionCanvas({ left, top, width, height, direction }) {
        if (!cameraOverlayCanvas || !cameraOverlay) {
            return;
        }
        const cameraShell = cameraPreview?.parentElement || cameraOverlay.parentElement;
        if (!cameraShell) {
            return;
        }
        const shellRect = cameraShell.getBoundingClientRect();
        const context = syncDetectionCanvasSize(shellRect.width, shellRect.height);
        if (!context) {
            return;
        }

        const strokeColor = ["left", "right", "up", "down"].includes(direction)
            ? "rgba(255, 206, 92, 0.98)"
            : "rgba(122, 226, 167, 0.98)";
        const fillColor = ["left", "right", "up", "down"].includes(direction)
            ? "rgba(255, 206, 92, 0.10)"
            : "rgba(122, 226, 167, 0.10)";
        const lineWidth = 3;
        const radius = 18;

        context.save();
        context.fillStyle = fillColor;
        context.strokeStyle = strokeColor;
        context.lineWidth = lineWidth;
        context.beginPath();
        if (typeof context.roundRect === "function") {
            context.roundRect(left, top, width, height, radius);
        } else {
            context.rect(left, top, width, height);
        }
        context.fill();
        context.stroke();
        context.restore();
    }

    function renderDetectionOverlay(overlayState = latestVisionOverlay) {
        if (!cameraOverlay || !detectionBox || !detectionLabel) {
            return;
        }
        if (!cameraActive || !cameraPreview || cameraPreview.hidden) {
            cameraOverlay.hidden = true;
            cameraOverlay.style.display = "none";
            detectionBox.hidden = true;
            detectionBox.style.display = "none";
            return;
        }

        const bbox = normalizeBbox(overlayState?.bboxNorm);
        if (!bbox) {
            cameraOverlay.hidden = true;
            cameraOverlay.style.display = "none";
            detectionBox.hidden = true;
            detectionBox.style.display = "none";
            return;
        }

        const cameraShell = cameraPreview.parentElement || cameraOverlay.parentElement;
        if (!cameraShell) {
            cameraOverlay.hidden = true;
            cameraOverlay.style.display = "none";
            detectionBox.hidden = true;
            detectionBox.style.display = "none";
            return;
        }

        cameraOverlay.hidden = false;
        cameraOverlay.style.display = "block";
        const shellRect = cameraShell.getBoundingClientRect();
        const shellWidth = shellRect.width;
        const shellHeight = shellRect.height;

        if (!shellWidth || !shellHeight) {
            cameraOverlay.hidden = true;
            cameraOverlay.style.display = "none";
            detectionBox.hidden = true;
            detectionBox.style.display = "none";
            return;
        }

        // The runtime sees frames from the browser bridge canvas (320x180),
        // so bbox_norm already targets the same 16:9 preview shell.
        const rawLeft = bbox.x * shellWidth;
        const rawTop = bbox.y * shellHeight;
        const rawWidth = bbox.width * shellWidth;
        const rawHeight = bbox.height * shellHeight;
        const centerX = rawLeft + rawWidth / 2;
        const centerY = rawTop + rawHeight / 2;
        const expandedWidth = rawWidth * DETECTION_BOX_SCALE_X;
        const expandedHeight = rawHeight * DETECTION_BOX_SCALE_Y;
        const left = Math.max(0, centerX - expandedWidth / 2);
        const top = Math.max(0, centerY - expandedHeight / 2);
        const right = Math.min(shellWidth, centerX + expandedWidth / 2);
        const bottom = Math.min(shellHeight, centerY + expandedHeight / 2);

        if (right <= left || bottom <= top) {
            cameraOverlay.hidden = true;
            cameraOverlay.style.display = "none";
            detectionBox.hidden = true;
            detectionBox.style.display = "none";
            return;
        }

        const direction = String(overlayState?.direction || "front").toLowerCase();
        const confidence = Number(overlayState?.confidence);
        const confidenceText = Number.isFinite(confidence)
            ? ` · ${(confidence * 100).toFixed(0)}%`
            : "";
        const motionText = humanizeHeadMotion(overlayState?.headTargetDeg);
        detectionBox.style.left = `${left}px`;
        detectionBox.style.top = `${top}px`;
        detectionBox.style.width = `${right - left}px`;
        detectionBox.style.height = `${bottom - top}px`;
        detectionBox.dataset.direction = direction;
        detectionBox.hidden = false;
        detectionBox.style.display = "block";
        detectionLabel.textContent = "";
        detectionLabel.style.display = "none";
        drawDetectionCanvas({
            left,
            top,
            width: right - left,
            height: bottom - top,
            direction,
        });
    }

    function appendVisionLog(title, detail) {
        if (!visionLog) {
            return;
        }
        if (visionLogEmpty) {
            visionLogEmpty.hidden = true;
        }
        const entry = document.createElement("div");
        entry.className = "vision-log-entry";
        entry.innerHTML = `<strong>${title}</strong><span>${detail}</span>`;
        visionLog.prepend(entry);
        while (visionLog.children.length > 6) {
            visionLog.removeChild(visionLog.lastElementChild);
        }
    }

    function updateVisionDirection(direction, subtitle) {
        const normalized = ["left", "right", "up", "down", "front"].includes(String(direction))
            ? String(direction)
            : "front";
        setPetAttention(normalized, subtitle);
        if (visionDirectionCard) {
            visionDirectionCard.dataset.direction = normalized;
        }
        if (visionDirectionLabel) {
            visionDirectionLabel.textContent = `方向：${humanizeDirection(normalized)}`;
        }
        if (visionDirectionSubtitle) {
            visionDirectionSubtitle.textContent = subtitle;
        }
    }

    function updateVisionTimestamp() {
        if (!visionLastUpdated) {
            return;
        }
        visionLastUpdated.textContent = formatClockTime(new Date());
    }

    function motionValueText(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return "--";
        }
        return `${number.toFixed(1)}°`;
    }

    function setRealtimeHud({
        direction = "front",
        confidence = null,
        headTargetDeg = {},
        identity = null,
        acquired = false,
    } = {}) {
        const identityText = humanizeIdentity(identity) || "等待结果";
        const identityConfidenceValue = Number(identity?.confidence);
        if (cameraTargetChip) {
            cameraTargetChip.textContent = acquired ? "已检测到目标" : "等待目标";
            cameraTargetChip.dataset.state = acquired ? "active" : "idle";
        }
        if (cameraDirection) {
            cameraDirection.textContent = humanizeDirection(direction);
        }
        if (cameraYaw) {
            cameraYaw.textContent = motionValueText(headTargetDeg?.yaw);
        }
        if (cameraPitch) {
            cameraPitch.textContent = motionValueText(headTargetDeg?.pitch);
        }
        if (cameraDistance) {
            cameraDistance.textContent = "--";
        }
        if (cameraIdentity) {
            cameraIdentity.textContent = identityText.replace(/^身份：/, "");
        }
        if (identityConfidence) {
            identityConfidence.textContent = Number.isFinite(identityConfidenceValue)
                ? identityConfidenceValue.toFixed(2)
                : "--";
        }
        if (footerConfidence) {
            footerConfidence.textContent = Number.isFinite(confidence)
                ? confidence.toFixed(2)
                : "--";
        }
        if (footerFps) {
            footerFps.textContent = cameraActive ? "实时" : "-- FPS";
        }
        if (footerResolution) {
            const width = cameraPreview?.videoWidth || 0;
            const height = cameraPreview?.videoHeight || 0;
            footerResolution.textContent = width && height ? `${width} × ${height}` : "--";
        }
    }

    function handleFrontDecision(payload) {
        const decision = Object(payload?.payload || {});
        const signalName = String(decision.signal_name || "");
        const metadata = Object(decision.signal_metadata || {});

        if (signalName === "vision_attention_updated") {
            const reactiveEventName = String(
                metadata.reactive_event_name || "attention_updated"
            );
            const direction = String(metadata.direction || "front").toLowerCase();
            const trackingEnabled = Boolean(metadata.tracking_enabled);
            const confidence = Number(metadata.confidence);
            const overlayState = {
                bboxNorm: metadata.bbox_norm,
                direction,
                confidence,
                headTargetDeg: Object(metadata.head_target_deg || {}),
                emotion: metadata.emotion,
                identity: metadata.identity,
            };
            latestVisionOverlay = overlayState;
            renderDetectionOverlay(overlayState);
            const emotionText = humanizeEmotionVersions(metadata.emotion);
            const identityText = humanizeIdentity(metadata.identity);
            renderEmotionCompare(metadata.emotion);
            setRealtimeHud({
                direction,
                confidence,
                headTargetDeg: overlayState.headTargetDeg,
                identity: metadata.identity,
                acquired: true,
            });
            if (visionSource) {
                const sourceParts = [
                    String(metadata.source || "reactive_vision"),
                    Number.isFinite(confidence)
                        ? `conf ${(confidence * 100).toFixed(0)}%`
                        : "",
                ].filter(Boolean);
                visionSource.textContent = Number.isFinite(confidence)
                    ? sourceParts.join(" · ")
                    : `source: ${String(metadata.source || "reactive_vision")}`;
            }
            if (visionEventName) {
                visionEventName.textContent = reactiveEventName;
            }
            if (visionTrackingEnabled) {
                visionTrackingEnabled.textContent = trackingEnabled ? "enabled" : "disabled";
            }
            if (visionEmotion) {
                visionEmotion.textContent = emotionText || "无表情结果";
            }
            if (visionIdentity) {
                visionIdentity.textContent = identityText || "无身份结果";
            }
            if (visionReleaseReason) {
                visionReleaseReason.textContent = "-";
            }
            setVisionStatus("已检测到目标", "active");
            const detailParts = [
                `检测链正在关注${humanizeDirection(direction)}的人脸`,
                identityText,
                emotionText,
                humanizeHeadMotion(overlayState.headTargetDeg),
            ].filter(Boolean);
            updateVisionDirection(
                direction,
                detailParts.join("，")
            );
            updateVisionTimestamp();
            const logKey = `${reactiveEventName}:${direction}:${trackingEnabled}`;
            if (reactiveEventName === "attention_acquired" || logKey !== lastVisionLogKey) {
                lastVisionLogKey = logKey;
                const logParts = [
                    `${formatClockTime(new Date())}`,
                    `tracking ${trackingEnabled ? "enabled" : "disabled"}`,
                    identityText,
                    emotionText,
                    humanizeHeadMotion(overlayState.headTargetDeg),
                ].filter(Boolean);
                appendVisionLog(
                    `${reactiveEventName} · ${humanizeDirection(direction)}`,
                    logParts.join(" · ")
                );
            }
            return;
        }

        if (signalName === "idle_entered" && String(metadata.source || "") === "reactive_vision") {
            const reason = humanizeReleaseReason(metadata.reason || "released");
            clearDetectionOverlay();
            setRealtimeHud({ acquired: false });
            lastVisionLogKey = `released:${reason}`;
            if (visionSource) {
                visionSource.textContent = `source: ${String(metadata.source || "reactive_vision")}`;
            }
            if (visionEventName) {
                visionEventName.textContent = "attention_released";
            }
            if (visionTrackingEnabled) {
                visionTrackingEnabled.textContent = metadata.return_to_center ? "returning" : "idle";
            }
            if (visionEmotion) {
                visionEmotion.textContent = "未锁定";
            }
            renderEmotionCompare(null);
            if (visionReleaseReason) {
                visionReleaseReason.textContent = reason;
            }
            setVisionStatus("当前未锁定目标", "searching");
            updateVisionDirection(
                "front",
                metadata.return_to_center
                    ? "检测链已经释放关注，头部正在回到中位"
                    : "检测链已经释放关注，等待下一次检测"
            );
            updateVisionTimestamp();
            appendVisionLog(
                `attention_released · ${reason}`,
                `${formatClockTime(new Date())} · return_to_center ${Boolean(metadata.return_to_center) ? "true" : "false"}`
            );
        }
    }

    function renderCameraPlaceholder(text, hidden = false) {
        if (!cameraPlaceholder) {
            return;
        }
        cameraPlaceholder.hidden = hidden;
        cameraPlaceholder.style.display = hidden ? "none" : "flex";
        if (!hidden) {
            cameraPlaceholder.textContent = text;
        }
    }

    function createMessage(role, text = "") {
        const demos = chatLog.querySelectorAll(".demo-message");
        demos.forEach((node) => node.remove());
        const wrapper = document.createElement("div");
        wrapper.className = `message ${role}`;

        const bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.textContent = text;

        wrapper.appendChild(bubble);
        chatLog.appendChild(wrapper);
        trimDesktopPetMessages();
        chatLog.scrollTop = chatLog.scrollHeight;
        return bubble;
    }

    function appendMessage(role, text) {
        createMessage(role, text);
        if (role === "assistant") {
            setPetSpeech(summarizePetSpeech(text));
        }
        syncDesktopPetChatVisibility({ preferOpen: true });
        scheduleDesktopPetChatHide(role === "assistant" ? 5200 : 3600);
    }

    function setComposerEnabled(enabled) {
        sendButton.disabled = !enabled;
        messageInput.disabled = !enabled;
        if (micButton) {
            micButton.disabled = !recognitionSupported || (!enabled && !recognitionActive);
        }
    }

    function updateMicButton() {
        if (!micButton) {
            return;
        }
        micButton.textContent = recognitionSupported
            ? (recognitionActive ? "停止" : "说话")
            : "无麦克风";
        micButton.dataset.active = recognitionActive ? "true" : "false";
    }

    function updateCameraButton() {
        if (!cameraToggle) {
            return;
        }
        if (!cameraSupported) {
            cameraToggle.textContent = "无相机";
            cameraToggle.disabled = true;
            return;
        }
        cameraToggle.disabled = false;
        cameraToggle.textContent = cameraActive ? "关闭相机" : "开启相机";
        cameraToggle.dataset.active = cameraActive ? "true" : "false";
    }

    function syncComposerState() {
        setComposerEnabled(socketReady && runtimeReady);
        updateMicButton();
        updateCameraButton();
    }

    function buildSocketUrl() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        return `${protocol}://${window.location.host}/ws/agent`;
    }

    function getTurnView(turnId) {
        const key = turnId || "turn:pending";
        if (!turnViews.has(key)) {
            turnViews.set(key, {
                hintBubble: null,
                hintText: "",
                finalBubble: null,
                finalText: "",
            });
        }
        return turnViews.get(key);
    }

    function ensureStageBubble(turnId, stage) {
        const turnView = getTurnView(turnId);
        const bubbleKey = `${stage}Bubble`;
        if (!turnView[bubbleKey]) {
            turnView[bubbleKey] = createMessage("assistant");
        }
        return turnView[bubbleKey];
    }

    function updateStageBubble(turnId, stage, text, mode) {
        const normalized = String(text || "");
        const turnView = getTurnView(turnId);
        const textKey = `${stage}Text`;

        if (mode === "append") {
            turnView[textKey] += normalized;
        } else {
            turnView[textKey] = normalized;
        }

        if (!turnView[textKey]) {
            return;
        }

        const bubble = ensureStageBubble(turnId, stage);
        bubble.textContent = turnView[textKey];
        trimDesktopPetMessages();
        chatLog.scrollTop = chatLog.scrollHeight;
        syncDesktopPetChatVisibility({ preferOpen: true });
        scheduleDesktopPetChatHide(5200);

        if (stage === "hint") {
            setPetPose("think", {
                label: "正在组织回应",
                copy: "Front 已经先给出一轮 hint，内核还在继续处理。",
                speech: summarizePetSpeech(turnView[textKey]),
            });
            return;
        }

        setPetPose("speak", {
            label: "正在回复",
            copy: "桌宠窗口已经开始把这一轮最终回复吐出来了。",
            speech: summarizePetSpeech(turnView[textKey]),
        });
    }

    function finishTurn() {
        syncComposerState();
        if (!recognitionActive && !isDesktopPetView) {
            messageInput.focus();
        }
        if (isDesktopPetView) {
            syncDesktopPetChatVisibility({ hideDelay: 1100 });
        }
        schedulePetIdle();
    }

    function applySurfaceStateToPet(phase) {
        if (!isDesktopPetView) {
            return;
        }
        if (avatarMode === "live2d") {
            return;
        }

        switch (phase) {
            case "listening":
            case "attending":
                setPetPose("listen", {
                    label: "正在听你说话",
                    copy: "Front 已经接住这一轮输入了。",
                });
                return;
            case "listening_wait":
            case "replying":
                setPetPose("think", {
                    label: "正在思考",
                    copy: "我正在等前后链路把这一轮整理完整。",
                });
                return;
            case "settling":
                setPetPose("speak", {
                    label: "准备收尾",
                    copy: "最终回复已经差不多了，马上回到待命。",
                });
                schedulePetIdle(1400);
                return;
            case "idle":
                if (avatarMode === "live2d") {
                    return;
                }
                schedulePetIdle(300);
                return;
            default:
                return;
        }
    }

    function handleEmbodimentEvent(payload) {
        const action = String(payload?.action || "");
        const eventPayload = Object(payload?.payload || {});
        if (action === "live2d_motion") {
            playLive2dNativeMotion(eventPayload.name, {
                source: String(eventPayload.source || ""),
            });
            return;
        }

        if (action === "live2d_expression") {
            applyLive2dNativeExpression(eventPayload.name, {
                source: String(eventPayload.source || ""),
            });
            return;
        }

        if (!isDesktopPetView) {
            return;
        }

        const pose = String(eventPayload.pose || "");
        const label = String(eventPayload.label || "");
        const speech = typeof eventPayload.speech === "string" ? eventPayload.speech : undefined;
        const copy = typeof eventPayload.copy === "string" ? eventPayload.copy : undefined;

        if (action === "surface_state") {
            if (avatarMode === "live2d") {
                return;
            }
            if (turnCompleted && ["settling", "idle"].includes(String(eventPayload.phase || ""))) {
                return;
            }
            if (pose && PET_SPRITES[pose]) {
                setPetPose(pose, {
                    label: label || undefined,
                    copy: copy || undefined,
                    speech: typeof speech === "string" ? summarizePetSpeech(speech) : undefined,
                });
            }
            if (eventPayload.attention) {
                setPetAttention(eventPayload.attention);
            }
            return;
        }

        if (action === "speech") {
            if (speech) {
                setPetSpeech(summarizePetSpeech(speech));
            }
            if (pose && PET_SPRITES[pose]) {
                setPetPose(pose, { label: label || undefined, copy: copy || undefined });
            }
            return;
        }

        if (action === "attention") {
            setPetAttention(eventPayload.direction || eventPayload.attention || "front", copy || null);
            return;
        }

        if (action === "pose" && pose && PET_SPRITES[pose]) {
            setPetPose(pose, {
                label: label || undefined,
                copy: copy || undefined,
                speech: typeof speech === "string" ? summarizePetSpeech(speech) : undefined,
            });
        }
    }

    function readDesktopPetPosition() {
        try {
            const raw = window.localStorage.getItem(DESKTOP_PET_POSITION_KEY);
            if (!raw) {
                return null;
            }
            const parsed = JSON.parse(raw);
            const x = Number(parsed?.x);
            const y = Number(parsed?.y);
            if (Number.isNaN(x) || Number.isNaN(y)) {
                return null;
            }
            return { x, y };
        } catch {
            return null;
        }
    }

    function writeDesktopPetPosition(position) {
        try {
            window.localStorage.setItem(
                DESKTOP_PET_POSITION_KEY,
                JSON.stringify(position)
            );
        } catch {
            // Persistence is best-effort.
        }
    }

    function ensureDesktopPetShell() {
        if (!isDesktopPetView || !appLayout || !petModeStack || !chatCard) {
            return null;
        }

        if (!desktopPetLayer || !desktopPetLayer.isConnected) {
            desktopPetLayer = document.createElement("div");
            desktopPetLayer.id = "desktop-pet-layer";
            desktopPetLayer.className = "desktop-pet-layer";
            appLayout.prepend(desktopPetLayer);
        }

        if (!petShell || !petShell.isConnected) {
            petShell = document.createElement("div");
            petShell.id = "pet-shell";
            petShell.className = "pet-shell";
            desktopPetLayer.appendChild(petShell);
        }

        if (petModeStack.parentElement !== petShell) {
            petShell.appendChild(petModeStack);
        }
        if (chatCard.parentElement !== petShell) {
            petShell.appendChild(chatCard);
        }

        return petShell;
    }

    function clampDesktopPetPosition(x, y) {
        const shell = petShell || ensureDesktopPetShell();
        if (!shell) {
            return { x: DESKTOP_PET_MARGIN, y: DESKTOP_PET_MARGIN };
        }

        const maxX = Math.max(
            DESKTOP_PET_MARGIN,
            window.innerWidth - shell.offsetWidth - DESKTOP_PET_MARGIN
        );
        const maxY = Math.max(
            DESKTOP_PET_MARGIN,
            window.innerHeight - shell.offsetHeight - DESKTOP_PET_MARGIN
        );

        return {
            x: Math.min(Math.max(Number(x) || DESKTOP_PET_MARGIN, DESKTOP_PET_MARGIN), maxX),
            y: Math.min(Math.max(Number(y) || DESKTOP_PET_MARGIN, DESKTOP_PET_MARGIN), maxY),
        };
    }

    function positionDesktopPetShell(x, y, persist = true) {
        const shell = petShell || ensureDesktopPetShell();
        if (!shell) {
            return;
        }

        const nextPosition = clampDesktopPetPosition(x, y);
        shell.style.left = `${nextPosition.x}px`;
        shell.style.top = `${nextPosition.y}px`;

        if (persist) {
            writeDesktopPetPosition(nextPosition);
        }
    }

    function placeDesktopPetShell() {
        const shell = ensureDesktopPetShell();
        if (!shell) {
            return;
        }

        const savedPosition = readDesktopPetPosition();
        if (savedPosition) {
            positionDesktopPetShell(savedPosition.x, savedPosition.y, false);
            return;
        }

        positionDesktopPetShell(
            (window.innerWidth - shell.offsetWidth) / 2,
            Math.max(DESKTOP_PET_MARGIN, Math.min(80, window.innerHeight * 0.12))
        );
    }

    function setDesktopPetDragging(active) {
        const shell = petShell || ensureDesktopPetShell();
        if (!shell) {
            return;
        }
        shell.dataset.dragging = active ? "true" : "false";
    }

    function handleDesktopPetPointerMove(event) {
        if (!desktopPetDrag) {
            return;
        }

        positionDesktopPetShell(
            event.clientX - desktopPetDrag.pointerOffsetX,
            event.clientY - desktopPetDrag.pointerOffsetY,
            false
        );
    }

    function finishDesktopPetDrag() {
        if (!desktopPetDrag) {
            return;
        }

        const shell = petShell || ensureDesktopPetShell();
        desktopPetDrag = null;
        setDesktopPetDragging(false);
        window.removeEventListener("pointermove", handleDesktopPetPointerMove);
        window.removeEventListener("pointerup", finishDesktopPetDrag);
        window.removeEventListener("pointercancel", finishDesktopPetDrag);

        if (shell) {
            const rect = shell.getBoundingClientRect();
            positionDesktopPetShell(rect.left, rect.top);
        }
    }

    function beginDesktopPetDrag(event) {
        if (!isDesktopPetView || event.button !== 0) {
            return;
        }

        const target = event.target instanceof Element ? event.target : null;
        if (target?.closest("button, textarea, input, a, label")) {
            return;
        }

        const shell = petShell || ensureDesktopPetShell();
        if (!shell) {
            return;
        }

        const rect = shell.getBoundingClientRect();
        desktopPetDrag = {
            pointerOffsetX: event.clientX - rect.left,
            pointerOffsetY: event.clientY - rect.top,
        };
        setDesktopPetDragging(true);
        window.addEventListener("pointermove", handleDesktopPetPointerMove);
        window.addEventListener("pointerup", finishDesktopPetDrag);
        window.addEventListener("pointercancel", finishDesktopPetDrag);
        event.preventDefault();
    }

    function configureDesktopPetView() {
        document.body.dataset.view = isDesktopPetView ? "desktop-pet" : "default";
        if (petModeStack) {
            petModeStack.hidden = !isDesktopPetView;
        }
        if (!isDesktopPetView) {
            return;
        }

        const shell = ensureDesktopPetShell();
        if (petModeStack) {
            petModeStack.hidden = false;
        }
        if (
            petStageCard &&
            petStageCard.dataset.desktopPetDragBound !== "true"
        ) {
            petStageCard.addEventListener("pointerdown", beginDesktopPetDrag);
            petStageCard.dataset.desktopPetDragBound = "true";
        }

        if (chatTitle) {
            chatTitle.textContent = "气泡对话";
        }
        if (chatSubtitle) {
            chatSubtitle.textContent =
                "这里还是同一个 app runtime，但会直接用桌面气泡来对话。";
        }
        if (messageInput) {
            messageInput.rows = 2;
            messageInput.placeholder = "放上来和我说句话";
        }
        if (chatCard) {
            chatCard.dataset.open = "false";
        }
        if (petFocusInputButton) {
            petFocusInputButton.addEventListener("click", () => {
                syncDesktopPetChatVisibility({ preferOpen: true, focusInput: true });
            });
        }
        if (shell && shell.dataset.desktopPetHoverBound !== "true") {
            shell.addEventListener("pointerenter", () => {
                desktopPetHoverActive = true;
                syncDesktopPetChatVisibility({ preferOpen: true });
            });
            shell.addEventListener("pointerleave", () => {
                desktopPetHoverActive = false;
                syncDesktopPetChatVisibility({ hideDelay: 320 });
            });
            shell.addEventListener("focusin", () => {
                syncDesktopPetChatVisibility({ preferOpen: true });
            });
            shell.addEventListener("focusout", () => {
                window.setTimeout(() => {
                    syncDesktopPetChatVisibility({ hideDelay: 320 });
                }, 0);
            });
            shell.dataset.desktopPetHoverBound = "true";
        }
        if (petSpeechBubble && petSpeechBubble.dataset.desktopPetFocusBound !== "true") {
            petSpeechBubble.addEventListener("click", () => {
                syncDesktopPetChatVisibility({ preferOpen: true, focusInput: true });
            });
            petSpeechBubble.dataset.desktopPetFocusBound = "true";
        }
        if (petFigure && petFigure.dataset.desktopPetFocusBound !== "true") {
            petFigure.addEventListener("click", () => {
                syncDesktopPetChatVisibility({ preferOpen: true, focusInput: true });
            });
            petFigure.dataset.desktopPetFocusBound = "true";
        }

        setPetRuntimeState("连接中", "starting");
        setPetAttention("front", "等待检测链事件");
        setPetPose("sleep", {
            label: "连接中",
            copy: "桌宠窗口正在等待 runtime 启动。",
            speech: "我先在这里待命，等内核连上。",
        });

        placeDesktopPetShell();
        syncDesktopPetChatVisibility({ hideDelay: 0 });
    }

    function formatSurfaceStatus(state) {
        const phase = String(state?.phase || "");
        if (phase === "listening") {
            return "Front 正在接收你的输入...";
        }
        if (phase === "attending") {
            return "Front 已注意到你，正在保持关注...";
        }
        if (phase === "listening_wait") {
            return "已收到语音，正在等待最终文本...";
        }
        if (phase === "replying") {
            return "Front 正在处理这一轮并组织回复...";
        }
        if (phase === "settling") {
            return "回复内容已经生成，正在做最后收尾...";
        }
        if (phase === "idle") {
            return "Runtime ready";
        }
        return "Runtime connected";
    }

    function formatRecognitionError(errorCode) {
        if (errorCode === "not-allowed" || errorCode === "service-not-allowed") {
            return "浏览器没有授予麦克风权限。";
        }
        if (errorCode === "audio-capture") {
            return "没有检测到可用麦克风设备。";
        }
        if (errorCode === "network") {
            return "浏览器语音识别服务暂时不可用。";
        }
        if (errorCode === "no-speech") {
            return "没有检测到清晰语音，可以再试一次。";
        }
        if (errorCode === "nomatch") {
            return "这次没有听清，可以再试一次。";
        }
        if (errorCode === "aborted") {
            return "语音输入已停止。";
        }
        return "语音识别失败，请再试一次。";
    }

    function formatCameraError(error) {
        const errorName = String(error?.name || "");
        if (errorName === "NotAllowedError" || errorName === "SecurityError") {
            return "浏览器没有授予摄像头权限。";
        }
        if (errorName === "NotFoundError" || errorName === "DevicesNotFoundError") {
            return "没有检测到可用摄像头设备。";
        }
        if (errorName === "NotReadableError" || errorName === "TrackStartError") {
            return "摄像头当前被其他应用占用。";
        }
        if (errorName === "OverconstrainedError") {
            return "当前摄像头不支持请求的分辨率。";
        }
        return "摄像头启动失败，请再试一次。";
    }

    function sendSocketEvent(payload) {
        if (!isSocketOpen()) {
            return false;
        }
        socket.send(JSON.stringify(payload));
        return true;
    }

    function sendEnvelope(type, payload) {
        return sendSocketEvent({ type, ts_ms: Date.now(), payload: payload || {} });
    }

    function dataUrlToBase64(dataUrl) {
        const commaIndex = String(dataUrl || "").indexOf(",");
        if (commaIndex < 0) {
            return String(dataUrl || "");
        }
        return String(dataUrl || "").slice(commaIndex + 1);
    }

    function stopBrowserCameraBridge() {
        if (cameraFrameTimer !== null) {
            window.clearInterval(cameraFrameTimer);
            cameraFrameTimer = null;
        }
    }

    function pushBrowserCameraFrame() {
        if (!cameraActive || !cameraPreview || cameraPreview.hidden || !isSocketOpen()) {
            return;
        }
        if (!cameraPreview.videoWidth || !cameraPreview.videoHeight) {
            return;
        }
        if (!cameraFrameCanvas) {
            cameraFrameCanvas = document.createElement("canvas");
        }
        cameraFrameCanvas.width = CAMERA_FRAME_WIDTH;
        cameraFrameCanvas.height = CAMERA_FRAME_HEIGHT;
        const context = cameraFrameCanvas.getContext("2d");
        if (!context) {
            return;
        }
        context.drawImage(cameraPreview, 0, 0, CAMERA_FRAME_WIDTH, CAMERA_FRAME_HEIGHT);
        const dataUrl = cameraFrameCanvas.toDataURL(
            "image/jpeg",
            CAMERA_FRAME_QUALITY
        );
        sendEnvelope("camera_frame", {
            image_b64: dataUrlToBase64(dataUrl),
            mime_type: "image/jpeg",
            width: CAMERA_FRAME_WIDTH,
            height: CAMERA_FRAME_HEIGHT,
        });
    }

    function startBrowserCameraBridge() {
        stopBrowserCameraBridge();
        pushBrowserCameraFrame();
        cameraFrameTimer = window.setInterval(
            pushBrowserCameraFrame,
            CAMERA_FRAME_INTERVAL_MS
        );
    }

    function emitUserSpeechStarted(_text = "") {
        // v4 wire protocol: VAD lifecycle is implicit via audio_chunk + speech_activity.
        // The browser already announces speech_activity(start) when the mic opens.
        return false;
    }

    function emitUserSpeechPartial(_text = "") {
        // v4 wire protocol does not carry partial transcripts inbound.
        return false;
    }

    function emitUserSpeechStopped(_text = "", _options = {}) {
        // v4 wire protocol: stopping the mic emits audio_stop + speech_activity(end).
        return false;
    }

    function submitUserText(rawText, options = {}) {
        const message = compactText(rawText);
        if (!message) {
            return false;
        }

        if (!isSocketOpen()) {
            connectSocket();
            if (options.fromSpeech) {
                messageInput.value = message;
                appendMessage("assistant", "语音已经识别完成，但 WebSocket 还没连上，请稍等后再发送。");
            } else {
                appendMessage("assistant", "WebSocket 还没连上，请稍等一下再发送。");
            }
            return false;
        }

        turnCompleted = false;
        appendMessage("user", message);
        if (!options.fromSpeech) {
            messageInput.value = "";
        }
        syncComposerState();
        setStatus(
            options.statusText || "消息已送达，Front 正在处理；你也可以继续发送。",
            true
        );

        sendEnvelope("browser_input", {
            kind: "text",
            session_id: THREAD_ID,
            payload: { text: message, turn_id: `T${Date.now().toString(36)}` },
        });
        syncDesktopPetChatVisibility({ preferOpen: true });
        scheduleDesktopPetChatHide(4200);
        clearPetIdleTimer();
        setPetPose("think", {
            label: "正在思考",
            copy: "我已经收到你的话了，正在把这一轮投给 front 和 kernel。",
            speech: `收到：${message}`,
        });
        return true;
    }

    async function stopCameraPreview() {
        stopBrowserCameraBridge();
        if (cameraStream) {
            cameraStream.getTracks().forEach((track) => track.stop());
        }
        cameraStream = null;
        cameraActive = false;
        if (cameraPreview) {
            cameraPreview.srcObject = null;
            cameraPreview.hidden = true;
        }
        clearDetectionOverlay();
        setRealtimeHud({ acquired: false });
        renderCameraPlaceholder("点击 Start Camera 预览本机相机", false);
        setCameraStatus("相机未启动", "idle");
        updateCameraButton();
    }

    async function startCameraPreview() {
        if (!cameraSupported) {
            setCameraStatus("当前浏览器不支持摄像头预览", "error");
            renderCameraPlaceholder("当前浏览器不支持 `getUserMedia()`。", false);
            updateCameraButton();
            return;
        }
        setCameraStatus("正在请求摄像头权限...", "requesting");
        renderCameraPlaceholder("正在请求本机摄像头权限...", false);
        updateCameraButton();
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    facingMode: "user",
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                },
                audio: false,
            });
            cameraStream = stream;
            cameraActive = true;
            if (cameraPreview) {
                cameraPreview.srcObject = stream;
                cameraPreview.hidden = false;
                await cameraPreview.play();
            }
            renderDetectionOverlay();
            setRealtimeHud({
                acquired: Boolean(latestVisionOverlay),
                direction: latestVisionOverlay?.direction || "front",
                confidence: latestVisionOverlay?.confidence,
                headTargetDeg: latestVisionOverlay?.headTargetDeg || {},
                identity: latestVisionOverlay?.identity,
            });
            renderCameraPlaceholder("", true);
            setCameraStatus("本机摄像头已连接", "ready");
            startBrowserCameraBridge();
        } catch (error) {
            cameraStream = null;
            cameraActive = false;
            if (cameraPreview) {
                cameraPreview.srcObject = null;
                cameraPreview.hidden = true;
            }
            renderCameraPlaceholder(formatCameraError(error), false);
            setCameraStatus(formatCameraError(error), "error");
        }
        updateCameraButton();
    }

    async function toggleCameraPreview() {
        if (cameraActive) {
            await stopCameraPreview();
            return;
        }
        await startCameraPreview();
    }

    async function finalizeRecognitionSession() {
        const transcript = currentRecognitionText();
        if (transcript && !speechCaptureEnded && !speechLifecycleActive) {
            emitUserSpeechStarted(transcript);
        }
        const shouldRepeatStopped = Boolean(
            speechCaptureEnded && transcript && transcript !== lastStoppedText
        );
        emitUserSpeechStopped(transcript, { allowRepeat: shouldRepeatStopped });

        recognitionActive = false;
        setSpeechPreview("");
        syncComposerState();

        const errorCode = recognitionError;
        recognitionFinalText = "";
        recognitionInterimText = "";
        recognitionError = "";
        lastPartialSentText = "";
        lastStoppedText = "";
        speechCaptureEnded = false;

        if (transcript) {
            const submitted = submitUserText(transcript, {
                fromSpeech: true,
                statusText: "语音已转成文本，Front 正在处理；你也可以继续发送。",
            });
            setMicStatus(
                submitted ? "本轮语音已转成文本并送入 runtime。" : "语音已识别，但当前连接还没恢复。",
                submitted ? "idle" : "error"
            );
            if (!submitted) {
                setPetPose("idle", {
                    label: "等待重连",
                    copy: "这轮语音已经转好了，但还没成功送进 runtime。",
                });
            }
            return;
        }

        if (errorCode) {
            const errorState =
                errorCode === "not-allowed" ||
                errorCode === "service-not-allowed" ||
                errorCode === "audio-capture"
                    ? "error"
                    : "idle";
            setMicStatus(formatRecognitionError(errorCode), errorState);
            setPetPose("idle", {
                label: "语音暂不可用",
                copy: formatRecognitionError(errorCode),
            });
            return;
        }

        setMicStatus("麦克风待命，可继续说话，也可直接输入。", "idle");
        schedulePetIdle(200);
    }

    function sdkMessageText(payload) {
        const content = Array.isArray(payload.content)
            ? payload.content
            : Array.isArray(payload.data?.content)
              ? payload.data.content
              : Array.isArray(payload.message?.content)
                ? payload.message.content
                : [];
        return content
            .filter((block) => block && (block.type === "text" || typeof block.text === "string"))
            .map((block) => compactText(block.text))
            .filter(Boolean)
            .join("\n");
    }

    function buildRecognition() {
        if (!recognitionSupported) {
            return null;
        }

        const instance = new RecognitionCtor();
        instance.lang = window.navigator.language || "zh-CN";
        instance.interimResults = true;
        instance.continuous = false;
        instance.maxAlternatives = 1;

        instance.addEventListener("start", () => {
            recognitionError = "";
            speechCaptureEnded = false;
            lastPartialSentText = "";
            lastStoppedText = "";
            setMicStatus("麦克风已开启，请开始说话。", "listening");
            setSpeechPreview("");
            clearPetIdleTimer();
            setPetPose("listen", {
                label: "正在听",
                copy: "麦克风已经打开，你可以直接对我说话。",
                speech: "我在听。",
            });
        });

        instance.addEventListener("speechstart", () => {
            speechCaptureEnded = false;
            turnCompleted = false;
            emitUserSpeechStarted(currentRecognitionText());
            setStatus("检测到你开始说话，正在接收语音。", true);
            setMicStatus("正在听你说话...", "listening");
        });

        instance.addEventListener("speechend", () => {
            speechCaptureEnded = true;
            emitUserSpeechStopped(currentRecognitionText());
            setStatus("检测到你停止说话，正在等待最终文本。", true);
            setMicStatus("已停止收音，正在整理文字...", "processing");
            setPetPose("think", {
                label: "整理语音",
                copy: "我在等浏览器把最后一版文字交出来。",
            });
        });

        instance.addEventListener("result", (event) => {
            let nextFinalText = recognitionFinalText;
            let nextInterimText = "";

            for (let index = event.resultIndex; index < event.results.length; index += 1) {
                const result = event.results[index];
                const transcript = compactText(result[0]?.transcript || "");
                if (!transcript) {
                    continue;
                }

                if (result.isFinal) {
                    nextFinalText = joinText(nextFinalText, transcript);
                } else {
                    nextInterimText = joinText(nextInterimText, transcript);
                }
            }

            recognitionFinalText = nextFinalText;
            recognitionInterimText = nextInterimText;

            const previewText = currentRecognitionText();
            if (previewText) {
                if (!speechCaptureEnded) {
                    emitUserSpeechStarted(previewText);
                    emitUserSpeechPartial(previewText);
                }
                setSpeechPreview(previewText);
                setMicStatus(
                    recognitionInterimText
                        ? "正在识别语音..."
                        : "已听到你的话，正在等待结束。",
                    recognitionInterimText ? "listening" : "processing"
                );
                if (!speechCaptureEnded) {
                    setPetPose("listen", {
                        label: "正在听",
                        copy: "继续说，我会把它实时转成文字。",
                        speech: previewText,
                    });
                }
            }
        });

        instance.addEventListener("nomatch", () => {
            recognitionError = "nomatch";
            setMicStatus(formatRecognitionError("nomatch"), "idle");
            setPetPose("idle", {
                label: "没听清",
                copy: formatRecognitionError("nomatch"),
            });
        });

        instance.addEventListener("error", (event) => {
            recognitionError = String(event.error || "unknown");
            if (recognitionError !== "aborted") {
                const errorState =
                    recognitionError === "not-allowed" ||
                    recognitionError === "service-not-allowed" ||
                    recognitionError === "audio-capture"
                        ? "error"
                        : "idle";
                setMicStatus(formatRecognitionError(recognitionError), errorState);
                setPetPose("idle", {
                    label: "语音出错",
                    copy: formatRecognitionError(recognitionError),
                });
            }
        });

        instance.addEventListener("end", () => {
            void finalizeRecognitionSession();
        });

        return instance;
    }

    function startRecognition() {
        if (!recognitionSupported || !recognition) {
            setMicStatus("当前浏览器不支持内建语音识别，请继续使用文本输入。", "unsupported");
            return;
        }

        if (!isSocketOpen()) {
            connectSocket();
            appendMessage("assistant", "WebSocket 还没连上，请稍等一下再使用语音。");
            return;
        }

        recognitionActive = true;
        recognitionFinalText = "";
        recognitionInterimText = "";
        recognitionError = "";
        speechLifecycleActive = false;
        lastPartialSentText = "";
        lastStoppedText = "";
        speechCaptureEnded = false;
        setSpeechPreview("");
        syncComposerState();
        setMicStatus("正在请求浏览器麦克风...", "processing");

        try {
            recognition.start();
        } catch (error) {
            recognitionActive = false;
            syncComposerState();
            setMicStatus("麦克风启动失败，请稍后再试。", "error");
        }
    }

    function stopRecognition() {
        if (!recognition || !recognitionActive) {
            return;
        }
        setMicStatus("正在停止收音...", "processing");
        try {
            recognition.stop();
        } catch (error) {
            recognitionActive = false;
            syncComposerState();
            setMicStatus("语音输入已停止。", "idle");
        }
    }

    function handleSocketEvent(envelope) {
        const eventType = String(envelope?.type || "");
        const payload = envelope?.payload || {};

        if (eventType === "sdk_message") {
            const text = sdkMessageText(payload);
            if (!text) {
                setStatus(`SDK message: ${payload.message_type || "unknown"}`, true);
                return;
            }
            turnCompleted = true;
            const turnId = String(payload.turn_id || "");
            updateStageBubble(turnId, "final", text, "replace");
            if (avatarMode !== "live2d" && shouldShowAntennaMotion(text)) {
                pulseConsolePetMotion("wiggle", 3200);
            }
            setStatus("Brain 已生成 SDK 回复。", true);
            finishTurn();
            return;
        }

        if (eventType === "action_result") {
            const status = String(payload.status || "");
            const verb = status === "ok" ? "完成" : status === "cancelled" ? "已取消" : "失败";
            setStatus(`${payload.name || "action"} ${verb}（${payload.duration_ms || 0}ms）`, true);
            if (status === "ok") {
                const actionName = String(payload.name || "");
                if (avatarMode !== "live2d") {
                    pulseConsolePetMotion(actionName === "set_antenna" ? "wiggle" : "celebrate", 3200);
                    setPetPose("speak", {
                        speech: `${actionName || "动作"} 完成啦。`,
                    });
                    schedulePetIdle(3400);
                }
            }
            if (status !== "ok" && payload.error) {
                appendMessage("assistant", `动作 ${payload.name} ${verb}：${payload.error}`);
                setPetRuntimeState("动作出错", "error");
                pulseConsolePetMotion("shake", 900);
            }
            return;
        }

        if (eventType === "embodiment") {
            handleEmbodimentEvent(payload);
            return;
        }

        if (eventType === "worker_event") {
            if (payload.task_id === "__surface__") {
                const phase = String(payload.payload?.state?.phase || "");
                if (turnCompleted && (phase === "settling" || phase === "idle")) {
                    return;
                }
                setStatus(formatSurfaceStatus({ phase }), runtimeReady);
                applySurfaceStateToPet(phase);
                return;
            }
            return;
        }

        if (eventType === "transcription") {
            const text = compactText(payload.text);
            if (payload.is_final) {
                setSpeechPreview("");
                if (text) {
                    appendMessage("user", text);
                }
            } else {
                setSpeechPreview(text);
            }
            return;
        }

        if (eventType === "vision_event") {
            handleFrontDecision({
                payload: {
                    signal_name: payload.event === "attention_released"
                        ? "idle_entered"
                        : "vision_attention_updated",
                    signal_metadata: payload.payload || {},
                },
            });
            return;
        }

        if (eventType === "tts_audio" || eventType === "speech_presenter" || eventType === "tts_stop" || eventType === "interrupt") {
            // v4 streams these for ancillary UI; sim_front_app does not render them.
            return;
        }

        if (eventType === "pipeline_error") {
            turnCompleted = false;
            appendMessage(
                "assistant",
                `runtime error (${payload.component || "pipeline"}): ${payload.reason || "unknown"}`,
            );
            setStatus("Runtime error", false);
            setPetRuntimeState("运行出错", "error");
            setPetPose("idle", {
                label: "这轮出错了",
                copy: payload.reason || "runtime 返回了错误，可以直接再试一轮。",
            });
            finishTurn();
            return;
        }

        if (eventType === "ping") {
            sendEnvelope("pong", {});
            return;
        }

        if (eventType === "pong") {
            return;
        }
    }

    function connectSocket() {
        if (
            socket &&
            (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
        ) {
            return;
        }

        socketReady = false;
        runtimeReady = false;
        syncComposerState();
        setStatus("Connecting app runtime WebSocket...", false);

        socket = new WebSocket(buildSocketUrl());
        socket.addEventListener("open", () => {
            socketReady = true;
            runtimeReady = true;
            setStatus("Runtime 已就绪。", true);
            setPetRuntimeState("已连接", "ready");
            setPetPose("idle", {
                label: "桌宠已上线",
                copy: "runtime 已经准备好了，可以直接和我对话。",
                speech: "我已经连上 v4 内核了。",
            });
            if (cameraActive) {
                startBrowserCameraBridge();
            }
            syncComposerState();
        });
        socket.addEventListener("message", (event) => {
            let envelope;
            try {
                envelope = JSON.parse(event.data);
            } catch (error) {
                console.warn("Failed to parse runtime websocket message", error, event.data);
                appendMessage("assistant", "收到了一条无法解析的运行时消息。");
                return;
            }
            try {
                handleSocketEvent(envelope);
            } catch (error) {
                console.error("Failed to handle runtime websocket message", error, envelope);
                appendMessage("assistant", "收到了一条运行时消息，但前端处理失败。");
            }
        });
        socket.addEventListener("close", () => {
            socket = null;
            socketReady = false;
            runtimeReady = false;
            turnCompleted = false;
            stopBrowserCameraBridge();
            syncComposerState();
            setStatus("WebSocket disconnected, retrying...", false);
            setPetRuntimeState("已断开", "error");
            setPetPose("sleep", {
                label: "连接断开",
                copy: "WebSocket 掉线了，我会继续自动重连。",
            });
            if (recognitionActive) {
                setMicStatus("连接已断开，本轮语音可能没有送达。", "error");
            }
            if (reconnectTimer !== null) {
                window.clearTimeout(reconnectTimer);
            }
            reconnectTimer = window.setTimeout(() => {
                reconnectTimer = null;
                connectSocket();
            }, 1000);
        });
        socket.addEventListener("error", () => {
            setStatus("WebSocket error", false);
            setPetRuntimeState("连接异常", "error");
        });
    }

    chatForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const message = compactText(messageInput.value);
        if (!message) {
            return;
        }

        submitUserText(message);
    });

    messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            chatForm.requestSubmit();
        }
    });
    messageInput.addEventListener("input", () => {
        if (!isDesktopPetView) {
            return;
        }
        syncDesktopPetChatVisibility({
            preferOpen: hasDesktopPetDraft(),
            hideDelay: hasDesktopPetDraft() ? 0 : 900,
        });
    });
    messageInput.addEventListener("focus", () => {
        if (!isDesktopPetView) {
            return;
        }
        syncDesktopPetChatVisibility({ preferOpen: true });
    });
    messageInput.addEventListener("blur", () => {
        if (!isDesktopPetView) {
            return;
        }
        window.setTimeout(() => {
            syncDesktopPetChatVisibility({ hideDelay: 320 });
        }, 0);
    });

    if (micButton) {
        micButton.addEventListener("click", () => {
            if (recognitionActive) {
                stopRecognition();
                return;
            }
            startRecognition();
        });
    }

    if (cameraToggle) {
        cameraToggle.addEventListener("click", () => {
            toggleCameraPreview().catch((error) => {
                console.error("camera toggle failed", error);
                setCameraStatus("摄像头切换失败", "error");
            });
        });
    }

    configureDesktopPetView();
    void loadAvatarConfig();
    setComposerEnabled(false);
    if (!recognitionSupported && composerHint) {
        composerHint.textContent = "Enter 发送，Shift+Enter 换行；语音输入需使用支持 SpeechRecognition 的浏览器";
    }
    recognition = buildRecognition();
    setMicStatus(
        recognitionSupported
            ? "麦克风待命，可继续说话，也可直接输入。"
            : "当前浏览器不支持内建语音识别，请继续使用文本输入。",
        recognitionSupported ? "idle" : "unsupported"
    );
    if (!cameraSupported) {
        setCameraStatus("当前浏览器不支持摄像头预览", "error");
        renderCameraPlaceholder("当前浏览器不支持 `getUserMedia()`。", false);
    } else {
        setCameraStatus("相机未启动", "idle");
    }
    setVisionStatus("等待检测链事件", "idle");
    updateVisionDirection("front", "还没有收到 reactive vision 更新");
    if (visionEventName) {
        visionEventName.textContent = "idle";
    }
    if (visionTrackingEnabled) {
        visionTrackingEnabled.textContent = "unknown";
    }
    if (visionEmotion) {
        visionEmotion.textContent = "等待结果";
    }
    if (visionIdentity) {
        visionIdentity.textContent = "等待结果";
    }
    renderEmotionCompare(null);
    if (visionReleaseReason) {
        visionReleaseReason.textContent = "-";
    }
    if (visionSource) {
        visionSource.textContent = "tracker: yolo";
    }
    if (visionLastUpdated) {
        visionLastUpdated.textContent = "尚未收到";
    }
    if (petLastUpdated) {
        petLastUpdated.textContent = "尚未收到";
    }
    setRealtimeHud({ acquired: false });
    updateMicButton();
    updateCameraButton();
    if (cameraPreview) {
        cameraPreview.addEventListener("loadedmetadata", () => {
            renderDetectionOverlay();
        });
    }
    window.addEventListener("resize", () => {
        renderDetectionOverlay();
        if (isDesktopPetView && avatarMode !== "live2d") {
            const shell = petShell || ensureDesktopPetShell();
            if (shell) {
                const rect = shell.getBoundingClientRect();
                positionDesktopPetShell(rect.left, rect.top, false);
            }
        }
    });
    window.addEventListener("beforeunload", () => {
        clearPetIdleTimer();
        window.removeEventListener("pointermove", handleDesktopPetPointerMove);
        window.removeEventListener("pointerup", finishDesktopPetDrag);
        window.removeEventListener("pointercancel", finishDesktopPetDrag);
        clearDesktopPetChatHideTimer();
        stopBrowserCameraBridge();
        if (cameraStream) {
            cameraStream.getTracks().forEach((track) => track.stop());
        }
    });
    connectSocket();
});
