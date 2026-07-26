import json
import sys
from pathlib import Path


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        send({"id": request_id, "result": {}})
    elif method == "thread/start":
        send({"id": request_id, "result": {"thread": {"id": "thread-new"}}})
    elif method == "thread/resume":
        send(
            {
                "id": request_id,
                "result": {"thread": {"id": params["threadId"]}},
            }
        )
    elif method == "turn/start":
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(
                json.dumps(params),
                encoding="utf-8",
            )
        missing_image = any(
            item.get("type") == "localImage"
            and not Path(item["path"]).is_file()
            for item in params.get("input") or []
        )
        if missing_image:
            send(
                {
                    "id": request_id,
                    "error": {"message": "local image does not exist"},
                }
            )
            continue
        turn_id = "turn-test"
        send({"id": request_id, "result": {"turn": {"id": turn_id}}})
        send(
            {
                "method": "item/started",
                "params": {
                    "turnId": turn_id,
                    "item": {
                        "id": "agent-message-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "",
                    },
                },
            }
        )
        send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "turnId": turn_id,
                    "itemId": "agent-message-1",
                    "delta": "Fake Codex reply",
                },
            }
        )
        send(
            {
                "method": "item/completed",
                "params": {
                    "turnId": turn_id,
                    "item": {
                        "id": "agent-message-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "Fake Codex reply",
                    },
                },
            }
        )
        send(
            {
                "method": "turn/completed",
                "params": {
                    "turn": {
                        "id": turn_id,
                        "status": "completed",
                        "error": None,
                    }
                },
            }
        )
    else:
        send(
            {
                "id": request_id,
                "error": {"message": f"unsupported test method: {method}"},
            }
        )
