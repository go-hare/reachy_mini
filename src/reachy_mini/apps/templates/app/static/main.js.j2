document.addEventListener("DOMContentLoaded", () => {
    const SESSION_ID = "app:main";
    const status = document.getElementById("status");
    const statusDot = document.getElementById("status-dot");
    const chatLog = document.getElementById("chat-log");
    const chatForm = document.getElementById("chat-form");
    const messageInput = document.getElementById("message-input");
    const sendButton = document.getElementById("send-button");
    const micButton = document.getElementById("mic-button");
    const micStatus = document.getElementById("mic-status");
    const composerHint = document.getElementById("composer-hint");
    const speechPreview = document.getElementById("speech-preview");
    const turnViews = new Map();
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext || null;
    const audioCaptureSupported = Boolean(
        AudioContextCtor &&
        window.navigator?.mediaDevices &&
        typeof window.navigator.mediaDevices.getUserMedia === "function"
    );
    const ttsPlayer = createTtsPlayer();

    let socket = null;
    let socketReady = false;
    let runtimeReady = true;
    let reconnectTimer = null;
    let microphoneActive = false;
    let microphoneStream = null;
    let microphoneContext = null;
    let microphoneSourceNode = null;
    let microphoneProcessorNode = null;
    let microphoneSilentGainNode = null;
    let surfacePhase = "idle";
    let pingTimer = null;
    let nextTurnSeq = 1;

    function compactText(text) {
        return String(text || "")
            .replace(/\s+/g, " ")
            .trim();
    }

    function isSocketOpen() {
        return Boolean(socket && socket.readyState === WebSocket.OPEN);
    }

    function setStatus(text, ready) {
        if (!status) {
            return;
        }
        status.textContent = text;
        if (statusDot) {
            statusDot.dataset.ready = ready ? "true" : "false";
        }
    }

    function setMicStatus(text, state = "idle") {
        if (!micStatus) {
            return;
        }
        micStatus.textContent = text;
        micStatus.dataset.state = state;
    }

    function setSpeechPreview(text) {
        if (!speechPreview) {
            return;
        }
        const normalized = compactText(text);
        speechPreview.hidden = !normalized;
        speechPreview.textContent = normalized;
    }

    function setDefaultMicStatus() {
        if (microphoneActive) {
            return;
        }
        if (!audioCaptureSupported) {
            setMicStatus("当前浏览器不支持原始麦克风流，请继续使用文本输入。", "unsupported");
            return;
        }
        setMicStatus("麦克风待命，点击 Talk 把音频送给 runtime。", "idle");
    }

    function createMessage(role, text = "") {
        if (!chatLog) {
            return null;
        }
        const wrapper = document.createElement("div");
        wrapper.className = `message ${role}`;
        const bubble = document.createElement("div");
        bubble.className = "bubble";
        bubble.textContent = text;
        wrapper.appendChild(bubble);
        chatLog.appendChild(wrapper);
        chatLog.scrollTop = chatLog.scrollHeight;
        return bubble;
    }

    function appendMessage(role, text) {
        return createMessage(role, text);
    }

    function setComposerEnabled(enabled) {
        if (sendButton) {
            sendButton.disabled = !enabled;
        }
        if (messageInput) {
            messageInput.disabled = !enabled;
        }
        if (micButton) {
            micButton.disabled =
                !audioCaptureSupported ||
                (!enabled && !microphoneActive);
        }
    }

    function updateMicButton() {
        if (!micButton) {
            return;
        }
        if (!audioCaptureSupported) {
            micButton.textContent = "Mic N/A";
            micButton.dataset.active = "false";
            return;
        }
        micButton.textContent = microphoneActive ? "Stop" : "Talk";
        micButton.dataset.active = microphoneActive ? "true" : "false";
    }

    function syncComposerState() {
        setComposerEnabled(socketReady && runtimeReady);
        updateMicButton();
    }

    function buildSocketUrl() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        return `${protocol}://${window.location.host}/ws/agent`;
    }

    function makeTurnId() {
        const seq = nextTurnSeq;
        nextTurnSeq += 1;
        return `T${Date.now().toString(36)}_${seq}`;
    }

    function getTurnView(turnId) {
        const key = turnId || "turn:pending";
        if (!turnViews.has(key)) {
            turnViews.set(key, {
                replyBubble: null,
                replyText: "",
            });
        }
        return turnViews.get(key);
    }

    function ensureReplyBubble(turnId) {
        const turnView = getTurnView(turnId);
        if (!turnView.replyBubble) {
            turnView.replyBubble = createMessage("assistant");
        }
        return turnView.replyBubble;
    }

    function setReplyText(turnId, text) {
        const turnView = getTurnView(turnId);
        turnView.replyText = String(text || "");
        if (!turnView.replyText) {
            return;
        }
        const bubble = ensureReplyBubble(turnId);
        if (!bubble) {
            return;
        }
        bubble.textContent = turnView.replyText;
        if (chatLog) {
            chatLog.scrollTop = chatLog.scrollHeight;
        }
    }

    function sdkMessageText(payload) {
        const content = Array.isArray(payload.content) ? payload.content : [];
        return content
            .filter((block) => block && block.type === "text")
            .map((block) => compactText(block.text))
            .filter(Boolean)
            .join("\n");
    }

    function formatSurfaceStatus(phase) {
        if (phase === "listening") {
            return "Runtime 正在接收你的输入...";
        }
        if (phase === "listening_wait") {
            return "已收到语音，正在等待最终文本...";
        }
        if (phase === "replying") {
            return "Brain 正在生成回复...";
        }
        if (phase === "acting") {
            return "正在执行动作...";
        }
        if (phase === "settling") {
            return "回复已完成，正在收尾...";
        }
        return "Runtime ready";
    }

    function applySurfacePhase(phase) {
        surfacePhase = phase || "idle";
        setStatus(formatSurfaceStatus(surfacePhase), true);
        if (surfacePhase === "listening") {
            setMicStatus("Runtime 检测到你在说话。", "listening");
        } else if (surfacePhase === "listening_wait") {
            setMicStatus("正在等待转写...", "processing");
        } else if (!microphoneActive && surfacePhase === "idle") {
            setDefaultMicStatus();
        }
    }

    function formatMicrophoneError(error) {
        const errorCode = String(error || "");
        if (errorCode === "NotAllowedError" || errorCode === "SecurityError") {
            return "浏览器没有授予麦克风权限。";
        }
        if (errorCode === "NotFoundError" || errorCode === "DevicesNotFoundError") {
            return "没有检测到可用麦克风设备。";
        }
        if (errorCode === "NotReadableError" || errorCode === "TrackStartError") {
            return "麦克风正被其他应用占用。";
        }
        if (errorCode === "AbortError") {
            return "麦克风初始化被中断，请再试一次。";
        }
        return "麦克风启动失败，请再试一次。";
    }

    function sendEnvelope(type, payload) {
        if (!isSocketOpen()) {
            return false;
        }
        const envelope = {
            type,
            ts_ms: Date.now(),
            payload: payload || {},
        };
        socket.send(JSON.stringify(envelope));
        return true;
    }

    function submitUserText(rawText) {
        const message = compactText(rawText);
        if (!message) {
            return false;
        }
        if (!isSocketOpen()) {
            connectSocket();
            appendMessage("assistant", "WebSocket 还没连上，请稍等一下再发送。");
            return false;
        }
        const turnId = makeTurnId();
        appendMessage("user", message);
        if (messageInput) {
            messageInput.value = "";
        }
        syncComposerState();
        setStatus("消息已送达，Brain 正在处理。", true);
        sendEnvelope("browser_input", {
            kind: "text",
            session_id: SESSION_ID,
            payload: { text: message, turn_id: turnId },
        });
        return true;
    }

    function arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        const chunkSize = 0x8000;
        let binary = "";
        for (let index = 0; index < bytes.length; index += chunkSize) {
            const chunk = bytes.subarray(index, index + chunkSize);
            binary += String.fromCharCode(...chunk);
        }
        return window.btoa(binary);
    }

    function base64ToArrayBuffer(value) {
        const binary = window.atob(String(value || ""));
        const buffer = new ArrayBuffer(binary.length);
        const view = new Uint8Array(buffer);
        for (let index = 0; index < binary.length; index += 1) {
            view[index] = binary.charCodeAt(index);
        }
        return buffer;
    }

    function float32ToPcm16Buffer(floatSamples) {
        const buffer = new ArrayBuffer(floatSamples.length * 2);
        const view = new DataView(buffer);
        for (let index = 0; index < floatSamples.length; index += 1) {
            const sample = Math.max(-1, Math.min(1, floatSamples[index] || 0));
            const value = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
            view.setInt16(index * 2, value, true);
        }
        return buffer;
    }

    function pcm16BufferToFloat32(buffer) {
        const view = new DataView(buffer);
        const length = Math.floor(buffer.byteLength / 2);
        const samples = new Float32Array(length);
        for (let index = 0; index < length; index += 1) {
            const value = view.getInt16(index * 2, true);
            samples[index] = value < 0 ? value / 0x8000 : value / 0x7fff;
        }
        return samples;
    }

    function createTtsPlayer() {
        let context = null;
        let nextStartAt = 0;

        function ensureContext() {
            if (!AudioContextCtor) {
                return null;
            }
            if (!context) {
                context = new AudioContextCtor();
            }
            return context;
        }

        return {
            async enqueue(envelopePayload) {
                const ctx = ensureContext();
                if (!ctx) {
                    return;
                }
                if (ctx.state === "suspended") {
                    try {
                        await ctx.resume();
                    } catch (_error) {
                        // Some browsers require a user gesture; ignore.
                    }
                }
                const buffer = base64ToArrayBuffer(envelopePayload.pcm_b64);
                if (buffer.byteLength === 0) {
                    return;
                }
                const sampleRate = Number(envelopePayload.sample_rate) || ctx.sampleRate;
                const samples = pcm16BufferToFloat32(buffer);
                if (samples.length === 0) {
                    return;
                }
                const audioBuffer = ctx.createBuffer(1, samples.length, sampleRate);
                audioBuffer.copyToChannel(samples, 0);
                const source = ctx.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(ctx.destination);
                const startAt = Math.max(ctx.currentTime, nextStartAt);
                source.start(startAt);
                nextStartAt = startAt + audioBuffer.duration;
            },
            stop() {
                if (context) {
                    nextStartAt = context.currentTime;
                }
            },
        };
    }

    async function releaseMicrophoneResources() {
        if (microphoneProcessorNode) {
            microphoneProcessorNode.onaudioprocess = null;
            microphoneProcessorNode.disconnect();
            microphoneProcessorNode = null;
        }
        if (microphoneSourceNode) {
            microphoneSourceNode.disconnect();
            microphoneSourceNode = null;
        }
        if (microphoneSilentGainNode) {
            microphoneSilentGainNode.disconnect();
            microphoneSilentGainNode = null;
        }
        if (microphoneStream) {
            microphoneStream.getTracks().forEach((track) => track.stop());
            microphoneStream = null;
        }
        if (microphoneContext) {
            const context = microphoneContext;
            microphoneContext = null;
            try {
                await context.close();
            } catch (_error) {
                // Ignore close failures from torn-down browser contexts.
            }
        }
    }

    function sendAudioChunk(floatSamples) {
        if (!isSocketOpen() || !microphoneContext) {
            return false;
        }
        const pcm16Buffer = float32ToPcm16Buffer(floatSamples);
        return sendEnvelope("audio_chunk", {
            pcm_b64: arrayBufferToBase64(pcm16Buffer),
            sample_rate: microphoneContext.sampleRate,
            channels: 1,
        });
    }

    async function startMicrophoneCapture() {
        if (!audioCaptureSupported) {
            setMicStatus("当前浏览器不支持原始麦克风流，请继续使用文本输入。", "unsupported");
            return;
        }
        if (microphoneActive) {
            return;
        }
        if (!isSocketOpen()) {
            connectSocket();
            appendMessage("assistant", "WebSocket 还没连上，请稍等一下再使用语音。");
            return;
        }

        setSpeechPreview("");
        setMicStatus("正在请求浏览器麦克风...", "processing");

        try {
            const stream = await window.navigator.mediaDevices.getUserMedia({
                audio: true,
                video: false,
            });
            const context = new AudioContextCtor({ latencyHint: "interactive" });
            const source = context.createMediaStreamSource(stream);
            const processor = context.createScriptProcessor(2048, 1, 1);
            const silentGain = context.createGain();
            silentGain.gain.value = 0;

            processor.onaudioprocess = (audioEvent) => {
                if (!microphoneActive || !isSocketOpen()) {
                    return;
                }
                const channelData = audioEvent.inputBuffer.getChannelData(0);
                if (!channelData || channelData.length === 0) {
                    return;
                }
                sendAudioChunk(channelData);
            };

            source.connect(processor);
            processor.connect(silentGain);
            silentGain.connect(context.destination);
            await context.resume();

            microphoneStream = stream;
            microphoneContext = context;
            microphoneSourceNode = source;
            microphoneProcessorNode = processor;
            microphoneSilentGainNode = silentGain;
            microphoneActive = true;
            syncComposerState();
            setStatus("浏览器麦克风已接入，runtime 将处理音频流。", true);
            setMicStatus("麦克风已开启，请开始说话。", "listening");
            sendEnvelope("speech_activity", { state: "start" });
        } catch (error) {
            await releaseMicrophoneResources();
            microphoneActive = false;
            syncComposerState();
            setMicStatus(formatMicrophoneError(error?.name || error), "error");
        }
    }

    async function stopMicrophoneCapture(options = {}) {
        const shouldNotifyRuntime = options.notifyRuntime !== false;
        const errorText = compactText(options.errorText || "");

        if (!microphoneActive && !microphoneStream && !microphoneContext) {
            if (errorText) {
                setMicStatus(errorText, "error");
            } else {
                setDefaultMicStatus();
            }
            return;
        }

        microphoneActive = false;
        syncComposerState();
        if (shouldNotifyRuntime) {
            sendEnvelope("audio_stop", {});
            sendEnvelope("speech_activity", { state: "end" });
            setMicStatus("已停止收音，正在等待转写...", "processing");
        } else if (errorText) {
            setMicStatus(errorText, "error");
        }

        await releaseMicrophoneResources();
        if (!shouldNotifyRuntime && !errorText) {
            setDefaultMicStatus();
        }
    }

    function handleEnvelope(envelope) {
        const type = String(envelope?.type || "");
        const payload = envelope?.payload || {};

        if (type === "sdk_message") {
            const text = sdkMessageText(payload);
            if (text) {
                setReplyText(payload.turn_id, text);
                setStatus("Brain 已生成 SDK 回复。", true);
            } else {
                setStatus(`SDK message: ${payload.message_type || "unknown"}`, true);
            }
            return;
        }
        if (type === "action_result") {
            const status = String(payload.status || "");
            const verb = status === "ok" ? "完成" : status === "cancelled" ? "已取消" : "失败";
            const detail = `${payload.name || "action"} ${verb}（${payload.duration_ms || 0}ms）`;
            if (status !== "ok" && payload.error) {
                appendMessage("assistant", `动作 ${payload.name} ${verb}：${payload.error}`);
            }
            setStatus(detail, true);
            return;
        }
        if (type === "worker_event") {
            if (payload.task_id === "__surface__") {
                const phase = String(payload.payload?.state?.phase || "");
                applySurfacePhase(phase);
                return;
            }
            const note = compactText(payload.payload?.note);
            if (note) {
                setStatus(`worker:${payload.task_id} ${payload.event} - ${note}`, true);
            }
            return;
        }
        if (type === "transcription") {
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
        if (type === "tts_audio") {
            void ttsPlayer.enqueue(payload);
            return;
        }
        if (type === "tts_stop") {
            ttsPlayer.stop();
            return;
        }
        if (type === "speech_presenter") {
            // Optional debug stream; safe to ignore.
            return;
        }
        if (type === "vision_event") {
            // Default UI does not render vision events; profiles can extend.
            return;
        }
        if (type === "interrupt") {
            ttsPlayer.stop();
            return;
        }
        if (type === "pipeline_error") {
            setStatus(`pipeline error: ${payload.reason || "unknown"}`, false);
            appendMessage(
                "assistant",
                `runtime error (${payload.component || "pipeline"}): ${payload.reason || "unknown"}`,
            );
            return;
        }
        if (type === "ping") {
            sendEnvelope("pong", {});
            return;
        }
        if (type === "pong") {
            return;
        }
    }

    function startPingTimer() {
        if (pingTimer !== null) {
            return;
        }
        pingTimer = window.setInterval(() => {
            if (!isSocketOpen()) {
                return;
            }
            sendEnvelope("ping", {});
        }, 10000);
    }

    function stopPingTimer() {
        if (pingTimer === null) {
            return;
        }
        window.clearInterval(pingTimer);
        pingTimer = null;
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
        setStatus("连接 runtime WebSocket...", false);

        socket = new WebSocket(buildSocketUrl());
        socket.addEventListener("open", () => {
            socketReady = true;
            runtimeReady = true;
            setStatus("Runtime 已就绪。", true);
            syncComposerState();
            setDefaultMicStatus();
            startPingTimer();
        });
        socket.addEventListener("message", (event) => {
            try {
                handleEnvelope(JSON.parse(event.data));
            } catch (_error) {
                appendMessage("assistant", "收到了一条无法解析的运行时消息。");
            }
        });
        socket.addEventListener("close", () => {
            socket = null;
            socketReady = false;
            runtimeReady = false;
            stopPingTimer();
            syncComposerState();
            setStatus("WebSocket 已断开，正在重连...", false);
            if (microphoneActive) {
                void stopMicrophoneCapture({
                    notifyRuntime: false,
                    errorText: "连接已断开，本轮语音可能没有送达。",
                });
            } else {
                setDefaultMicStatus();
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
        });
    }

    if (chatForm) {
        chatForm.addEventListener("submit", (event) => {
            event.preventDefault();
            const message = compactText(messageInput?.value);
            if (!message) {
                return;
            }
            submitUserText(message);
        });
    }

    if (messageInput) {
        messageInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (chatForm) {
                    chatForm.requestSubmit();
                }
            }
        });
    }

    if (micButton) {
        micButton.addEventListener("click", () => {
            if (microphoneActive) {
                void stopMicrophoneCapture();
                return;
            }
            void startMicrophoneCapture();
        });
    }

    setComposerEnabled(false);
    if (composerHint) {
        composerHint.textContent = audioCaptureSupported
            ? "Enter 发送，Shift+Enter 换行；Talk 把音频流送到 runtime。"
            : "Enter 发送，Shift+Enter 换行；当前浏览器不支持原始麦克风流。";
    }
    setSpeechPreview("");
    setDefaultMicStatus();
    updateMicButton();
    connectSocket();
});
