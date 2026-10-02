import contextlib
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import pipeline

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "run_log.txt"
SCHEDULE_CONFIG_PATH = BASE_DIR / "schedule_config.json"

# 기존 Windows 작업 스케줄러(KidsnoteBot_MonFri)가 실제로는 매일 21:00에 실행되고
# 있어(이름과 달리 요일 제한 없음), 그 동작을 기본값으로 그대로 반영한다.
# 다만 같은 시각에 두 메커니즘이 동시에 돌면 키즈노트에 중복 로그인하게 되므로
# 기본값은 비활성화(enabled=false)로 두고, 웹 스케줄러를 쓰기로 했다면 사용자가
# 직접 켠 뒤 기존 작업 스케줄러 쪽을 꺼야 한다.
DEFAULT_SCHEDULE = {"enabled": False, "times": ["21:00"]}

# 동시 실행 방지용 락. 스크래핑~캘린더 동기화가 수 분 걸리는 긴 작업이라
# 웹 요청 스레드가 아닌 별도 스레드에서 돌리고, 중복 트리거(수동+예약 겹침 포함)를 막는다.
RUN_LOCK = threading.Lock()
STATE = {
    "status": "idle",  # idle | running | success | error
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "trigger": None,  # "manual" | "schedule"
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


def load_schedule_config() -> dict:
    if not SCHEDULE_CONFIG_PATH.exists():
        return dict(DEFAULT_SCHEDULE)
    with open(SCHEDULE_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_schedule_config(config: dict) -> None:
    with open(SCHEDULE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _run_pipeline_job(trigger: str):
    acquired = RUN_LOCK.acquire(blocking=False)
    if not acquired:
        if trigger == "schedule":
            with open(LOG_PATH, "a", encoding="utf-8") as log_file:
                log_file.write(f"\n===== (예약 실행 건너뜀: 이미 실행 중) {datetime.now()} =====\n")
        return {"error": "이미 실행 중입니다."}

    def _target():
        STATE["status"] = "running"
        STATE["trigger"] = trigger
        STATE["last_started_at"] = datetime.now().isoformat(timespec="seconds")
        STATE["last_error"] = None
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as log_file:
                label = "웹에서 수동 실행" if trigger == "manual" else "예약 실행"
                log_file.write(f"\n===== ({label}) {datetime.now()} =====\n")
                with contextlib.redirect_stdout(_TeeWriter(sys.__stdout__, log_file)):
                    pipeline.run_all_children()
            STATE["status"] = "success"
        except Exception as e:
            STATE["status"] = "error"
            STATE["last_error"] = str(e)
        finally:
            STATE["last_finished_at"] = datetime.now().isoformat(timespec="seconds")
            RUN_LOCK.release()

    threading.Thread(target=_target, daemon=True).start()
    return {"status": "started"}


def trigger_manual_run():
    return _run_pipeline_job("manual")


def _scheduled_fire():
    _run_pipeline_job("schedule")


def _apply_jobs(config: dict):
    _scheduler.remove_all_jobs()
    if not config.get("enabled"):
        return
    for t in config.get("times", []):
        hour, minute = t.split(":")
        _scheduler.add_job(
            _scheduled_fire,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id=f"run_{t}",
            replace_existing=True,
        )


def get_schedule_status() -> dict:
    config = load_schedule_config()
    next_runs = sorted(
        job.next_run_time for job in _scheduler.get_jobs() if job.next_run_time
    )
    return {
        **config,
        "next_run_at": next_runs[0].isoformat(timespec="seconds") if next_runs else None,
    }


def update_schedule(config: dict) -> dict:
    save_schedule_config(config)
    _apply_jobs(config)
    return get_schedule_status()


def start():
    _apply_jobs(load_schedule_config())
    _scheduler.start()


def shutdown():
    _scheduler.shutdown(wait=False)
