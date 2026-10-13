import os
from datetime import datetime, timedelta

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
    _table.put_item(Item={
        "worker_id": WORKER_ID,
        "last_seen": datetime.now().isoformat(timespec="seconds"),
    })


def get_status() -> dict:
    response = _table.get_item(Key={"worker_id": WORKER_ID})
    item = response.get("Item")
    if not item:
        return {"online": False, "last_seen": None}

    last_seen = datetime.fromisoformat(item["last_seen"])
    online = (datetime.now() - last_seen) < timedelta(seconds=STALE_AFTER_SECONDS)
    return {"online": online, "last_seen": item["last_seen"]}
