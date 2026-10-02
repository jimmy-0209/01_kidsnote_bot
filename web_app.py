from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import kidsnote_discovery
import scheduler
import users_store

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


def _validate_children(children: list) -> str | None:
    if not children:
        return "자녀를 하나 이상 추가하세요."
    for c in children:
        if not (c.get("child_name") and c.get("school") and c.get("class")):
            return "자녀마다 이름/기관(학교)/반을 모두 선택해야 합니다."
    return None


@app.get("/api/users")
def list_users():
    result = []
    for user in users_store.list_users():
        public = users_store.public_user(user)
        public["state"] = scheduler.get_job_state(user["id"])
        public["next_run_at"] = scheduler.get_user_next_run(user["id"])
        result.append(public)
    return JSONResponse(result)


@app.post("/api/users")
def create_user(payload: dict):
    kidsnote_id = (payload.get("kidsnote_id") or "").strip()
    kidsnote_pw = payload.get("kidsnote_pw") or ""
    children = payload.get("children") or []
    times = payload.get("times") or []

    if not kidsnote_id or not kidsnote_pw:
        return JSONResponse({"error": "키즈노트 ID/PW를 입력하세요."}, status_code=400)

    error = _validate_children(children)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    error = _validate_times(times)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    user = users_store.add_user(kidsnote_id, kidsnote_pw, children, times)
    scheduler.register_user_schedule(user)
    return JSONResponse(users_store.public_user(user))


@app.delete("/api/users/{user_id}")
def remove_user(user_id: str):
    scheduler.unregister_user_schedule(user_id)
    scheduler.JOB_STATES.pop(user_id, None)
    deleted = users_store.delete_user(user_id)
    if not deleted:
        return JSONResponse({"error": "해당 사용자를 찾을 수 없습니다."}, status_code=404)
    return JSONResponse({"status": "deleted"})


@app.post("/api/users/{user_id}/run")
def run_user(user_id: str):
    if not users_store.get_user(user_id):
        return JSONResponse({"error": "해당 사용자를 찾을 수 없습니다."}, status_code=404)
    result = scheduler.trigger_job_run(user_id)
    if "error" in result:
        return JSONResponse(result, status_code=409)
    return JSONResponse(result)


@app.post("/api/verify-kidsnote")
def verify_kidsnote(payload: dict):
    kidsnote_id = (payload.get("kidsnote_id") or "").strip()
    kidsnote_pw = payload.get("kidsnote_pw") or ""
    if not kidsnote_id or not kidsnote_pw:
        return JSONResponse({"error": "키즈노트 ID/PW를 입력하세요."}, status_code=400)

    result = kidsnote_discovery.discover_account(kidsnote_id, kidsnote_pw)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "계정 확인에 실패했습니다.")}, status_code=422)
    return JSONResponse({"children": result["children"]})
