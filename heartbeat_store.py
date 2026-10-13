import os
from datetime import datetime, timedelta, timezone

import boto3
from dotenv import load_dotenv

load_dotenv()

HEARTBEAT_TABLE_NAME = os.environ.get("DYNAMODB_HEARTBEAT_TABLE", "kidsnote_worker_heartbeat")

# worker.py가 지금은 PC 1대에서만 돈다고 가정하고 고정 키 하나만 쓴다.
WORKER_ID = "default"

# worker.py의 하트비트 주기(20초)보다 충분히 여유를 둬서, 폴링 타이밍 차이로
# 생기는 거짓 "꺼짐" 판정을 막는다.
STALE_AFTER_SECONDS = 90

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION"))
_table = _dynamodb.Table(HEARTBEAT_TABLE_NAME)


def ping() -> None:
    # ping()은 PC(KST)에서, get_status()는 Vercel(UTC)에서 실행된다 — naive
    # datetime.now()를 쓰면 두 시각이 9시간 어긋나 항상 "켜짐"으로 잘못 판정된다.
    # 타임존 정보를 명시해서 어느 쪽에서 실행되든 올바르게 비교되게 한다.
    _table.put_item(Item={
        "worker_id": WORKER_ID,
        "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def get_status() -> dict:
    response = _table.get_item(Key={"worker_id": WORKER_ID})
    item = response.get("Item")
    if not item:
        return {"online": False, "last_seen": None}

    last_seen = datetime.fromisoformat(item["last_seen"])
    online = (datetime.now(timezone.utc) - last_seen) < timedelta(seconds=STALE_AFTER_SECONDS)
    return {"online": online, "last_seen": item["last_seen"]}
