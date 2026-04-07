import json, os, time
target = os.environ.get("SIDECAR_CAPTURE_PATH")
payload = {k: os.environ.get(k, "") for k in ["MINI_AGENT_TEAM_NAME", "MINI_AGENT_TEAMMATE_NAME", "MINI_AGENT_MAILBOX_DIR", "MINI_AGENT_TEAM_DIR", "MINI_AGENT_AGENT_ID"]}
if target:
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
time.sleep(30)
