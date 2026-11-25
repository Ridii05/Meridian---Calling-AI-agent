import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from loguru import logger
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

import db
from events import event_bus
from pipeline import run_voice_pipeline

load_dotenv()

webrtc_handler = SmallWebRTCRequestHandler()
active_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    logger.info("Meridian SQLite database initialized")
    yield
    logger.info("Shutting down active WebRTC sessions...")
    for task in list(active_tasks):
        task.cancel()
    await webrtc_handler.close()


app = FastAPI(title="Meridian Voice Clinic API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/doctors")
async def get_doctors():
    return {"doctors": db.list_doctors_with_slots()}


@app.get("/api/appointments")
async def get_appointments():
    return {"appointments": db.list_appointments()}


@app.post("/api/reset")
async def reset_demo():
    db.init_db(force_reset=True)
    await event_bus.publish("data_reset", {})
    return {"ok": True}


@app.get("/api/events")
async def sse_events():
    return StreamingResponse(
        event_bus.subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/offer")
async def webrtc_offer(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    webrtc_req = SmallWebRTCRequest(
        sdp=data["sdp"],
        type=data["type"],
        pc_id=data.get("pc_id"),
        restart_pc=data.get("restart_pc"),
    )

    async def on_connection(connection):
        task = asyncio.create_task(run_voice_pipeline(connection))
        active_tasks.add(task)
        task.add_done_callback(active_tasks.discard)

    answer = await webrtc_handler.handle_web_request(
        request=webrtc_req,
        webrtc_connection_callback=on_connection,
    )
    return answer


@app.patch("/api/offer")
async def webrtc_ice_patch(request: Request):
    data = await request.json()
    candidates = [
        IceCandidate(
            candidate=c["candidate"],
            sdp_mid=c.get("sdp_mid"),
            sdp_mline_index=c.get("sdp_mline_index"),
        )
        for c in data.get("candidates", [])
    ]
    patch_req = SmallWebRTCPatchRequest(
        pc_id=data["pc_id"],
        candidates=candidates,
    )
    await webrtc_handler.handle_patch_request(patch_req)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )