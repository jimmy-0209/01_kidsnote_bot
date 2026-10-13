import json
import os
import uuid
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Key
from dotenv import load_dotenv

import crypto_utils

load_dotenv()

USERS_TABLE_NAME = os.environ.get("DYNAMODB_USERS_TABLE", "kidsnote_users")
KAKAO_USER_ID_INDEX = "kakao_user_id-index"

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION"))
_table = _dynamodb.Table(USERS_TABLE_NAME)


def _encrypt_for_storage(user: dict) -> dict:
    """DynamoDB에 쓰기 전 kidsnote_pw/google_credentials를 암호화한다. put_item을
    호출하는 모든 곳(add_user/update_user/claim_user)은 반드시 이 함수를 통과한
    dict를 저장해야 한다 — 그렇지 않으면 get_user()가 돌려준(복호화된) 평문이
    그대로 다시 저장된다.
    """
    stored = dict(user)
    if stored.get("kidsnote_pw"):
        stored["kidsnote_pw"] = crypto_utils.encrypt(stored["kidsnote_pw"])
    if stored.get("google_credentials"):
        stored["google_credentials"] = crypto_utils.encrypt(json.dumps(stored["google_credentials"]))
    if not stored.get("kakao_user_id"):
        # kakao_user_id-index GSI의 파티션 키라서 NULL 타입으로 쓰면 DynamoDB가
        # ValidationException을 낸다 — 아예 속성 자체를 빼야 "이 인덱스에 없는
        # 항목"으로 정상 처리된다(sparse index).
        stored.pop("kakao_user_id", None)
    return stored


def _decrypt_from_storage(user: dict | None) -> dict | None:
    if user is None:
        return None
    user = dict(user)
    if user.get("kidsnote_pw"):
        user["kidsnote_pw"] = crypto_utils.decrypt(user["kidsnote_pw"])
    if user.get("google_credentials"):
        user["google_credentials"] = json.loads(crypto_utils.decrypt(user["google_credentials"]))
    return user


def list_users() -> list[dict]:
    items = []
    response = _table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = _table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return [_decrypt_from_storage(item) for item in items]


def get_user(user_id: str) -> dict | None:
    response = _table.get_item(Key={"id": user_id})
    return _decrypt_from_storage(response.get("Item"))


def get_user_by_kakao_id(kakao_user_id: str) -> dict | None:
    """카카오 로그인으로 식별된 사용자 고유 id로 등록된 사용자를 찾는다.

    GSI(kakao_user_id-index)로 조회하므로 사용자 수가 늘어나도 전체 scan 없이
    O(1)에 가깝게 찾는다. kakao_user_id가 없는(레거시/미연결) 레코드는 GSI
    파티션 키 값이 없어 자동으로 인덱스에서 빠지므로 별도 필터링이 필요 없다.
    """
    response = _table.query(
        IndexName=KAKAO_USER_ID_INDEX,
        KeyConditionExpression=Key("kakao_user_id").eq(kakao_user_id),
        Limit=1,
    )
    items = response.get("Items", [])
    return _decrypt_from_storage(items[0]) if items else None


def add_user(kidsnote_id: str, kidsnote_pw: str, children: list[dict], times: list[str],
             google_credentials: dict | None = None, kakao_user_id: str | None = None) -> dict:
    """사용자 한 명(= 키즈노트 계정 하나)을 등록한다.

    children: [{"child_name", "school", "class", "institution_testid", "keywords"}, ...]
    입력 순서가 곧 로그인 시 처리 순서(index)이며, pipeline.run_children_for_user가
    index==0은 소속만 전환, index>0은 이름 전환 후 소속 전환으로 분기한다.

    google_credentials: 구글 OAuth로 위임받은 토큰 dict(선택). 비어 있으면
    pipeline._get_calendar_service가 기존 단일 계정용 token.json으로 대체 동작한다.

    kakao_user_id: 이 사용자를 등록한 카카오 로그인 계정의 고유 id. 이 값이 있는
    레코드만 web_app.py에서 "소유자만 수정/삭제/실행 가능" 체크 대상이 된다 —
    DynamoDB 마이그레이션 이전에 만들어진 레코드는 이 값이 없어 그 체크를
    건너뛰므로(하위 호환), 기존 단일 사용자는 카카오 연동 없이도 계속 동작한다.
    """
    user = {
        "id": uuid.uuid4().hex[:8],
        "kidsnote_id": kidsnote_id,
        "kidsnote_pw": kidsnote_pw,
        "children": children,
        "schedule": {"enabled": True, "times": times},
        "google_credentials": google_credentials,
        "kakao_user_id": kakao_user_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    _table.put_item(Item=_encrypt_for_storage(user))
    return user


def update_user(user_id: str, times: list[str] | None = None,
                 children_keywords: list[list[str]] | None = None) -> dict | None:
    """스케줄 시각과 자녀별 키워드만 선택적으로 업데이트한다.

    kidsnote_id/kidsnote_pw, 각 자녀의 child_name/school/class/institution_testid,
    google_credentials는 절대 건드리지 않는다. children_keywords는 user["children"]과
    같은 순서(index)로 정렬된 키워드 리스트의 리스트여야 하며, 길이가 다르면 짧은 쪽
    기준으로만 매칭한다(자녀 추가/삭제는 이 함수의 책임이 아님).
    """
    user = get_user(user_id)
    if user is None:
        return None
    if times is not None:
        user["schedule"]["times"] = times
    if children_keywords is not None:
        for child, keywords in zip(user["children"], children_keywords):
            child["keywords"] = keywords
    _table.put_item(Item=_encrypt_for_storage(user))
    return user


def claim_user(user_id: str, kakao_user_id: str) -> dict | None:
    """kakao_user_id가 없는(DynamoDB 마이그레이션 이전) 레코드를 로그인한 카카오
    계정에 1회 연결한다. 이미 다른 카카오 계정에 연결돼 있으면 덮어쓰지 않는다.
    """
    user = get_user(user_id)
    if user is None or user.get("kakao_user_id"):
        return None
    user["kakao_user_id"] = kakao_user_id
    _table.put_item(Item=_encrypt_for_storage(user))
    return user


def delete_user(user_id: str) -> bool:
    existing = get_user(user_id)
    if existing is None:
        return False
    _table.delete_item(Key={"id": user_id})
    return True


def public_user(user: dict) -> dict:
    """비밀번호와 구글 OAuth 토큰(refresh_token 등 민감정보)을 제외한 사용자 정보 (API 응답용).

    대신 연동 여부만 불리언으로 노출해 대시보드가 "연동됨/안됨" 표시는 할 수 있게 한다.
    """
    public = {k: v for k, v in user.items() if k not in ("kidsnote_pw", "google_credentials", "kakao_user_id")}
    public["has_google_calendar"] = bool(user.get("google_credentials"))
    return public
