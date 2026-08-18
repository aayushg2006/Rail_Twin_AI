"""WebSocket endpoint — the live channel between the twin and the console.

server -> client : {type:"snapshot", simState, prediction, kpis, options, ...}  (~4 Hz)
client -> server : {cmd:"pause"|"resume"|"set_speed"|"seek"|"apply_action"|
                    "decide"|"load_scenario"|"inject_event"|"set_horizon"|"reset", ...}
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    orch = ws.app.state.orchestrator
    orch.add_client(ws)
    try:
        await orch.snapshot_now(ws)
        while True:
            msg = await ws.receive_json()
            await orch.handle_command(msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        orch.remove_client(ws)
