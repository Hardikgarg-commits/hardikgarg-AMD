from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from swarm import Swarm
import asyncio

app = FastAPI()

# Allow frontend fetch calls safely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
def toggle():
    swarm.log("Manual mode toggle triggered")
    return {"status": "ok"}


# ============================================
# MANUAL DRONE MOVEMENT (Arrow Keys)
# ============================================

@app.get("/manual_move")
def manual_move(id: int, dx: int, dy: int):
    swarm.manual_move(id, dx, dy)
    return {"status": "ok"}


# ============================================
# MANUAL DIRECTION CHANGE
# ============================================

@app.get("/set_direction")
def set_direction(id: int, dir: int):
    swarm.set_direction(id, dir)
    return {"status": "ok"}


# ============================================
# WEBSOCKET LIVE DATA STREAM
# ============================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            swarm.update()
            await websocket.send_json(swarm.get_state())
            await asyncio.sleep(0.5)  # update rate (2 FPS backend tick)

    except Exception:
        print("Client disconnected")