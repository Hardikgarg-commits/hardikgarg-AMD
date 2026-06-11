import os
import secrets
import logging

from fastapi import FastAPI, WebSocket, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from swarm import Swarm, NUM_DRONES
import asyncio

logger = logging.getLogger(__name__)

app = FastAPI()

# ============================================
# CORS — restrict to known origins
# ============================================

ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Authorization"],
)

# ============================================
# API KEY AUTH
# ============================================

API_KEY = os.environ.get("SWARM_API_KEY", secrets.token_urlsafe(32))

if "SWARM_API_KEY" not in os.environ:
    logger.warning(
        "SWARM_API_KEY not set — generated ephemeral key: %s", API_KEY
    )


def verify_api_key(api_key: str = Query(alias="api_key", default="")):
    if not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )


swarm = Swarm()

# ============================================
# STATIC FRONTEND
# ============================================

app.mount("/static", StaticFiles(directory="../frontend"), name="static")

@app.get("/")
def read_index():
    return FileResponse("../frontend/index.html")


# ============================================
# MODE TOGGLE (Optional future feature)
# ============================================

@app.get("/toggle")
def toggle(_: None = Depends(verify_api_key)):
    swarm.log("Manual mode toggle triggered")
    return {"status": "ok"}


# ============================================
# MANUAL DRONE MOVEMENT (Arrow Keys)
# ============================================

MAX_MOVE_DELTA = 10

@app.get("/manual_move")
def manual_move(
    id: int = Query(ge=0, lt=NUM_DRONES),
    dx: int = Query(ge=-MAX_MOVE_DELTA, le=MAX_MOVE_DELTA),
    dy: int = Query(ge=-MAX_MOVE_DELTA, le=MAX_MOVE_DELTA),
    _: None = Depends(verify_api_key),
):
    swarm.manual_move(id, dx, dy)
    return {"status": "ok"}


# ============================================
# MANUAL DIRECTION CHANGE
# ============================================

@app.get("/set_direction")
def set_direction(
    id: int = Query(ge=0, lt=NUM_DRONES),
    dir: int = Query(ge=-1, le=1),
    _: None = Depends(verify_api_key),
):
    swarm.set_direction(id, dir)
    return {"status": "ok"}


# ============================================
# WEBSOCKET LIVE DATA STREAM
# ============================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    api_key = websocket.query_params.get("api_key", "")
    if not secrets.compare_digest(api_key, API_KEY):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    try:
        while True:
            swarm.update()
            await websocket.send_json(swarm.get_state())
            await asyncio.sleep(0.5)

    except Exception:
        logger.info("Client disconnected")
