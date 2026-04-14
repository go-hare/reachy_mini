document.addEventListener("DOMContentLoaded", () => {
    const THREAD_ID = "app:main";
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
    const RecognitionCtor =
        window.SpeechRecognition || window.webkitSpeechRecognition || null;
    const recognitionSupported = typeof RecognitionCtor === "function";

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
        speechPreview.textContent = normalized ? `识别中：${normalized}` : "";
    }

    function createMessage(role, text = "") {
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
        createMessage(role, text);
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

    function syncComposerState() {
        setComposerEnabled(socketReady && runtimeReady);
        updateMicButton();
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
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    function finishTurn() {
        syncComposerState();
        if (!recognitionActive) {
            messageInput.focus();
        }
    }

    function formatSurfaceStatus(state) {
        const phase = String(state?.phase || "");
        if (phase === "listening") {
            return "Front 正在接收你的输入...";
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

    function sendSocketEvent(payload) {
        if (!isSocketOpen()) {
            return false;
        }
        socket.send(JSON.stringify(payload));
        return true;
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
        return true;
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
            return;
        }

        setMicStatus("麦克风待命，可继续说话，也可直接输入。", "idle");
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
            }
        });

        instance.addEventListener("nomatch", () => {
            recognitionError = "nomatch";
            setMicStatus(formatRecognitionError("nomatch"), "idle");
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
            syncComposerState();
            return;
        }

        if (eventType === "surface_state") {
            const phase = String(payload?.state?.phase || "");
            if (turnCompleted && (phase === "settling" || phase === "idle")) {
                return;
            }
            setStatus(formatSurfaceStatus(payload.state), runtimeReady);
            return;
        }

        if (eventType === "thinking") {
            turnCompleted = false;
            updateStageBubble(
                payload.turn_id,
                "thinking",
                payload.text,
                payload.phase === "delta" ? "append" : "replace"
            );
            setStatus("单脑正在思考，你也可以继续发送。", true);
            return;
        }

        if (eventType === "tool_progress") {
            turnCompleted = false;
            updateStageBubble(payload.turn_id, "tool", payload.content, "append");
            setStatus("工具正在执行中。", true);
            return;
        }

        if (eventType === "text_delta") {
            turnCompleted = false;
            updateStageBubble(payload.turn_id, "reply", payload.text, "append");
            setStatus("单脑正在输出这一轮回复，你也可以继续发送。", true);
            return;
        }

        if (eventType === "turn_done") {
            turnCompleted = true;
            updateStageBubble(payload.turn_id, "reply", payload.text, "replace");
            setStatus("这轮回复已经完成，你也可以继续发送。", true);
            finishTurn();
            return;
        }

        if (eventType === "turn_error") {
            turnCompleted = false;
            appendMessage("assistant", `请求失败：${payload.error || "unknown error"}`);
            setStatus("Runtime error", false);
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
            syncComposerState();
            setStatus("WebSocket disconnected, retrying...", false);
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

    if (micButton) {
        micButton.addEventListener("click", () => {
            if (recognitionActive) {
                stopRecognition();
                return;
            }
            startRecognition();
        });
    }

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
    updateMicButton();
    connectSocket();
});
