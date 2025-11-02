#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, asyncio, websockets, zmq, zmq.asyncio
from datetime import datetime
import logging, sys
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,   # goes to run_all's log file
)
log = logging.getLogger("gui_settings")

CMD_ENDPOINT   = os.getenv("TIMONE_CMD", "tcp://127.0.0.1:5557")
WS_HOST        = os.getenv("SETTINGS_WS", "0.0.0.0")
WS_PORT        = int(os.getenv("SETTINGS_WSPORT", "8766"))
DRYRUN         = os.getenv("SETTINGS_DRYRUN", "0") == "1"

_zctx = zmq.asyncio.Context.instance()
_req  = _zctx.socket(zmq.REQ)
_req.connect(CMD_ENDPOINT)

# --- helpers -------------------------------------------------
async def _reqrep(obj: dict) -> dict:
    await _req.send_json(obj)
    return await _req.recv_json()

def _friendly_error(err: str) -> dict:
    e = err.lower()
    if "readiness to read" in e or "multiple access on port" in e or "serial not connected" in e:
        return {"ok": False, "code": "device_offline_or_in_use", "error": err}
    return {"ok": False, "code": "cmd_failed", "error": err}

async def forward_settings(payload: dict) -> dict:
    if DRYRUN:
        return {"ok": True, "dryrun": True, "echo": payload}

    # Preflight ping to avoid serial write when the device isn’t up
    try:
        pre = await _reqrep({"action": "GET_STATUS"})
        if not pre.get("ok"):
            return _friendly_error(pre.get("error", "unknown"))
    except Exception as e:
        return _friendly_error(str(e))

    cmd = {
        "action": "RAW",
        "peripheral_id": 0,
        "payload_hex": "",
        "note": {"gui_settings": payload},
    }
    try:
        rep = await _reqrep(cmd)
        return rep if rep.get("ok") else _friendly_error(rep.get("error","unknown"))
    except Exception as e:
        return _friendly_error(str(e))

# --- websocket server ---------------------------------------
async def handler(ws):
    await ws.send(json.dumps({"type": "hello", "msg": "settings-bridge-ready", "dryrun": DRYRUN}))
    async for msg in ws:
        try:
            data = json.loads(msg)
            log.info("Message received from GUI:\n%s", json.dumps(data, indent=2))
        except Exception:
            await ws.send(json.dumps({"type":"error","error":"invalid_json"}))
            continue

        # 🔍 DEBUG LOG: print every message received from GUI
        print("\n[gui_settings] Message received from GUI @",
              datetime.now().strftime("%H:%M:%S"))
        print(json.dumps(data, indent=2))

        if (data.get("type") or "").lower() == "radio_settings":
            rep = await forward_settings(data)
            await ws.send(json.dumps({
                "type": "ack",
                "for": "radio_settings",
                "radio": data.get("radio"),
                **rep
            }))
        else:
            await ws.send(json.dumps({"type":"error","error":"unsupported_type"}))

async def main():
    async with websockets.serve(handler, WS_HOST, WS_PORT, max_size=1_000_000):
        print(f"[gui_settings] WS on ws://{WS_HOST}:{WS_PORT}  (CMD → {CMD_ENDPOINT})  DRYRUN={DRYRUN}")
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
