import json, os, sys
payload = {"stdin": sys.stdin.read(), "env": {k: os.environ.get(k, "") for k in ["CCMINI_PROJECT_DIR", "CCMINI_SESSION_ID", "CCMINI_HOOK_EVENT"]}}
with open(os.environ["HOOK_CAPTURE_PATH"], "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print(json.dumps({"decision":"block","reason":"blocked_by_hook","additionalContext":"hook_ctx"}, ensure_ascii=False))
