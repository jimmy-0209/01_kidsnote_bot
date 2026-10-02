from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import jobs_store
import scheduler

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "run_log.txt"


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="KidsnoteBot 대시보드", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/api/status")
def get_status():
    return JSONResponse(scheduler.JOB_STATES[scheduler.LEGACY_JOB_ID])


@app.post("/api/run")
def trigger_run():
    result = scheduler.trigger_manual_run()
    if "error" in result:
        return JSONResponse(result, status_code=409)
    return JSONResponse(result)


@app.get("/api/logs")
def get_logs(lines: int = 200):
    if not LOG_PATH.exists():
        return JSONResponse({"logs": ""})
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    tail = "".join(all_lines[-lines:])
    return JSONResponse({"logs": tail})


@app.get("/api/schedule")
def get_schedule():
    return JSONResponse(scheduler.get_schedule_status())


@app.post("/api/schedule")
def set_schedule(config: dict):
    for t in config.get("times", []):
        hour, _, minute = t.partition(":")
        if not (hour.isdigit() and minute.isdigit() and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            return JSONResponse({"error": f"잘못된 시간 형식입니다: {t} (HH:MM 형식이어야 합니다)"}, status_code=400)
    return JSONResponse(scheduler.update_schedule(config))


def _validate_times(times: list) -> str | None:
    if not times:
        return "스케줄 시각을 하나 이상 입력하세요."
    for t in times:
        hour, _, minute = str(t).partition(":")
        if not (hour.isdigit() and minute.isdigit() and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            return f"잘못된 시간 형식입니다: {t} (HH:MM 형식이어야 합니다)"
    return None


@app.get("/api/jobs")
def list_jobs():
    result = []
    for job in jobs_store.list_jobs():
        public = jobs_store.public_job(job)
        public["state"] = scheduler.get_job_state(job["id"])
        public["next_run_at"] = scheduler.get_job_next_run(job["id"])
        result.append(public)
    return JSONResponse(result)


@app.post("/api/jobs")
def create_job(payload: dict):
    kidsnote_id = (payload.get("kidsnote_id") or "").strip()
    kidsnote_pw = payload.get("kidsnote_pw") or ""
    target_school = (payload.get("target_school") or "").strip()
    target_class = (payload.get("target_class") or "").strip()
    times = payload.get("times") or []

    if not kidsnote_id or not kidsnote_pw or not target_school or not target_class:
        return JSONResponse({"error": "키즈노트 ID/PW, 학교명, 반 이름을 모두 입력하세요."}, status_code=400)

    error = _validate_times(times)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    job = jobs_store.add_job(kidsnote_id, kidsnote_pw, target_school, target_class, times)
    scheduler.register_job_schedule(job)
    return JSONResponse(jobs_store.public_job(job))


@app.delete("/api/jobs/{job_id}")
def remove_job(job_id: str):
    scheduler.unregister_job_schedule(job_id)
    scheduler.JOB_STATES.pop(job_id, None)
    deleted = jobs_store.delete_job(job_id)
    if not deleted:
        return JSONResponse({"error": "해당 job을 찾을 수 없습니다."}, status_code=404)
    return JSONResponse({"status": "deleted"})


@app.post("/api/jobs/{job_id}/run")
def run_job(job_id: str):
    if not jobs_store.get_job(job_id):
        return JSONResponse({"error": "해당 job을 찾을 수 없습니다."}, status_code=404)
    result = scheduler.trigger_job_run(job_id)
    if "error" in result:
        return JSONResponse(result, status_code=409)
    return JSONResponse(result)
