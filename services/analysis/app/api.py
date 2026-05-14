from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Apex Analysis Service", version="0.1.0")


class AnalyzePayload(BaseModel):
    map_id: str
    video_path: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/analyze")
def enqueue_analysis(payload: AnalyzePayload):
    # Placeholder endpoint for queue integration (BullMQ/Celery bridge).
    return {"status": "queued", "map_id": payload.map_id, "video_path": payload.video_path}
