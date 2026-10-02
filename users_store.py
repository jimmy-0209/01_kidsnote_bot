import json
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
USERS_CONFIG_PATH = BASE_DIR / "users_config.json"


def _load() -> dict:
    if not USERS_CONFIG_PATH.exists():
        return {"users": []}
    with open(USERS_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"users": data.get("users", [])}


def _save(data: dict) -> None:
    with open(USERS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_users() -> list[dict]:
    return _load()["users"]


def get_user(user_id: str) -> dict | None:
    for user in list_users():
        if user["id"] == user_id:
            return user
    return None


def add_user(kidsnote_id: str, kidsnote_pw: str, children: list[dict], times: list[str],
             google_credentials: dict | None = None) -> dict:
    """사용자 한 명(= 키즈노트 계정 하나)을 등록한다.

    children: [{"child_name", "school", "class", "institution_testid", "keywords"}, ...]
    입력 순서가 곧 로그인 시 처리 순서(index)이며, pipeline.run_children_for_user가
    index==0은 소속만 전환, index>0은 이름 전환 후 소속 전환으로 분기한다.

    google_credentials: 구글 OAuth로 위임받은 토큰 dict(선택). 비어 있으면
    pipeline._get_calendar_service가 기존 단일 계정용 token.json으로 대체 동작한다.
    """
    data = _load()
    user = {
        "id": uuid.uuid4().hex[:8],
        "kidsnote_id": kidsnote_id,
        "kidsnote_pw": kidsnote_pw,
        "children": children,
        "schedule": {"enabled": True, "times": times},
        "google_credentials": google_credentials,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    data["users"].append(user)
    _save(data)
    return user


def update_user(user_id: str, times: list[str] | None = None,
                 children_keywords: list[list[str]] | None = None) -> dict | None:
    """스케줄 시각과 자녀별 키워드만 선택적으로 업데이트한다.

    kidsnote_id/kidsnote_pw, 각 자녀의 child_name/school/class/institution_testid,
    google_credentials는 절대 건드리지 않는다. children_keywords는 user["children"]과
    같은 순서(index)로 정렬된 키워드 리스트의 리스트여야 하며, 길이가 다르면 짧은 쪽
    기준으로만 매칭한다(자녀 추가/삭제는 이 함수의 책임이 아님).
    """
    data = _load()
    for user in data["users"]:
        if user["id"] != user_id:
            continue
        if times is not None:
            user["schedule"]["times"] = times
        if children_keywords is not None:
            for child, keywords in zip(user["children"], children_keywords):
                child["keywords"] = keywords
        _save(data)
        return user
    return None


def delete_user(user_id: str) -> bool:
    data = _load()
    before = len(data["users"])
    data["users"] = [u for u in data["users"] if u["id"] != user_id]
    _save(data)
    return len(data["users"]) < before


def public_user(user: dict) -> dict:
    """비밀번호와 구글 OAuth 토큰(refresh_token 등 민감정보)을 제외한 사용자 정보 (API 응답용).

    대신 연동 여부만 불리언으로 노출해 대시보드가 "연동됨/안됨" 표시는 할 수 있게 한다.
    """
    public = {k: v for k, v in user.items() if k not in ("kidsnote_pw", "google_credentials")}
    public["has_google_calendar"] = bool(user.get("google_credentials"))
    return public
