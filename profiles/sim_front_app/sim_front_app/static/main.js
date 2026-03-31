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
    const visionStatus = document.getElementById("vision-status");
    const visionSource = document.getElementById("vision-source");
    const visionDirectionCard = document.getElementById("vision-direction-card");
    const visionDirectionLabel = document.getElementById("vision-direction-label");
    const visionDirectionSubtitle = document.getElementById("vision-direction-subtitle");
    const visionEventName = document.getElementById("vision-event-name");
    const visionTrackingEnabled = document.getElementById("vision-tracking-enabled");
    const visionReleaseReason = document.getElementById("vision-release-reason");
    const visionLastUpdated = document.getElementById("vision-last-updated");
    const visionLog = document.getElementById("vision-log");
    const visionLogEmpty = document.getElementById("vision-log-empty");
    const turnViews = new Map();
    const RecognitionCtor =
        window.SpeechRecognition || window.webkitSpeechRecognition || null;
    const recognitionSupported = typeof RecognitionCtor === "function";
    const cameraSupported =
        Boolean(navigator.mediaDevices) &&
        typeof navigator.mediaDevices.getUserMedia === "function";
    const DESKTOP_PET_POSITION_KEY = "reachy-mini.desktop-pet.position.v1";
    const DESKTOP_PET_MARGIN = 24;
    const PET_SPRITES = Object.freeze({
        idle: "/static/assets/desktop-pet/idle.png?v=20260331-desktop-pet-3",
        listen: "/static/assets/desktop-pet/listen.png?v=20260331-desktop-pet-3",
        think: "/static/assets/desktop-pet/think.png?v=20260331-desktop-pet-3",
        speak: "/static/assets/desktop-pet/speak.png?v=20260331-desktop-pet-3",
        sleep: "/static/assets/desktop-pet/sleep.png?v=20260331-desktop-pet-3",
        drag: "/static/assets/desktop-pet/drag.png?v=20260331-desktop-pet-3",
    });

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
    let latestVisionOverlay = null;
    let lastVisionLogKey = "";
    let petIdleTimer = null;
    let desktopPetLayer = null;
    let petShell = null;
    let desktopPetDrag = null;
    let desktopPetHoverActive = false;
    let desktopPetChatHideTimer = null;
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
        setPetRuntimeState(ready ? "已连接" : "连接中", ready ? "ready" : "starting");
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

    function updatePetTimestamp(value = new Date()) {
        if (!petLastUpdated) {
            return;
        }
        petLastUpdated.textContent = formatClockTime(value);
    }

    function setPetSpeech(text) {
        if (!petSpeechBubble) {
            return;
        }
        const normalized = truncatePetText(text);
        petSpeechBubble.textContent = normalized || "我会在这里等你下一句。";
        updatePetTimestamp();
    }

    function setPetRuntimeState(text, state = "starting") {
        if (petRuntimeChip) {
            petRuntimeChip.textContent = text;
            petRuntimeChip.dataset.state = state;
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
        if (petVisionText && detail) {
            petVisionText.textContent = detail;
        }
        updatePetTimestamp();
    }

    function setPetPose(pose = "idle", options = {}) {
        const normalizedPose = PET_SPRITES[pose] ? pose : "idle";

        if (petSprite) {
            petSprite.src = PET_SPRITES[normalizedPose];
            petSprite.dataset.pose = normalizedPose;
        }

        if (petFigure) {
            petFigure.dataset.pose = normalizedPose;
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
            };
            latestVisionOverlay = overlayState;
            renderDetectionOverlay(overlayState);
            if (visionSource) {
                visionSource.textContent = Number.isFinite(confidence)
                    ? `${String(metadata.source || "reactive_vision")} · conf ${(confidence * 100).toFixed(0)}%`
                    : `source: ${String(metadata.source || "reactive_vision")}`;
            }
            if (visionEventName) {
                visionEventName.textContent = reactiveEventName;
            }
            if (visionTrackingEnabled) {
                visionTrackingEnabled.textContent = trackingEnabled ? "enabled" : "disabled";
            }
            if (visionReleaseReason) {
                visionReleaseReason.textContent = "-";
            }
            setVisionStatus("已检测到目标", "active");
            updateVisionDirection(
                direction,
                `检测链正在关注${humanizeDirection(direction)}的人脸，${humanizeHeadMotion(overlayState.headTargetDeg)}`
            );
            updateVisionTimestamp();
            const logKey = `${reactiveEventName}:${direction}:${trackingEnabled}`;
            if (reactiveEventName === "attention_acquired" || logKey !== lastVisionLogKey) {
                lastVisionLogKey = logKey;
                appendVisionLog(
                    `${reactiveEventName} · ${humanizeDirection(direction)}`,
                    `${formatClockTime(new Date())} · tracking ${trackingEnabled ? "enabled" : "disabled"} · ${humanizeHeadMotion(overlayState.headTargetDeg)}`
                );
            }
            return;
        }

        if (signalName === "idle_entered" && String(metadata.source || "") === "reactive_vision") {
            const reason = humanizeReleaseReason(metadata.reason || "released");
            clearDetectionOverlay();
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
            setPetSpeech(text);
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
            ? (recognitionActive ? "Stop" : "Talk")
            : "Mic N/A";
        micButton.dataset.active = recognitionActive ? "true" : "false";
    }

    function updateCameraButton() {
        if (!cameraToggle) {
            return;
        }
        if (!cameraSupported) {
            cameraToggle.textContent = "Camera N/A";
            cameraToggle.disabled = true;
            return;
        }
        cameraToggle.disabled = false;
        cameraToggle.textContent = cameraActive ? "Stop Camera" : "Start Camera";
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
                speech: turnView[textKey],
            });
            return;
        }

        setPetPose("speak", {
            label: "正在回复",
            copy: "桌宠窗口已经开始把这一轮最终回复吐出来了。",
            speech: turnView[textKey],
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
                schedulePetIdle(300);
                return;
            default:
                return;
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
            window.innerWidth - shell.offsetWidth - DESKTOP_PET_MARGIN,
            window.innerHeight - shell.offsetHeight - DESKTOP_PET_MARGIN
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

    function stopBrowserCameraBridge() {
        if (cameraFrameTimer !== null) {
            window.clearInterval(cameraFrameTimer);
            cameraFrameTimer = null;
        }
    }

    function pushBrowserCameraFrame() {
        if (!cameraActive || !cameraPreview || !isSocketOpen()) {
            return;
        }
        if (cameraPreview.readyState < 2) {
            return;
        }

        const width = 320;
        const height = Math.max(180, Math.round(width * 9 / 16));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        if (!context) {
            return;
        }
        context.drawImage(cameraPreview, 0, 0, width, height);
        const imageB64 = canvas.toDataURL("image/jpeg", 0.6);
        sendSocketEvent({
            type: "browser_camera_frame",
            thread_id: THREAD_ID,
            image_b64: imageB64,
        });
    }

    function startBrowserCameraBridge() {
        stopBrowserCameraBridge();
        cameraFrameTimer = window.setInterval(() => {
            pushBrowserCameraFrame();
        }, 450);
    }

    function emitUserSpeechStarted(text = "") {
        if (speechLifecycleActive) {
            return true;
        }
        const delivered = sendSocketEvent({
            type: "user_speech_started",
            thread_id: THREAD_ID,
            text: compactText(text),
        });
        speechLifecycleActive = delivered;
        if (delivered) {
            lastStoppedText = "";
        }
        return delivered;
    }

    function emitUserSpeechPartial(text = "") {
        const normalized = compactText(text);
        if (!normalized || speechCaptureEnded) {
            return false;
        }
        if (!speechLifecycleActive) {
            emitUserSpeechStarted(normalized);
        }
        if (!speechLifecycleActive || lastPartialSentText === normalized) {
            return speechLifecycleActive;
        }
        const delivered = sendSocketEvent({
            type: "user_speech_partial",
            thread_id: THREAD_ID,
            text: normalized,
        });
        if (delivered) {
            lastPartialSentText = normalized;
        }
        return delivered;
    }

    function emitUserSpeechStopped(text = "", options = {}) {
        const normalized = compactText(text);
        const allowRepeat = Boolean(options.allowRepeat);
        if (!speechLifecycleActive && (!allowRepeat || lastStoppedText === normalized)) {
            return false;
        }
        sendSocketEvent({
            type: "user_speech_stopped",
            thread_id: THREAD_ID,
            text: normalized,
        });
        speechLifecycleActive = false;
        lastStoppedText = normalized;
        return true;
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

        sendSocketEvent({
            type: "user_text",
            thread_id: THREAD_ID,
            text: message,
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

    function handleSocketEvent(payload) {
        const eventType = String(payload?.type || "");

        if (eventType === "runtime_status") {
            runtimeReady = Boolean(payload.ready);
            setStatus(
                runtimeReady
                    ? "App runtime ready"
                    : "App runtime is starting...",
                runtimeReady
            );
            if (runtimeReady) {
                setPetPose("idle", {
                    label: "桌宠已上线",
                    copy: "runtime 已经准备好了，可以直接和我对话。",
                    speech: "我已经连上内核了。",
                });
            } else {
                setPetPose("sleep", {
                    label: "连接中",
                    copy: "runtime 还在启动，这会儿我先安静待命。",
                });
            }
            syncComposerState();
            return;
        }

        if (eventType === "surface_state") {
            const phase = String(payload?.state?.phase || "");
            if (turnCompleted && (phase === "settling" || phase === "idle")) {
                return;
            }
            setStatus(formatSurfaceStatus(payload.state), runtimeReady);
            applySurfaceStateToPet(phase);
            return;
        }

        if (eventType === "front_decision") {
            handleFrontDecision(payload);
            return;
        }

        if (eventType === "front_hint_chunk") {
            turnCompleted = false;
            updateStageBubble(payload.turn_id, "hint", payload.text, "append");
            setStatus("Front 已先回应，后续处理还在继续，你也可以继续发送。", true);
            return;
        }

        if (eventType === "front_hint_done") {
            turnCompleted = false;
            updateStageBubble(payload.turn_id, "hint", payload.text, "replace");
            setStatus("Front 已先回应，后续处理还在继续，你也可以继续发送。", true);
            return;
        }

        if (eventType === "front_final_chunk") {
            turnCompleted = false;
            updateStageBubble(payload.turn_id, "final", payload.text, "append");
            setStatus("Front 正在输出这一轮的最终回复，你也可以继续发送。", true);
            return;
        }

        if (eventType === "front_final_done") {
            turnCompleted = true;
            updateStageBubble(payload.turn_id, "final", payload.text, "replace");
            setStatus("这轮回复已经完成，你也可以继续发送。", true);
            finishTurn();
            return;
        }

        if (eventType === "turn_error") {
            turnCompleted = false;
            appendMessage("assistant", `请求失败：${payload.error || "unknown error"}`);
            setStatus("Runtime error", false);
            setPetRuntimeState("运行出错", "error");
            setPetPose("idle", {
                label: "这轮出错了",
                copy: payload.error || "runtime 返回了错误，可以直接再试一轮。",
            });
            finishTurn();
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
            setStatus("WebSocket connected, waiting runtime...", false);
            setPetPose("sleep", {
                label: "等待 runtime",
                copy: "WebSocket 已连上，正在等 runtime 报 ready。",
            });
            if (cameraActive) {
                startBrowserCameraBridge();
            }
            syncComposerState();
        });
        socket.addEventListener("message", (event) => {
            try {
                handleSocketEvent(JSON.parse(event.data));
            } catch (error) {
                appendMessage("assistant", "收到了一条无法解析的运行时消息。");
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
    updateMicButton();
    updateCameraButton();
    if (cameraPreview) {
        cameraPreview.addEventListener("loadedmetadata", () => {
            renderDetectionOverlay();
        });
    }
    window.addEventListener("resize", () => {
        renderDetectionOverlay();
        if (isDesktopPetView) {
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
