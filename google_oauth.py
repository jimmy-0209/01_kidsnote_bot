import os
import uuid

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

# pipeline.py도 자체적으로 load_dotenv()를 호출하지만, import 순서에 따라
# 이 모듈이 먼저 로드될 수 있으므로 여기서도 독립적으로 호출한다
# (여러 번 호출해도 안전함).
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/calendar']

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# state 토큰 -> 교환된 credentials dict (아직 교환 전이면 None).
# 프로토타입 단계라 영구 저장소 없이 서버 메모리에만 짧게 보관하고,
# /api/users 등록(2단계)에서 실제로 꺼내 쓰면 바로 비워낸다.
_PENDING: dict[str, dict | None] = {}


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
    """
    flow = _build_flow(redirect_uri)
    state = state or uuid.uuid4().hex
    auth_url, _ = flow.authorization_url(
        access_type="offline",       # refresh_token을 받기 위해 필요
        include_granted_scopes="true",
        prompt="consent",            # 재인증 시에도 refresh_token이 매번 발급되도록 강제
        state=state,
    )
    _PENDING[state] = None
    return {"auth_url": auth_url, "state": state}


def exchange_code(state: str, code: str, redirect_uri: str) -> bool:
    """콜백에서 받은 code를 토큰으로 교환해 _PENDING[state]에 저장한다."""
    if state not in _PENDING:
        return False
    flow = _build_flow(redirect_uri)
    flow.fetch_token(code=code)
    creds = flow.credentials
    _PENDING[state] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    return True


def pop_credentials(state: str) -> dict | None:
    """state에 대응하는 credentials가 준비됐으면 꺼내 _PENDING에서 제거한다 (한 번만 조회 가능)."""
    if state not in _PENDING or _PENDING[state] is None:
        return None
    return _PENDING.pop(state)


def peek_status(state: str) -> bool:
    """아직 꺼내지 않고 준비 여부만 확인 (폴링용)."""
    return state in _PENDING and _PENDING[state] is not None
