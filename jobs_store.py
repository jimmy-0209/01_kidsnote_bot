import json
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
USERS_CONFIG_PATH = BASE_DIR / "users_config.json"


def _load() -> dict:
    if not USERS_CONFIG_PATH.exists():
        return {"jobs": []}
    with open(USERS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(USERS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_jobs() -> list[dict]:
    return _load()["jobs"]


def get_job(job_id: str) -> dict | None:
    for job in list_jobs():
        if job["id"] == job_id:
            return job
    return None


def add_job(kidsnote_id: str, kidsnote_pw: str, target_school: str, target_class: str, times: list[str]) -> dict:
    data = _load()
    job = {
        "id": uuid.uuid4().hex[:8],
        "kidsnote_id": kidsnote_id,
        "kidsnote_pw": kidsnote_pw,
        "target_school": target_school,
        "target_class": target_class,
        "schedule": {"enabled": True, "times": times},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    data["jobs"].append(job)
    _save(data)
    return job


def delete_job(job_id: str) -> bool:
    data = _load()
    before = len(data["jobs"])
    data["jobs"] = [j for j in data["jobs"] if j["id"] != job_id]
    _save(data)
    return len(data["jobs"]) < before


def public_job(job: dict) -> dict:
    """비밀번호를 제외한 job 정보 (API 응답용)."""
    return {k: v for k, v in job.items() if k != "kidsnote_pw"}
