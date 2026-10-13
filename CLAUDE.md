# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 프로젝트는

키즈노트(한국 어린이집/유치원 알림장 앱)에서 아이들의 공지사항을 스크래핑하고, Gemini로 학부모가 챙겨야 할 항목을 추출한 뒤 구글 캘린더에 동기화하는 봇입니다. 카카오 로그인으로 누구나 가입할 수 있는 다중 사용자 서비스이며, 두 부분으로 나뉘어 배포됩니다.

1. **웹 대시보드(`web_app.py`, FastAPI)** — [Vercel](https://kidsnote-bot.vercel.app)에서 서버리스로 돌아갑니다. 카카오 로그인, 사용자 등록/수정/삭제, 실행 이력 조회, 구글 캘린더 연동을 담당합니다. 모든 데이터는 AWS DynamoDB에 저장되므로 이 프로세스는 상태가 없습니다(stateless).
2. **워커(`worker.py`)** — PC(또는 상시 켜진 서버)에서 백그라운드 프로세스로 돕니다. 사용자별 cron 스케줄 실행, 수동 실행 요청 처리, 계정 인증(Playwright) 요청 처리를 전담합니다. Playwright/PyMuPDF 등 무거운 의존성은 이 프로세스에만 있습니다.

**이 둘은 서로 직접 통신하지 않습니다.** 웹 쪽이 "실행해줘"/"계정 인증해줘" 요청을 DynamoDB에 레코드로 남기면, 워커가 주기적으로 폴링해서 그 요청을 찾아 처리하고 결과를 같은 레코드에 써 넣습니다. 그래서 PC가 인터넷에 포트를 열 필요가 없습니다 — 워커는 항상 자기가 먼저 AWS/구글/카카오/키즈노트로 나가는 연결만 합니다.

## 명령어

- 워커 실행(PC): `run_worker.bat` (내부적으로 `python worker.py`). 창 없이 백그라운드로 띄우려면 `run_worker_hidden.vbs` 사용 — Windows 시작 프로그램에 `KidsnoteBot_Worker.lnk`로 등록돼 있어 재부팅해도 자동 실행됩니다. **이게 지금 PC에서 띄워야 하는 유일한 프로세스입니다.** 스케줄된 실행/계정 인증이 안 될 때는 이 프로세스가 떠 있는지(그리고 `https://kidsnote-bot.vercel.app`의 "PC 워커" 표시가 켜짐인지)부터 확인.
- 웹 대시보드는 로컬에서 임시로 테스트할 때만 `run_web_server.bat` (`uvicorn web_app:app --host 127.0.0.1 --port 3020`)으로 띄웁니다. 상시 운영은 Vercel이 담당하므로 PC에서 이 프로세스를 계속 띄워둘 필요는 없습니다.
- Vercel 배포: `vercel --prod` (GitHub `main` 브랜치 push 시에도 자동 배포됨). 환경변수는 Vercel 대시보드(Settings → Environment Variables)에서 관리.
- 의존성 설치:
  - 웹 대시보드(`web_app.py`)만 필요하면: `pip install -r requirements.txt` — Vercel이 설치하는 것과 동일한 최소 목록(Playwright 등 무거운 패키지 없음).
  - 워커까지 포함해 PC에서 전부 돌리려면: `pip install -r requirements.txt -r requirements-worker.txt`, 그리고 `playwright install chromium`.
- 구글 캘린더에 동기화된 특정 기간 일정 일괄 삭제: `python clean_calendar.py` — 두 경로와 독립적인 유틸리티로, 레거시 `TARGET_SCHOOL`/`token.json`/`credentials.json`만 공유한다(이 셋은 이 유틸리티를 위해서만 `.env`/로컬에 남겨둔 것). 실행 전 파일 내 하드코딩된 `time_min`/`time_max`를 수정해야 하며, 삭제 대상 목록을 보여준 뒤 `y` 확인 입력을 받아야 삭제가 진행됨
- 이 저장소에는 테스트, 린트, 빌드 단계가 없습니다

## 아키텍처

### 공유 핵심 로직 (`pipeline.py`) — 워커 전용, Playwright/PyMuPDF/Gemini 의존

- `_scrape_notices(page, target_keywords, days_back=14)`: 로그인/자녀/기관 전환이 끝난 Playwright `page`에서 최근 공지 피드를 열어 `target_keywords`(기본값 `DEFAULT_KEYWORDS` = 계획안/가정통신문/취소/연기/변경/긴급)에 해당하는 카드만 남긴다. 매칭된 카드는 공지 PDF를 다운로드해 PyMuPDF로 1페이지를 PNG로 변환하고, PDF가 없는 경우(일부 기관은 공지를 일반 웹페이지로 렌더링함)에는 "공지사항 내용" 마커 이후의 본문 텍스트와, UI 요소가 아닌 실제 첨부 이미지(`>200px` 너비)만 함께 스크래핑한다.
- `analyze_and_calendar(data_list, target_school, target_class, google_credentials)`: 한 아이에 대해 스크래핑된 모든 문서(이미지+텍스트)를 Gemini(`gemini-3.1-flash-lite`)에 **한 번의 요청**으로 묶어서 보내, 모델이 중복/겹치는 공지를 직접 교차 검증하며 병합하도록 한다. 프롬프트에는 해당 학교 앞으로 등록된 향후 60일치 기존 캘린더 일정(제목에 `[학교명]` 포함)도 함께 전달되어, Gemini가 비슷한 새 제목을 만드는 대신 기존 제목을 재사용하게 하고, 취소/연기 공지를 원래 일정에 매핑할 수 있게 한다. 다른 반/연령 대상 공지는 명시적으로 제외하도록 지시한다(졸업사진 촬영처럼 반마다 대상이 다른 공지가 섞여 들어오는 걸 막기 위함). 반환된 JSON 일정은 제목 유사도(`difflib.SequenceMatcher`, 임계값 0.5)로 같은 날짜의 기존 일정과 중복 여부를 판단해 `update()`/`insert()`를 호출하고, 실제로 캘린더에 반영한 일정 목록을 리턴한다(실행 이력에 기록하기 위함). `google_credentials`로 그 사용자의 구글 OAuth 토큰을 받아 캘린더 API를 호출한다(등록 시 구글 연동이 필수라 항상 존재).
- `run_children_for_user(user)`: `users_store`에서 읽은 사용자 1명(JSON `children` 배열)을 처리하는 진입점. `children[0]`은 로그인 시 기본 활성화된 자녀라 소속만 전환, `children[1]`부터는 이름 전환 후 소속 전환. 한 번의 로그인 세션 안에서 해당 사용자의 모든 자녀를 순회한다. 자녀의 소속 전환은 `_select_institution`이 담당하며, 등록 마법사가 저장해 둔 `institution_testid`가 있으면 그 `data-testid`로 정밀 클릭하고 없으면 학교명 텍스트로 폴백한다.

### 데이터 계층 (AWS DynamoDB) — `ap-northeast-2`, 전부 Provisioned 1 RCU/1 WCU(Always Free 한도 내)

- `kidsnote_users` (`users_store.py`): 사용자 CRUD. PK `id`. `kakao_user_id`에 GSI(`kakao_user_id-index`)가 있어 카카오 로그인 시 scan 없이 바로 본인 계정을 찾는다. `kidsnote_pw`/`google_credentials`는 `crypto_utils.py`(Fernet, `KIDSNOTE_PW_ENC_KEY`)로 암호화해 저장하고, 읽을 때 투명하게 복호화한다. `public_user()`가 API 응답 시 이 둘과 `kakao_user_id`를 제거한다.
- `kidsnote_run_history` (`run_history_store.py`): 실행 이력이자 **작업 큐**이다. `request_run(user_id, trigger)`가 "queued" 레코드를 하나 만들면, 워커가 주기적으로 `list_queued()`로 찾아 `mark_running` → 실행 → `finalize`로 같은 레코드를 갱신한다. 웹 프로세스(Vercel)와 워커 프로세스가 직접 통신하지 못하므로, 이 테이블 레코드 하나가 "지금 상태 표시"와 "실행 큐" 역할을 동시에 한다. TTL(`expires_at`, 30일)로 자동 삭제되므로 별도 로그 파일이 필요 없다. 타임스탬프는 반드시 KST(`timezone(timedelta(hours=9))`)로 명시한다 — naive `datetime.now()`를 쓰면 Vercel(UTC)과 PC(KST)에서 실행되는 함수끼리 9시간 어긋난다.
- `kidsnote_discovery_requests` (`discovery_request_store.py`): 회원가입 1단계(계정 인증, `kidsnote_discovery.py`)도 Playwright가 필요해 Vercel에서 못 돌리므로 같은 "대기 레코드" 패턴으로 워커에 위임한다. 웹이 `create_request`로 레코드를 만들고 프론트가 `/api/verify-kidsnote/{request_id}`를 폴링, 워커가 처리 후 결과를 써 넣는다. TTL 30분.
- `kidsnote_worker_heartbeat` (`heartbeat_store.py`): 워커가 20초마다 `ping()`으로 살아있음을 기록하고, 대시보드가 90초 이내면 "켜짐"으로 판단한다. 타임스탬프도 KST 명시(위와 같은 이유).

### 웹 대시보드 (`web_app.py`, Vercel 배포)

- 카카오 로그인(`kakao_oauth.py`, `/api/auth/kakao/*`): Authorization Code 플로우. `state`는 세션 쿠키(Starlette `SessionMiddleware`, `SESSION_SECRET_KEY`)에 저장. 콜백에서 받은 카카오 고유 id를 세션에 저장하고 `users_store.get_user_by_kakao_id`로 본인 계정을 찾는다.
- 구글 캘린더 연동(`google_oauth.py`, `/api/auth/google/*`): authorization code + PKCE. `state` → 교환된 credentials dict는 서버 메모리(`_PENDING`, `_FLOWS`)에 짧게 보관되며, 등록 시 꺼내 DynamoDB에 영구 저장된다.
- 사용자 CRUD(`/api/users`)는 로그인한 카카오 계정 본인 것만 조회/수정/삭제 가능(`_require_owner`). cron 등록/해제는 더 이상 이 프로세스가 직접 하지 않고 — 워커가 60초마다 DynamoDB를 다시 읽어 스스로 맞춘다(`scheduler._resync_schedules`). 그래서 `register_user_schedule` 등을 호출할 필요가 없다.
- `/api/verify-kidsnote`(POST)는 `discovery_request_store`에 요청만 남기고 즉시 반환, `/api/verify-kidsnote/{request_id}`(GET)로 프론트가 폴링한다.
- `/api/worker-status`: PC 워커 하트비트 조회(로그인 불필요, 민감정보 없음). 대시보드 헤더에 켜짐/꺼짐 표시 + "지금 실행" 버튼 활성화 여부에 쓰인다.
- `/privacy`: 개인정보 처리방침 페이지(`templates/privacy.html`).
- `/static`: OG 이미지 등 정적 파일 (`FastAPI StaticFiles` 마운트).

### Vercel 배포 세부사항

- `api/index.py`가 `web_app.py`의 `app`을 그대로 import하는 진입점. `vercel.json`이 `@vercel/python` 빌더로 이걸 빌드하며 `includeFiles`로 필요한 루트 `.py` 파일과 `templates/`, `static/`을 명시적으로 포함시킨다.
- 루트 `requirements.txt`는 **Vercel이 설치하는 것과 동일한 최소 목록**이다(Playwright/PyMuPDF/google-genai 등 워커 전용 무거운 패키지 없음) — `scheduler.py`가 `pipeline.py`를 import하지 않도록 분리해 둔 덕분에 가능하다. 워커 전용 추가 패키지는 `requirements-worker.txt`에 있고, PC는 두 파일을 다 설치한다.
- Vercel 환경변수는 `web_app.py`가 실제로 쓰는 것만 등록돼 있다: `GOOGLE_CLIENT_ID`/`SECRET`, `AWS_ACCESS_KEY_ID`/`SECRET_ACCESS_KEY`/`DEFAULT_REGION`, `SESSION_SECRET_KEY`, `KIDSNOTE_PW_ENC_KEY`, `KAKAO_CLIENT_ID`/`SECRET`. `GEMINI_API_KEY`나 키즈노트 계정 정보는 워커(PC)의 `.env`에만 있고 Vercel에는 없다(안 쓰므로).

## 참고

- `clean_calendar.py`를 위해 `.env`에 `TARGET_SCHOOL`과 로컬 `token.json`/`credentials.json`을 남겨 두었다 — 이 셋을 지우면 그 유틸리티가 깨진다. 그 외 단일 계정(.env) 기반 자동 실행 경로는 전부 삭제됐다(`main.py`, `run_kidsnote_bot.bat`, `schedule_config.json`, `pipeline.run_all_children`, `scheduler`의 레거시 cron/`LEGACY_JOB_ID` 처리, `web_app`의 `/api/run`·`/api/status`).
- 다운로드된 공지 이미지(`notice_img_*.png`, `notice_web_img_*.jpg`)는 스크립트 옆에 생성되는 런타임 산출물로, 빌드 결과물이 아니며 gitignore 처리되어 있습니다.
- `token.json`/`credentials.json`, `.env`, `.vercel/`는 비밀번호·OAuth 토큰·API 키를 담고 있어 gitignore 처리되어 있습니다.
- 이 저장소는 2026-10-01에 통합 모노레포에서 분리되었으며, 이전 커밋 히스토리는 이관하지 않았습니다.
