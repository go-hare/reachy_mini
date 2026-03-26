document.addEventListener("DOMContentLoaded", () => {
    const status = document.getElementById("status");
    const statusDot = document.getElementById("status-dot");
    const chatLog = document.getElementById("chat-log");
    const chatForm = document.getElementById("chat-form");
    const messageInput = document.getElementById("message-input");
    const sendButton = document.getElementById("send-button");
    const turnViews = new Map();

    let socket = null;
    let socketReady = false;
    let runtimeReady = false;
    let reconnectTimer = null;

    function setStatus(text, ready) {
        status.textContent = text;
        statusDot.dataset.ready = ready ? "true" : "false";
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
    }

    function syncComposerState() {
        setComposerEnabled(socketReady && runtimeReady);
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
        messageInput.focus();
    }

    function formatSurfaceStatus(state) {
        const phase = String(state?.phase || "");
        if (phase === "listening") {
            return "Front 已接到消息，正在投递内核...";
        }
        if (phase === "replying") {
            return "内核处理中，Front 正在组织最终回复...";
        }
        if (phase === "settling") {
            return "最终回复已生成，Runtime 正在收尾...";
        }
        if (phase === "idle") {
            return "Runtime ready";
        }
        return "Runtime connected";
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
            setStatus(formatSurfaceStatus(payload.state), runtimeReady);
            return;
        }

        if (eventType === "front_hint_chunk") {
            updateStageBubble(payload.turn_id, "hint", payload.text, "append");
            setStatus("Front 已先回应，内核继续处理中，你也可以继续发送。", true);
            return;
        }

        if (eventType === "front_hint_done") {
            updateStageBubble(payload.turn_id, "hint", payload.text, "replace");
            setStatus("Front 已先回应，内核继续处理中，你也可以继续发送。", true);
            return;
        }

        if (eventType === "front_final_chunk") {
            updateStageBubble(payload.turn_id, "final", payload.text, "append");
            setStatus("Front 正在输出最终回复，你也可以继续发送。", true);
            return;
        }

        if (eventType === "front_final_done") {
            updateStageBubble(payload.turn_id, "final", payload.text, "replace");
            setStatus("Runtime ready", true);
            finishTurn();
            return;
        }

        if (eventType === "turn_error") {
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
            syncComposerState();
            setStatus("WebSocket disconnected, retrying...", false);
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
        const message = messageInput.value.trim();
        if (!message) {
            return;
        }

        if (!socket || socket.readyState !== WebSocket.OPEN) {
            connectSocket();
            appendMessage("assistant", "WebSocket 还没连上，请稍等一下再发送。");
            return;
        }

        appendMessage("user", message);
        messageInput.value = "";
        syncComposerState();
        setStatus("消息已投递，等待 Front 首轮回复；你也可以继续发送。", true);

        socket.send(
            JSON.stringify({
                type: "user_text",
                thread_id: "app:main",
                text: message,
            })
        );
    });

    messageInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            chatForm.requestSubmit();
        }
    });

    setComposerEnabled(false);
    connectSocket();
});
