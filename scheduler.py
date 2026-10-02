import contextlib
import json
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import jobs_store
import pipeline

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "run_log.txt"
SCHEDULE_CONFIG_PATH = BASE_DIR / "schedule_config.json"

LEGACY_JOB_ID = "legacy"

# 기존 Windows 작업 스케줄러(KidsnoteBot_MonFri)가 실제로는 매일 21:00에 실행되고
# 있어(이름과 달리 요일 제한 없음), 그 동작을 기본값으로 그대로 반영한다.
# 다만 같은 시각에 두 메커니즘이 동시에 돌면 키즈노트에 중복 로그인하게 되므로
# 기본값은 비활성화(enabled=false)로 두고, 웹 스케줄러를 쓰기로 했다면 사용자가
# 직접 켠 뒤 기존 작업 스케줄러 쪽을 꺼야 한다.
DEFAULT_SCHEDULE = {"enabled": False, "times": ["21:00"]}

# 실행 요청을 순서대로 처리하는 큐 + 워커 스레드 1개.
# 여러 사용자의 스케줄이 같은 시각에 겹치더라도 Playwright 브라우저가
# 동시에 여러 개 뜨지 않도록 항상 하나씩만(FIFO) 처리한다.
_RUN_QUEUE: "queue.Queue[tuple[str, str]]" = queue.Queue()

# job_id -> {status, last_started_at, last_finished_at, last_error, trigger}
# status: idle | queued | running | success | error
# legacy(.env 기반 단일 사용자 흐름)는 LEGACY_JOB_ID 키로 관리한다.
JOB_STATES: dict[str, dict] = {
    LEGACY_JOB_ID: {
        "status": "idle",
        "last_started_at": None,
        "last_finished_at": None,
        "last_error": None,
        "trigger": None,
    }
}

_scheduler = BackgroundScheduler(daemon=True)


class _TeeWriter:
    """print() 출력을 콘솔과 run_log.txt 양쪽에 동시에 기록한다.

    CLI 실행(run_kidsnote_bot.bat)은 이미 쉘 리다이렉션(`>> run_log.txt`)으로
    로그를 남기므로 pipeline.py 자체는 건드리지 않는다. 반면 웹/스케줄러로
    트리거한 실행은 그런 리다이렉션이 없으므로, 여기서만 run_log.txt에 이어쓰기 해
    대시보드가 어느 경로로 실행됐든 같은 로그 파일을 그대로 보여주게 한다.
    """

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)

    def flush(self):
        for s in self._streams:
            s.flush()


def get_job_state(job_id: str) -> dict:
    return _get_state(job_id)


def _get_state(job_id: str) -> dict:
    return JOB_STATES.setdefault(job_id, {
        "status": "idle",
        "last_started_at": None,
        "last_finished_at": None,
        "last_error": None,
        "trigger": None,
    })


def load_schedule_config() -> dict:
    if not SCHEDULE_CONFIG_PATH.exists():
        return dict(DEFAULT_SCHEDULE)
    with open(SCHEDULE_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule_config(config: dict) -> None:
    with open(SCHEDULE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def enqueue_run(job_id: str, trigger: str) -> dict:
    state = _get_state(job_id)
    if state["status"] in ("queued", "running"):
        return {"error": "이미 대기 중이거나 실행 중입니다."}
    state["status"] = "queued"
    state["trigger"] = trigger
    _RUN_QUEUE.put((job_id, trigger))
    return {"status": "queued"}


def trigger_manual_run():
    return enqueue_run(LEGACY_JOB_ID, "manual")


def trigger_job_run(job_id: str):
    return enqueue_run(job_id, "manual")


def _execute(job_id: str, trigger: str):
    state = _get_state(job_id)
    state["status"] = "running"
    state["last_started_at"] = datetime.now().isoformat(timespec="seconds")
    state["last_error"] = None
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            if job_id == LEGACY_JOB_ID:
                label = "웹에서 수동 실행" if trigger == "manual" else "예약 실행"
            else:
                label = f"job:{job_id} " + ("수동 실행" if trigger == "manual" else "예약 실행")
            log_file.write(f"\n===== ({label}) {datetime.now()} =====\n")
            with contextlib.redirect_stdout(_TeeWriter(sys.__stdout__, log_file)):
                if job_id == LEGACY_JOB_ID:
                    pipeline.run_all_children()
                else:
                    job = jobs_store.get_job(job_id)
                    if not job:
                        raise ValueError(f"job을 찾을 수 없습니다: {job_id}")
                    pipeline.run_single_job(job)
        state["status"] = "success"
    except Exception as e:
        state["status"] = "error"
        state["last_error"] = str(e)
    finally:
        state["last_finished_at"] = datetime.now().isoformat(timespec="seconds")


def _worker_loop():
    while True:
        job_id, trigger = _RUN_QUEUE.get()
        try:
            _execute(job_id, trigger)
        finally:
            _RUN_QUEUE.task_done()


def _scheduled_fire(job_id: str):
    result = enqueue_run(job_id, "schedule")
    if "error" in result:
        with open(LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n===== (예약 실행 건너뜀: {job_id} 이미 대기/실행 중) {datetime.now()} =====\n")


def _apply_legacy_jobs(config: dict):
    for existing in _scheduler.get_jobs():
        if existing.id.startswith("legacy_"):
            _scheduler.remove_job(existing.id)
    if not config.get("enabled"):
        return
    for t in config.get("times", []):
        hour, minute = t.split(":")
        _scheduler.add_job(
            _scheduled_fire,
            CronTrigger(hour=int(hour), minute=int(minute)),
            args=[LEGACY_JOB_ID],
            id=f"legacy_{t}",
            replace_existing=True,
        )


def register_job_schedule(job: dict):
    """job 하나의 스케줄 시각들에 맞춰 cron 트리거를 등록한다 (기존 등록은 먼저 제거)."""
    unregister_job_schedule(job["id"])
    schedule = job.get("schedule", {})
    if not schedule.get("enabled", True):
        return
    for t in schedule.get("times", []):
        hour, minute = t.split(":")
        _scheduler.add_job(
            _scheduled_fire,
            CronTrigger(hour=int(hour), minute=int(minute)),
            args=[job["id"]],
            id=f"job_{job['id']}_{t}",
            replace_existing=True,
        )


def unregister_job_schedule(job_id: str):
    for existing in _scheduler.get_jobs():
        if existing.id.startswith(f"job_{job_id}_"):
            _scheduler.remove_job(existing.id)


def get_schedule_status() -> dict:
    config = load_schedule_config()
    next_runs = sorted(
        job.next_run_time
        for job in _scheduler.get_jobs()
        if job.id.startswith("legacy_") and job.next_run_time
    )
    return {
        **config,
        "next_run_at": next_runs[0].isoformat(timespec="seconds") if next_runs else None,
    }


def update_schedule(config: dict) -> dict:
    save_schedule_config(config)
    _apply_legacy_jobs(config)
    return get_schedule_status()


def get_job_next_run(job_id: str) -> str | None:
    next_runs = sorted(
        job.next_run_time
        for job in _scheduler.get_jobs()
        if job.id.startswith(f"job_{job_id}_") and job.next_run_time
    )
    return next_runs[0].isoformat(timespec="seconds") if next_runs else None


def start():
    _apply_legacy_jobs(load_schedule_config())
    for job in jobs_store.list_jobs():
        register_job_schedule(job)
    threading.Thread(target=_worker_loop, daemon=True).start()
    _scheduler.start()


def shutdown():
    _scheduler.shutdown(wait=False)
