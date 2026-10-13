import queue
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import run_history_store
import users_store

# 이 모듈은 pipeline.py(Playwright, PyMuPDF 등 무거운 의존성)를 절대 import하지
# 않는다 — web_app.py가 이 모듈을 쓰는데, Vercel로 옮겨질 예정이라 그쪽에서 Playwright
# 바이너리를 번들링하면 배포 크기 제한에 걸린다. 실제 실행 로직(pipeline 호출)은
# worker.py에만 있고, 여기 있는 _RUN_QUEUE는 worker.py가 직접 꺼내 쓴다.

# 실행 요청을 순서대로 처리하는 큐 + 워커 스레드 1개 (이 프로세스, 즉 worker.py 안에서만 쓰임).
# 여러 사용자의 스케줄이 같은 시각에 겹치더라도 Playwright 브라우저가
# 동시에 여러 개 뜨지 않도록 항상 하나씩만(FIFO) 처리한다.
_RUN_QUEUE: "queue.Queue[tuple[str, str, str]]" = queue.Queue()

_scheduler = BackgroundScheduler(daemon=True)


def get_job_state(user_id: str) -> dict:
    return run_history_store.get_current_state(user_id)


def trigger_job_run(user_id: str) -> dict:
    return run_history_store.request_run(user_id, "manual")


KST = timezone(timedelta(hours=9))

# 시각 항목은 {"time": "HH:MM", "days": "all"|"weekdays"|"weekend"} 형태다.
# 과거 데이터는 그냥 "HH:MM" 문자열이라(요일 구분 없이 매일) 어디서든 이 함수로
# 정규화해서 쓴다.
_CRON_DAY_OF_WEEK = {"all": "*", "weekdays": "mon-fri", "weekend": "sat,sun"}
_WEEKDAY_SETS = {"all": None, "weekdays": {0, 1, 2, 3, 4}, "weekend": {5, 6}}


def _normalize_time_entry(entry) -> dict:
    if isinstance(entry, str):
        return {"time": entry, "days": "all"}
    return {"time": entry.get("time"), "days": entry.get("days", "all")}


def compute_next_run(time_entries: list) -> str | None:
    """APScheduler 라이브 인스턴스를 들여다보지 않고 time_entries만으로 다음
    실행 시각을 계산한다 — 이래야 워커의 스케줄러 인스턴스를 갖고 있지 않은
    웹 프로세스(Vercel, UTC)에서도 "다음 실행" 표시가 가능하다.

    naive datetime.now()는 이 함수를 호출하는 프로세스의 서버 로컬 시각을
    반환하므로(Vercel=UTC, PC=KST), 반드시 KST를 명시해서 어디서 호출되든
    같은 결과가 나오게 한다.
    """
    if not time_entries:
        return None
    now = datetime.now(KST)
    candidates = []
    for raw_entry in time_entries:
        entry = _normalize_time_entry(raw_entry)
        hour, _, minute = entry["time"].partition(":")
        allowed_weekdays = _WEEKDAY_SETS.get(entry["days"])
        candidate = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        if allowed_weekdays is not None:
            while candidate.weekday() not in allowed_weekdays:
                candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates).isoformat(timespec="seconds")


def _poll_queued():
    """DynamoDB에 쌓인 '대기' 요청(웹 대시보드에서 올린 수동 실행 포함)을 찾아
    로컬 실행 큐에 넣는다. 웹 프로세스와 워커 프로세스가 분리되어 있어도 이
    폴링이 둘을 연결하는 유일한 통로다.
    """
    for item in run_history_store.list_queued():
        user_id = item["user_id"]
        started_at = item["started_at"]
        trigger = item.get("trigger", "manual")
        run_history_store.mark_running(user_id, started_at)
        _RUN_QUEUE.put((user_id, trigger, started_at))


def _scheduled_fire(user_id: str):
    result = run_history_store.request_run(user_id, "schedule")
    if "error" in result:
        print(f"\n===== (예약 실행 건너뜀: {user_id} 이미 대기/실행 중) {datetime.now()} =====")


def register_user_schedule(user: dict):
    """사용자 한 명의 공유 스케줄 시각들에 맞춰 cron 트리거를 등록한다 (기존 등록은 먼저 제거)."""
    unregister_user_schedule(user["id"])
    schedule = user.get("schedule", {})
    if not schedule.get("enabled", True):
        return
    for i, raw_entry in enumerate(schedule.get("times", [])):
        entry = _normalize_time_entry(raw_entry)
        hour, minute = entry["time"].split(":")
        day_of_week = _CRON_DAY_OF_WEEK.get(entry["days"], "*")
        _scheduler.add_job(
            _scheduled_fire,
            CronTrigger(hour=int(hour), minute=int(minute), day_of_week=day_of_week),
            args=[user["id"]],
            id=f"user_{user['id']}_{i}",
            replace_existing=True,
        )


def unregister_user_schedule(user_id: str):
    for existing in _scheduler.get_jobs():
        if existing.id.startswith(f"user_{user_id}_"):
            _scheduler.remove_job(existing.id)


def _resync_schedules():
    """DynamoDB 기준으로 cron 등록을 주기적으로 다시 맞춘다.

    웹 프로세스(Vercel)와 워커 프로세스가 분리되어 사용자 등록/수정/삭제가
    이 워커가 아닌 다른 프로세스에서 일어나므로, 웹 쪽이 register_user_schedule을
    직접 호출할 수 없다. 대신 이 워커가 주기적으로 DB를 다시 읽어 cron 목록을
    맞춘다 — 최악의 경우 반영까지 이 주기만큼 지연된다.
    """
    users = users_store.list_users()
    current_ids = {u["id"] for u in users}
    for user in users:
        register_user_schedule(user)

    for job in _scheduler.get_jobs():
        if job.id.startswith("user_"):
            job_user_id = job.id.split("_")[1]
            if job_user_id not in current_ids:
                _scheduler.remove_job(job.id)


def get_user_next_run(user_id: str) -> str | None:
    user = users_store.get_user(user_id)
    if not user or not user.get("schedule", {}).get("enabled", True):
        return None
    return compute_next_run(user["schedule"].get("times", []))


def start():
    """워커 프로세스(worker.py)에서만 호출해야 한다 — 웹 프로세스에서 호출하면
    사용자 CRUD가 이 프로세스가 아닌 워커 프로세스의 스케줄러에 반영돼야 하는데
    실제로는 반영되지 않아 두 프로세스의 스케줄 상태가 어긋난다.

    실제 작업 실행 스레드(_RUN_QUEUE를 꺼내 pipeline을 호출하는 쪽)는 worker.py가
    이 함수 호출 전에 따로 띄운다 — 여기서는 cron 등록과 큐에 채워넣는 폴링만 한다.
    """
    _resync_schedules()
    _scheduler.add_job(_poll_queued, IntervalTrigger(seconds=10), id="poll_queued", replace_existing=True)
    _scheduler.add_job(_resync_schedules, IntervalTrigger(seconds=60), id="resync_schedules", replace_existing=True)
    _scheduler.start()


def shutdown():
    _scheduler.shutdown(wait=False)
