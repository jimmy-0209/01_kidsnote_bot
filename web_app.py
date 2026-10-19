import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

import discovery_request_store
import google_oauth
import heartbeat_store
import kakao_oauth
import run_history_store
import scheduler
import users_store

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# 운영자 본인의 카카오 고유 id. 로그인 세션의 kakao_user_id가 이 값과 일치할 때만
# 관리자 메뉴(/api/admin/*)를 노출한다 — 값은 /api/me 응답의 kakao_user_id로 확인한다.
ADMIN_KAKAO_USER_ID = os.environ.get("ADMIN_KAKAO_USER_ID")

# Phase 5: 스케줄 실행(scheduler.start())은 이 웹 프로세스가 아니라 별도의
# worker.py 프로세스가 전담한다. 이 앱은 DynamoDB를 읽고 쓰는 API/대시보드
# 역할만 하며, scheduler 모듈의 나머지 함수(get_job_state/trigger_* 등)는
# 전부 DynamoDB를 직접 보는 순수 함수라 워커가 떠 있지 않아도 안전하게 호출 가능하다.
app = FastAPI(title="KidsnoteBot 대시보드")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET_KEY"],
    same_site="lax",
    max_age=60 * 60 * 24 * 30,
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/api/worker-status")
def get_worker_status():
    """PC 워커가 켜져 있는지 — 로그인 여부와 무관하게 공개 정보(민감한 내용 없음)."""
    return JSONResponse(heartbeat_store.get_status())


@app.get("/api/history")
def get_history(request: Request, limit: int = 20):
    kakao_user_id = request.session.get("kakao_user_id")
    if not kakao_user_id:
        return JSONResponse([])

    # 로그인한 카카오 계정이 소유한 사용자의 이력만 보여준다 — 다른 사람이
    # 등록한 계정의 실행 이력은 보이면 안 되는 개인정보이므로.
    owner = users_store.get_user_by_kakao_id(kakao_user_id)
    if not owner:
        return JSONResponse([])

    runs = run_history_store.list_runs(owner["id"], limit=limit)
    enriched = []
    for run in runs:
        run.pop("expires_at", None)  # Decimal이라 JSON 직렬화 불가 + 프론트에서 불필요
        enriched.append({**run, "label": owner["kidsnote_id"]})
    return JSONResponse(enriched)


VALID_SCHEDULE_DAYS = {"all", "weekdays", "weekend"}


def _validate_times(times: list) -> str | None:
    """times의 각 항목은 "HH:MM" 문자열(과거 데이터, 매일로 간주) 또는
    {"time": "HH:MM", "days": "all"|"weekdays"|"weekend"} 형태다."""
    if not times:
        return "스케줄 시각을 하나 이상 입력하세요."
    for entry in times:
        if isinstance(entry, str):
            t, days = entry, "all"
        else:
            t, days = entry.get("time"), entry.get("days", "all")
        if days not in VALID_SCHEDULE_DAYS:
            return f"잘못된 요일 옵션입니다: {days}"
        hour, _, minute = str(t).partition(":")
        if not (hour.isdigit() and minute.isdigit() and 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            return f"잘못된 시간 형식입니다: {t} (HH:MM 형식이어야 합니다)"
    return None


def _validate_children(children: list) -> str | None:
    if not children:
        return "자녀를 하나 이상 추가하세요."
    for c in children:
        if not (c.get("child_name") and c.get("school") and c.get("class")):
            return "자녀마다 이름/기관(학교)/반을 모두 선택해야 합니다."
    return None


def _is_admin(request: Request) -> bool:
    kakao_user_id = request.session.get("kakao_user_id")
    return bool(ADMIN_KAKAO_USER_ID) and kakao_user_id == ADMIN_KAKAO_USER_ID


def _require_owner(request: Request, user: dict) -> JSONResponse | None:
    """카카오 로그인으로 등록된 사용자(kakao_user_id가 있는 레코드)는 본인만 수정/삭제/실행 가능.

    DynamoDB 마이그레이션 이전부터 있던 레코드(kakao_user_id 없음)는 카카오 로그인
    없이 쓰던 시절 그대로 통과시킨다 — 하위 호환.
    """
    owner_id = user.get("kakao_user_id")
    if owner_id and owner_id != request.session.get("kakao_user_id"):
        return JSONResponse({"error": "본인이 등록한 계정만 수정/삭제/실행할 수 있습니다."}, status_code=403)
    return None


@app.get("/api/users")
def list_users(request: Request):
    kakao_user_id = request.session.get("kakao_user_id")
    if not kakao_user_id:
        return JSONResponse([])

    user = users_store.get_user_by_kakao_id(kakao_user_id)
    if not user:
        return JSONResponse([])

    public = users_store.public_user(user)
    public["state"] = scheduler.get_job_state(user["id"])
    public["next_run_at"] = scheduler.get_user_next_run(user["id"])
    return JSONResponse([public])


@app.post("/api/users")
def create_user(request: Request, payload: dict):
    kakao_user_id = request.session.get("kakao_user_id")
    if not kakao_user_id:
        return JSONResponse({"error": "먼저 카카오 로그인이 필요합니다."}, status_code=401)

    if users_store.get_user_by_kakao_id(kakao_user_id):
        return JSONResponse({"error": "이미 이 카카오 계정으로 등록된 사용자가 있습니다."}, status_code=409)

    kidsnote_id = (payload.get("kidsnote_id") or "").strip()
    kidsnote_pw = payload.get("kidsnote_pw") or ""
    children = payload.get("children") or []
    times = payload.get("times") or []
    google_credentials = payload.get("google_credentials") or None

    if not kidsnote_id or not kidsnote_pw:
        return JSONResponse({"error": "키즈노트 ID/PW를 입력하세요."}, status_code=400)

    if not google_credentials:
        return JSONResponse({"error": "구글 캘린더 연동을 먼저 완료해야 등록할 수 있습니다."}, status_code=400)

    error = _validate_children(children)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    error = _validate_times(times)
    if error:
        return JSONResponse({"error": error}, status_code=400)

    user = users_store.add_user(kidsnote_id, kidsnote_pw, children, times, google_credentials, kakao_user_id=kakao_user_id)
    return JSONResponse(users_store.public_user(user))


@app.patch("/api/users/{user_id}")
def patch_user(request: Request, user_id: str, payload: dict):
    existing = users_store.get_user(user_id)
    if not existing:
        return JSONResponse({"error": "해당 사용자를 찾을 수 없습니다."}, status_code=404)

    owner_error = _require_owner(request, existing)
    if owner_error:
        return owner_error

    times = payload.get("times")
    if times is not None:
        error = _validate_times(times)
        if error:
            return JSONResponse({"error": error}, status_code=400)

    children_keywords = payload.get("children_keywords")
    if children_keywords is not None and len(children_keywords) != len(existing["children"]):
        return JSONResponse({"error": "자녀 수와 키워드 목록 수가 일치하지 않습니다."}, status_code=400)

    user = users_store.update_user(user_id, times=times, children_keywords=children_keywords)
    return JSONResponse(users_store.public_user(user))


@app.delete("/api/users/{user_id}")
def remove_user(request: Request, user_id: str):
    existing = users_store.get_user(user_id)
    if not existing:
        return JSONResponse({"error": "해당 사용자를 찾을 수 없습니다."}, status_code=404)

    owner_error = _require_owner(request, existing)
    if owner_error:
        return owner_error

    users_store.delete_user(user_id)
    return JSONResponse({"status": "deleted"})


@app.post("/api/users/{user_id}/run")
def run_user(request: Request, user_id: str):
    existing = users_store.get_user(user_id)
    if not existing:
        return JSONResponse({"error": "해당 사용자를 찾을 수 없습니다."}, status_code=404)

    owner_error = _require_owner(request, existing)
    if owner_error:
        return owner_error

    result = scheduler.trigger_job_run(user_id)
    if "error" in result:
        return JSONResponse(result, status_code=409)
    return JSONResponse(result)


@app.post("/api/verify-kidsnote")
def verify_kidsnote(payload: dict):
    """계정 인증은 Playwright가 필요해 이 프로세스(앞으로 Vercel)에서 직접 돌릴 수
    없다. 요청만 DynamoDB에 기록해 두고, PC 워커가 집어서 처리한 뒤 결과를 그 자리에
    써 두면, 프론트엔드가 /api/verify-kidsnote/{request_id}로 폴링해서 가져간다.
    """
    kidsnote_id = (payload.get("kidsnote_id") or "").strip()
    kidsnote_pw = payload.get("kidsnote_pw") or ""
    if not kidsnote_id or not kidsnote_pw:
        return JSONResponse({"error": "키즈노트 ID/PW를 입력하세요."}, status_code=400)

    request_id = discovery_request_store.create_request(kidsnote_id, kidsnote_pw)
    return JSONResponse({"request_id": request_id})


@app.get("/api/verify-kidsnote/{request_id}")
def get_verify_kidsnote_status(request_id: str):
    status = discovery_request_store.get_status(request_id)
    if status is None:
        return JSONResponse({"error": "알 수 없는 요청입니다."}, status_code=404)
    return JSONResponse(status)


def _google_redirect_uri(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/auth/google/callback"


@app.get("/api/auth/google/login")
def google_login(request: Request, state: str | None = None):
    if not google_oauth.is_configured():
        return JSONResponse(
            {"error": "서버에 GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET이 설정되어 있지 않습니다."},
            status_code=500,
        )
    result = google_oauth.create_auth_url(_google_redirect_uri(request), state=state)
    return RedirectResponse(result["auth_url"])


@app.get("/api/auth/google/callback", response_class=HTMLResponse)
def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(f"<h3>구글 로그인 실패</h3><p>{error}</p><p>이 창을 닫아주세요.</p>", status_code=400)
    if not code or not state:
        return HTMLResponse("<h3>잘못된 요청입니다.</h3>", status_code=400)

    try:
        ok = google_oauth.exchange_code(state, code, _google_redirect_uri(request))
    except Exception as e:
        return HTMLResponse(f"<h3>토큰 교환 중 오류가 발생했습니다</h3><p>{e}</p>", status_code=500)

    if not ok:
        return HTMLResponse("<h3>알 수 없는 인증 요청입니다 (state 불일치).</h3>", status_code=400)

    return HTMLResponse("<h3>✅ 구글 캘린더 연동 완료</h3><p>이 창을 닫고 원래 탭으로 돌아가세요.</p>")


@app.get("/api/auth/google/status/{state}")
def google_status(state: str, consume: bool = False):
    if consume:
        creds = google_oauth.pop_credentials(state)
        return JSONResponse({"ready": creds is not None, "credentials": creds})
    return JSONResponse({"ready": google_oauth.peek_status(state)})


def _kakao_redirect_uri(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/auth/kakao/callback"


@app.get("/api/auth/kakao/login")
def kakao_login(request: Request):
    if not kakao_oauth.is_configured():
        return JSONResponse(
            {"error": "서버에 KAKAO_CLIENT_ID가 설정되어 있지 않습니다."},
            status_code=500,
        )
    state = secrets.token_urlsafe(16)
    request.session["kakao_oauth_state"] = state
    auth_url = kakao_oauth.build_auth_url(_kakao_redirect_uri(request), state)
    return RedirectResponse(auth_url)


@app.get("/api/auth/kakao/callback", response_class=HTMLResponse)
def kakao_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(f"<h3>카카오 로그인 실패</h3><p>{error}</p><p>이 창을 닫아주세요.</p>", status_code=400)
    if not code or not state or state != request.session.get("kakao_oauth_state"):
        return HTMLResponse("<h3>알 수 없는 인증 요청입니다 (state 불일치).</h3>", status_code=400)

    request.session.pop("kakao_oauth_state", None)

    try:
        profile = kakao_oauth.exchange_code_for_user(code, _kakao_redirect_uri(request))
    except Exception as e:
        return HTMLResponse(f"<h3>카카오 로그인 처리 중 오류가 발생했습니다</h3><p>{e}</p>", status_code=500)

    request.session["kakao_user_id"] = profile["kakao_user_id"]
    request.session["kakao_nickname"] = profile.get("nickname")
    return RedirectResponse("/")


@app.post("/api/auth/kakao/logout")
def kakao_logout(request: Request):
    request.session.clear()
    return JSONResponse({"status": "logged_out"})


@app.get("/api/me")
def get_me(request: Request):
    kakao_user_id = request.session.get("kakao_user_id")
    if not kakao_user_id:
        return JSONResponse({"logged_in": False})

    user = users_store.get_user_by_kakao_id(kakao_user_id)
    unclaimed_count = sum(1 for u in users_store.list_users() if not u.get("kakao_user_id"))
    return JSONResponse({
        "logged_in": True,
        "nickname": request.session.get("kakao_nickname"),
        # 본인 세션에 이미 있는 값을 본인에게 그대로 돌려주는 것 — ADMIN_KAKAO_USER_ID를
        # 설정하려면 이 값을 먼저 확인해야 하므로 노출한다.
        "kakao_user_id": kakao_user_id,
        "registered": user is not None,
        "user": users_store.public_user(user) if user else None,
        # DynamoDB 마이그레이션 이전부터 있던, 아직 어떤 카카오 계정에도 연결되지 않은
        # 레코드가 있는지 여부만 노출(내용은 노출 안 함) — "기존 계정 연결" 버튼 표시용.
        "unclaimed_legacy_available": unclaimed_count > 0,
        "is_admin": _is_admin(request),
    })


@app.get("/api/admin/users")
def admin_list_users(request: Request):
    """운영자 본인(ADMIN_KAKAO_USER_ID) 계정만 전체 가입자 목록을 조회할 수 있다."""
    if not _is_admin(request):
        return JSONResponse({"error": "관리자만 접근할 수 있습니다."}, status_code=403)

    result = []
    for u in users_store.list_users():
        public = users_store.public_user(u)
        # 일반 /api/users 응답과 달리, 관리자 화면에서는 어느 카카오 계정이 소유자인지
        # 식별할 수 있어야 하므로 kakao_user_id를 그대로 포함시킨다.
        public["kakao_user_id"] = u.get("kakao_user_id")
        public["state"] = scheduler.get_job_state(u["id"])
        result.append(public)
    return JSONResponse(result)


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(request: Request, user_id: str):
    if not _is_admin(request):
        return JSONResponse({"error": "관리자만 접근할 수 있습니다."}, status_code=403)

    if not users_store.delete_user(user_id):
        return JSONResponse({"error": "해당 사용자를 찾을 수 없습니다."}, status_code=404)
    return JSONResponse({"status": "deleted"})


@app.post("/api/claim-legacy")
def claim_legacy(request: Request):
    """DynamoDB 마이그레이션 이전부터 있던, 카카오 계정에 연결되지 않은 레코드를
    지금 로그인한 카카오 계정에 1회 연결한다. 그 뒤로는 _require_owner/목록 필터링이
    이 계정을 일반 사용자와 동일하게 취급한다.
    """
    kakao_user_id = request.session.get("kakao_user_id")
    if not kakao_user_id:
        return JSONResponse({"error": "먼저 카카오 로그인이 필요합니다."}, status_code=401)

    if users_store.get_user_by_kakao_id(kakao_user_id):
        return JSONResponse({"error": "이미 이 카카오 계정으로 등록된 사용자가 있습니다."}, status_code=409)

    unclaimed = [u for u in users_store.list_users() if not u.get("kakao_user_id")]
    if not unclaimed:
        return JSONResponse({"error": "연결할 수 있는 기존 계정이 없습니다."}, status_code=404)
    if len(unclaimed) > 1:
        return JSONResponse({"error": "연결 대상이 여러 개라 자동으로 연결할 수 없습니다. 직접 문의하세요."}, status_code=409)

    user = users_store.claim_user(unclaimed[0]["id"], kakao_user_id)
    return JSONResponse(users_store.public_user(user))
