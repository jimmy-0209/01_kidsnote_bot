import os
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Attr
from dotenv import load_dotenv

import crypto_utils

load_dotenv()

DISCOVERY_TABLE_NAME = os.environ.get("DYNAMODB_DISCOVERY_TABLE", "kidsnote_discovery_requests")

# 계정 인증(kidsnote_discovery.discover_account)도 Playwright가 필요해서 Vercel에서
# 못 돌리므로, Phase 5의 실행 큐와 같은 패턴(DynamoDB 레코드 하나가 "요청+상태")으로
# PC 워커에 위임한다. 회원가입 중 잠깐 쓰고 버리는 데이터라 TTL을 짧게 둔다.
REQUEST_RETENTION_MINUTES = 30

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION"))
_table = _dynamodb.Table(DISCOVERY_TABLE_NAME)


def create_request(kidsnote_id: str, kidsnote_pw: str) -> str:
    request_id = uuid.uuid4().hex
    _table.put_item(Item={
        "request_id": request_id,
        "kidsnote_id": kidsnote_id,
        "kidsnote_pw": crypto_utils.encrypt(kidsnote_pw),
        "status": "queued",
        "result": None,
        "error": None,
        "expires_at": int(time.time()) + REQUEST_RETENTION_MINUTES * 60,
    })
    return request_id


def list_queued() -> list[dict]:
    """워커의 폴링용 — 테이블이 작아 scan + 필터로도 충분하다."""
    items = []
    kwargs = {"FilterExpression": Attr("status").eq("queued")}
    response = _table.scan(**kwargs)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = _table.scan(ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs)
        items.extend(response.get("Items", []))
    return items


def get_credentials(request_id: str) -> tuple[str, str] | None:
    response = _table.get_item(Key={"request_id": request_id})
    item = response.get("Item")
    if not item:
        return None
    return item["kidsnote_id"], crypto_utils.decrypt(item["kidsnote_pw"])


def mark_running(request_id: str) -> None:
    _table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET #s = :running",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":running": "running"},
    )


def finalize(request_id: str, status: str, result: dict | None, error: str | None) -> None:
    _table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET #s = :status, #r = :result, #e = :error",
        ExpressionAttributeNames={"#s": "status", "#r": "result", "#e": "error"},
        ExpressionAttributeValues={":status": status, ":result": result, ":error": error},
    )


def get_status(request_id: str) -> dict | None:
    """kidsnote_pw는 절대 돌려주지 않는다 — 조회 결과는 그대로 API 응답으로 나가는
    공개용(public) 형태라고 보고 설계한다."""
    response = _table.get_item(Key={"request_id": request_id})
    item = response.get("Item")
    if not item:
        return None
    return {
        "status": item.get("status"),
        "result": item.get("result"),
        "error": item.get("error"),
    }
