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


def add_user(kidsnote_id: str, kidsnote_pw: str, children: list[dict], times: list[str]) -> dict:
    """사용자 한 명(= 키즈노트 계정 하나)을 등록한다.

    children: [{"child_name", "school", "class", "institution_testid", "keywords"}, ...]
    입력 순서가 곧 로그인 시 처리 순서(index)이며, pipeline.run_children_for_user가
    index==0은 소속만 전환, index>0은 이름 전환 후 소속 전환으로 분기한다.
    """
    data = _load()
    user = {
        "id": uuid.uuid4().hex[:8],
        "kidsnote_id": kidsnote_id,
        "kidsnote_pw": kidsnote_pw,
        "children": children,
        "schedule": {"enabled": True, "times": times},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    data["users"].append(user)
    _save(data)
    return user


def delete_user(user_id: str) -> bool:
    data = _load()
    before = len(data["users"])
    data["users"] = [u for u in data["users"] if u["id"] != user_id]
    _save(data)
    return len(data["users"]) < before


def public_user(user: dict) -> dict:
    """비밀번호를 제외한 사용자 정보 (API 응답용)."""
    return {k: v for k, v in user.items() if k != "kidsnote_pw"}
