import os
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

KAKAO_CLIENT_ID = os.getenv("KAKAO_CLIENT_ID")
# 카카오 콘솔에서 "Client Secret 사용"을 켠 경우에만 필요(선택 사항).
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET")

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
USER_INFO_URL = "https://kapi.kakao.com/v2/user/me"


def is_configured() -> bool:
    return bool(KAKAO_CLIENT_ID)


def build_auth_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": KAKAO_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_user(code: str, redirect_uri: str) -> dict:
    """인가 코드를 토큰으로 교환하고, 그 토큰으로 카카오 사용자 고유 id/닉네임만 조회해 반환한다.

    구글 연동(google_oauth.py)과 달리 카카오 쪽은 "이 사람이 누구인지" 식별하는
    로그인 용도일 뿐 카카오 API를 대신 호출할 일이 없으므로, access_token 자체는
    저장하지 않고 이 함수 안에서 한 번 쓰고 버린다.
    """
    token_payload = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if KAKAO_CLIENT_SECRET:
        token_payload["client_secret"] = KAKAO_CLIENT_SECRET

    token_res = requests.post(TOKEN_URL, data=token_payload, timeout=10)
    token_res.raise_for_status()
    access_token = token_res.json()["access_token"]

    user_res = requests.get(
        USER_INFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    user_res.raise_for_status()
    profile = user_res.json()

    nickname = profile.get("kakao_account", {}).get("profile", {}).get("nickname")

    return {"kakao_user_id": str(profile["id"]), "nickname": nickname}
