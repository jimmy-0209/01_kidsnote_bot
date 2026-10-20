import json
import os
import time
import uuid

import boto3
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

import crypto_utils

# pipeline.py도 자체적으로 load_dotenv()를 호출하지만, import 순서에 따라
# 이 모듈이 먼저 로드될 수 있으므로 여기서도 독립적으로 호출한다
# (여러 번 호출해도 안전함).
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/calendar']

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

GOOGLE_OAUTH_TABLE_NAME = os.environ.get("DYNAMODB_GOOGLE_OAUTH_TABLE", "kidsnote_google_oauth_pending")
# 로그인 팝업 하나가 끝나는 데 걸리는 시간보다 넉넉하게 — 프론트가 최대 3분까지 폴링한다.
PENDING_RETENTION_MINUTES = 15

_dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION"))
_table = _dynamodb.Table(GOOGLE_OAUTH_TABLE_NAME)


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _build_flow(redirect_uri: str) -> Flow:
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)


def create_auth_url(redirect_uri: str, state: str | None = None) -> dict:
    """동의 화면 URL과 state 토큰을 생성한다. state는 CSRF 방지 + 이 로그인 시도를
    나중에 /api/auth/google/status로 조회하기 위한 키로 함께 쓰인다.

    프론트에서 팝업을 띄우기 전에 state를 미리 만들어 쿼리로 넘기면(마법사가
    팝업과 별개로 즉시 폴링을 시작할 수 있도록) 그 값을 그대로 사용하고,
    없으면 서버가 새로 생성한다.

    PKCE code_verifier는 DynamoDB에 저장해 둔다 — 이 웹 앱은 Vercel 서버리스로
    돌아 요청마다 다른 인스턴스가 처리할 수 있어서, 메모리에만 들고 있으면
    로그인 시작과 콜백이 다른 인스턴스에 걸렸을 때 "state 불일치" 오류가 난다.
    """
    flow = _build_flow(redirect_uri)
    state = state or uuid.uuid4().hex
    auth_url, _ = flow.authorization_url(
        access_type="offline",       # refresh_token을 받기 위해 필요
        include_granted_scopes="true",
        prompt="consent",            # 재인증 시에도 refresh_token이 매번 발급되도록 강제
        state=state,
    )
    _table.put_item(Item={
        "state": state,
        "code_verifier": flow.code_verifier,
        "redirect_uri": redirect_uri,
        "status": "pending",
        "credentials": None,
        "expires_at": int(time.time()) + PENDING_RETENTION_MINUTES * 60,
    })
    return {"auth_url": auth_url, "state": state}


def exchange_code(state: str, code: str, redirect_uri: str) -> bool:
    """콜백에서 받은 code를 토큰으로 교환해 DynamoDB에 저장해 둔다.

    create_auth_url()에서 저장해 둔 code_verifier를 새 Flow 인스턴스에 그대로
    복원해야 PKCE 검증이 통과한다 — 이 값 없이 새 Flow로 바로 교환하면 구글
    쪽에서 invalid_grant(Missing code verifier) 오류를 반환한다.
    """
    response = _table.get_item(Key={"state": state})
    item = response.get("Item")
    if item is None:
        return False

    flow = _build_flow(item.get("redirect_uri") or redirect_uri)
    flow.code_verifier = item["code_verifier"]
    flow.fetch_token(code=code)
    creds = flow.credentials
    creds_json = json.dumps({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    })
    _table.update_item(
        Key={"state": state},
        UpdateExpression="SET #s = :ready, credentials = :creds",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":ready": "ready", ":creds": crypto_utils.encrypt(creds_json)},
    )
    return True


def pop_credentials(state: str) -> dict | None:
    """state에 대응하는 credentials가 준비됐으면 꺼내 테이블에서 제거한다 (한 번만 조회 가능)."""
    response = _table.get_item(Key={"state": state})
    item = response.get("Item")
    if item is None or item.get("status") != "ready":
        return None
    _table.delete_item(Key={"state": state})
    return json.loads(crypto_utils.decrypt(item["credentials"]))


def peek_status(state: str) -> bool:
    """아직 꺼내지 않고 준비 여부만 확인 (폴링용)."""
    response = _table.get_item(Key={"state": state})
    item = response.get("Item")
    return item is not None and item.get("status") == "ready"
