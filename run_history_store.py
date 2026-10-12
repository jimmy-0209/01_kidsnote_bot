import os
import time
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Attr, Key
from dotenv import load_dotenv

load_dotenv()

HISTORY_TABLE_NAME = os.environ.get("DYNAMODB_RUN_HISTORY_TABLE", "kidsnote_run_history")

# 로그 파일 대신 "최근 실행 이력"만 짧게 보관한다. 긴 보관이 필요 없으므로
# expires_at(TTL)로 일정 기간 뒤 DynamoDB가 자동 삭제 — 별도 정리/보관 불필요.
HISTORY_RETENTION_DAYS = 30

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION"))
_table = _dynamodb.Table(HISTORY_TABLE_NAME)


def request_run(user_id: str, trigger: str) -> dict:
    """실행을 요청한다 — "대기(queued)" 레코드 하나를 만들어 두는 게 끝이다.

    Phase 5(워커 분리) 이후 웹 프로세스(나중엔 Vercel)와 워커 프로세스는 서로
    직접 통신하지 못하므로, 이 테이블의 레코드 하나가 "지금 상태를 보여주는 판"과
    "실행해야 할 작업 큐" 역할을 동시에 겸한다 — 워커가 주기적으로 status=='queued'인
    레코드를 찾아(list_queued) 실행한다. cron 스케줄 발화(scheduler._scheduled_fire)도
    동일한 경로를 사용해 두 트리거(수동/예약)가 같은 메커니즘으로 동작한다.
    """
    current = get_current_state(user_id)
    if current["status"] in ("queued", "running"):
        return {"error": "이미 대기 중이거나 실행 중입니다."}

    started_at = datetime.now().isoformat(timespec="seconds")
    _table.put_item(Item={
        "user_id": user_id,
        "started_at": started_at,
        "finished_at": None,
        "status": "queued",
        "error": None,
        "registered": [],
        "trigger": trigger,
        "expires_at": int(time.time()) + HISTORY_RETENTION_DAYS * 86400,
    })
    return {"status": "queued", "started_at": started_at}


def list_queued() -> list[dict]:
    """모든 사용자에 걸쳐 아직 처리되지 않은 '대기' 요청을 찾는다 (워커의 폴링용).

    테이블이 작아(개인용 서비스 규모) scan + 필터로도 비용/성능에 문제가 없다.
    """
    items = []
    kwargs = {"FilterExpression": Attr("status").eq("queued")}
    response = _table.scan(**kwargs)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = _table.scan(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)
        items.extend(response.get("Items", []))
    return items


def mark_running(user_id: str, started_at: str) -> None:
    _table.update_item(
        Key={"user_id": user_id, "started_at": started_at},
        UpdateExpression="SET #s = :running",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":running": "running"},
    )


def finalize(user_id: str, started_at: str, status: str, error: str | None, registered: list[dict]) -> None:
    """request_run()이 만들어 둔 같은 레코드(user_id, started_at)를 최종 결과로 덮어쓴다."""
    _table.update_item(
        Key={"user_id": user_id, "started_at": started_at},
        UpdateExpression="SET #s = :status, finished_at = :finished_at, #e = :error, registered = :registered",
        ExpressionAttributeNames={"#s": "status", "#e": "error"},
        ExpressionAttributeValues={
            ":status": status,
            ":finished_at": datetime.now().isoformat(timespec="seconds"),
            ":error": error,
            ":registered": registered,
        },
    )


def get_current_state(user_id: str) -> dict:
    """대시보드의 실시간 상태 표시용 — 예전 scheduler.JOB_STATES(프로세스 메모리)를
    대체한다. 웹 프로세스와 워커 프로세스가 분리돼도 이 DynamoDB 레코드만 보면
    되므로 프로세스 경계를 넘어 상태를 공유할 수 있다.
    """
    items = list_runs(user_id, limit=1)
    if not items:
        return {"status": "idle", "last_started_at": None, "last_finished_at": None, "last_error": None, "trigger": None}
    latest = items[0]
    return {
        "status": latest.get("status", "idle"),
        "last_started_at": latest.get("started_at"),
        "last_finished_at": latest.get("finished_at"),
        "last_error": latest.get("error"),
        "trigger": latest.get("trigger"),
    }


def list_runs(user_id: str, limit: int = 10) -> list[dict]:
    response = _table.query(
        KeyConditionExpression=Key("user_id").eq(user_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])
