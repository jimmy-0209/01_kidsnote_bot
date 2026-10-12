import threading
from datetime import datetime

from apscheduler.triggers.interval import IntervalTrigger

import discovery_request_store
import kidsnote_discovery
import pipeline
import run_history_store
import scheduler
import users_store


def _execute(user_id: str, trigger: str, started_at: str):
    run_history_store.mark_running(user_id, started_at)
    label = "웹에서 수동 실행" if trigger == "manual" else "예약 실행"
    print(f"\n===== ({label}: {user_id}) {datetime.now()} =====")

    results = []
    error_msg = None
    try:
        if user_id == scheduler.LEGACY_JOB_ID:
            results = pipeline.run_all_children() or []
        else:
            user = users_store.get_user(user_id)
            if not user:
                raise ValueError(f"사용자를 찾을 수 없습니다: {user_id}")
            results = pipeline.run_children_for_user(user) or []
        history_status = "success"
    except Exception as e:
        error_msg = str(e)
        history_status = "error"

    registered = [
        {"label": r.get("label"), **ev}
        for r in results for ev in r.get("registered", [])
    ]
    child_errors = [f"{r.get('label')}: {r['error']}" for r in results if r.get("error")]
    if not error_msg and child_errors:
        history_status = "partial_error"

    run_history_store.finalize(
        user_id=user_id,
        started_at=started_at,
        status=history_status,
        error="; ".join(([error_msg] if error_msg else []) + child_errors) or None,
        registered=registered,
    )


def _run_loop():
    while True:
        user_id, trigger, started_at = scheduler._RUN_QUEUE.get()
        try:
            _execute(user_id, trigger, started_at)
        finally:
            scheduler._RUN_QUEUE.task_done()


def _poll_discovery_requests():
    """회원가입 1단계(계정 인증)도 Playwright가 필요해 Vercel에서 못 돌리므로,
    같은 '대기 레코드를 찾아 처리' 패턴으로 이 워커가 대신 실행한다.
    """
    for item in discovery_request_store.list_queued():
        request_id = item["request_id"]
        discovery_request_store.mark_running(request_id)
        print(f"\n===== (계정 인증 요청 처리: {request_id}) {datetime.now()} =====")
        creds = discovery_request_store.get_credentials(request_id)
        if not creds:
            continue
        kidsnote_id, kidsnote_pw = creds
        result = kidsnote_discovery.discover_account(kidsnote_id, kidsnote_pw)
        if result.get("ok"):
            discovery_request_store.finalize(request_id, "success", {"children": result["children"]}, None)
        else:
            discovery_request_store.finalize(request_id, "error", None, result.get("error", "계정 확인에 실패했습니다."))


def main():
    threading.Thread(target=_run_loop, daemon=True).start()
    scheduler.start()
    scheduler._scheduler.add_job(
        _poll_discovery_requests, IntervalTrigger(seconds=5), id="poll_discovery", replace_existing=True
    )
    print("워커 실행 중 — 예약된 시각/수동 실행/계정 인증 요청을 처리합니다. (Ctrl+C로 종료)")
    threading.Event().wait()


if __name__ == "__main__":
    main()
