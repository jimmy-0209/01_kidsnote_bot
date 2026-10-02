from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

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
    return JSONResponse(scheduler.STATE)


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
