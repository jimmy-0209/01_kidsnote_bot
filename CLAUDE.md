# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 프로젝트는

키즈노트(한국 어린이집/유치원 알림장 앱)에서 아이들의 공지사항을 스크래핑하고, Gemini로 학부모가 챙겨야 할 항목을 추출한 뒤 구글 캘린더에 동기화하는 봇입니다. 두 가지 실행 경로가 공존합니다.

1. **레거시 단일 사용자 CLI** — `.env`에 설정된 계정 한 개(아이 최대 2명)를 `main.py`로 1회 실행. 과거에는 Windows 작업 스케줄러 `KidsnoteBot_MonFri` 작업이 무인으로 돌렸으나, 이제는 삭제되었고 아래 2번 경로가 유일한 자동 실행 수단입니다.
2. **다중 사용자 웹 대시보드** — `web_app.py`(FastAPI)가 누구나 키즈노트 계정과 자녀/학교/반, 구글 캘린더 연동, 실행 스케줄을 직접 등록할 수 있는 마법사형 UI(`templates/dashboard.html`)를 제공합니다. 사용자별 설정은 `users_config.json`에 저장되고, `scheduler.py`가 사용자별 cron 스케줄과 레거시(.env) 스케줄을 함께 관리·실행합니다.

두 경로 모두 핵심 스크래핑/분석/캘린더 로직은 `pipeline.py` 하나를 공유합니다.

## 명령어

- 레거시 1회 실행: `./venv/Scripts/python.exe main.py` (스케줄 실행 시에는 `run_kidsnote_bot.bat`을 통해 `pythonw.exe`로 실행되며, 이 배치파일이 `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`을 설정하고 출력을 `run_log.txt`에 append함)
- 웹 대시보드 실행: `run_web_server.bat` (`uvicorn web_app:app --host 127.0.0.1 --port 3020`). 창 없이 백그라운드로 띄우려면 `run_web_server_hidden.vbs`를 사용 — 이게 지금의 유일한 상시 자동 실행 수단이므로, 스케줄된 실행이 안 될 때는 이 프로세스가 떠 있는지부터 확인
- 의존성 설치: `pip install -r requirements.txt`, 그리고 `playwright install chromium` (헤드리스 Chromium을 테스트 러너 없이 직접 실행)
- 구글 캘린더에 동기화된 특정 기간 일정 일괄 삭제: `python clean_calendar.py` — 레거시 `TARGET_SCHOOL`/`token.json` 기준으로만 동작하는 독립 유틸리티. 실행 전 파일 내 하드코딩된 `time_min`/`time_max`를 수정해야 하며, 삭제 대상 목록을 보여준 뒤 `y` 확인 입력을 받아야 삭제가 진행됨
- 이 저장소에는 테스트, 린트, 빌드 단계가 없습니다

## 아키텍처

### 공유 핵심 로직 (`pipeline.py`)

- `_scrape_notices(page, target_keywords, days_back=14)`: 로그인/자녀/기관 전환이 끝난 Playwright `page`에서 최근 공지 피드를 열어 `target_keywords`(기본값 `DEFAULT_KEYWORDS` = 계획안/가정통신문/취소/연기/변경/긴급)에 해당하는 카드만 남긴다. 매칭된 카드는 공지 PDF를 다운로드해 PyMuPDF로 1페이지를 PNG로 변환하고, PDF가 없는 경우(일부 기관은 공지를 일반 웹페이지로 렌더링함)에는 "공지사항 내용" 마커 이후의 본문 텍스트와, UI 요소가 아닌 실제 첨부 이미지(`>200px` 너비)만 함께 스크래핑한다. 레거시 흐름과 다중 사용자 흐름 양쪽에서 동일하게 재사용된다.
- `analyze_and_calendar(data_list, target_school, target_class, google_credentials=None)`: 한 아이에 대해 스크래핑된 모든 문서(이미지+텍스트)를 Gemini(`gemini-3.1-flash-lite`)에 **한 번의 요청**으로 묶어서 보내, 모델이 중복/겹치는 공지를 직접 교차 검증하며 병합하도록 한다. 프롬프트에는 해당 학교 앞으로 등록된 향후 60일치 기존 캘린더 일정(제목에 `[학교명]` 포함)도 함께 전달되어, Gemini가 비슷한 새 제목을 만드는 대신 기존 제목을 재사용하게 하고, 취소/연기 공지를 원래 일정에 매핑할 수 있게 한다. 반환된 JSON 일정은 제목 유사도(`difflib.SequenceMatcher`, 임계값 0.5)로 같은 날짜의 기존 일정과 중복 여부를 판단해 `update()`/`insert()`를 호출한다. 취소 일정은 공지에 적힌 날짜가 실제 행사일이 아니라 '공지 게시일'인 경우가 많으므로, 같은 날짜가 아닌 60일 전체 범위에서 제목 유사도로 원본 일정을 찾아 그 날짜를 그대로 유지한다. `google_credentials`가 주어지면 그 사용자의 구글 OAuth 토큰으로 캘린더 API를 호출하고, 없으면(레거시 흐름) `token.json`/`credentials.json`을 그대로 사용한다 — `_get_calendar_service`의 이 분기 덕분에 레거시 흐름은 변경 전과 100% 동일하게 동작한다.
- `run_all_children()`: `.env` 기반 `CHILDREN` 배열(최대 2명)을 순서대로 처리하는 레거시 진입점. 첫째는 로그인 시 기본 활성화되어 있어 소속(기관) 드롭다운만 전환하면 되고, 둘째는 이름 전환 드롭다운(`CHILD_NAME_1` → `CHILD_NAME_2`)을 먼저 거친 뒤 소속 드롭다운을 전환해야 한다. `main.py`와 `scheduler.py`(레거시 cron) 양쪽에서 호출된다.
- `run_children_for_user(user)`: `users_config.json`의 사용자 1명(JSON `children` 배열)을 처리하는 다중 사용자 진입점. `children[0]`은 기본 활성화된 자녀라 소속만 전환, `children[1]`부터는 이름 전환 후 소속 전환 — 레거시와 동일한 index 기반 규칙. 한 번의 로그인 세션 안에서 해당 사용자의 모든 자녀를 순회한다. 자녀의 소속 전환은 `_select_institution`이 담당하며, 등록 마법사가 저장해 둔 `institution_testid`가 있으면 그 `data-testid`로 정밀 클릭하고 없으면 학교명 텍스트로 폴백한다.

### 다중 사용자 웹 대시보드

- `web_app.py`: FastAPI 앱. 사용자 CRUD(`/api/users`), 레거시/사용자별 수동 실행 트리거(`/api/run`, `/api/users/{id}/run`), 레거시 스케줄 조회·변경(`/api/schedule`), 로그 조회(`/api/logs`, `run_log.txt` tail), 키즈노트 계정 검증(`/api/verify-kidsnote`), 구글 OAuth 흐름(`/api/auth/google/*`)을 제공한다. `lifespan`에서 `scheduler.start()`/`shutdown()`을 호출한다.
- `kidsnote_discovery.py`: 신규 사용자 등록 마법사 2단계에서 쓰인다. 키즈노트 로그인 후 자녀 목록과 각 자녀의 소속(기관) 후보를 수집해, 어느 줄이 기관명이고 어느 줄이 반 이름인지 서버가 추측하지 않고 사용자가 화면에서 직접 고르게 한다(연계 서비스가 섞인 계정은 줄 순서가 다를 수 있기 때문).
- `users_store.py`: `users_config.json`(gitignore 대상, 비밀번호·구글 토큰 포함)을 읽고 쓰는 CRUD 계층. `public_user()`가 API 응답 시 비밀번호와 `google_credentials`를 제거하고 `has_google_calendar` 불리언으로만 노출한다.
- `google_oauth.py`: 사용자별 구글 캘린더 OAuth(authorization code + PKCE) 흐름. `state` 토큰 → 교환된 credentials dict는 영구 저장소 없이 서버 메모리(`_PENDING`, `_FLOWS`)에만 짧게 보관되며, `/api/users` 등록 시 꺼내 `users_config.json`에 영구 저장된다. 콜백에서 `fetch_token()`을 호출할 때는 `create_auth_url()`에서 만든 "같은" `Flow` 인스턴스를 재사용해야 한다(PKCE `code_verifier`가 그 인스턴스에만 있음).
- `scheduler.py`: APScheduler `BackgroundScheduler` + FIFO 큐(`_RUN_QUEUE`) + 워커 스레드 1개로 모든 실행(레거시 cron, 사용자별 cron, 수동 트리거)을 순차 처리한다 — 여러 스케줄이 겹쳐도 Playwright 브라우저가 동시에 여러 개 뜨지 않게 하기 위함이다. `JOB_STATES`는 레거시는 `LEGACY_JOB_ID` 키, 다중 사용자는 `user_id` 키로 상태(`idle/queued/running/success/error`)를 관리한다. 레거시 스케줄 설정은 `schedule_config.json`에, 사용자별 스케줄은 `users_config.json`의 각 사용자 `schedule` 필드에 저장된다. 웹/스케줄러로 트리거한 실행은 `_TeeWriter`로 `print()` 출력을 콘솔과 `run_log.txt` 양쪽에 동시 기록한다(CLI 경로는 배치파일의 쉘 리다이렉션이 이미 로그를 남기므로 중복 기록하지 않음).

구글 OAuth 토큰(레거시)은 `token.json`에 캐시되며(`credentials.json` 기반, 만료 시 자동 갱신), 키즈노트/Gemini 인증 정보와 레거시 아이별 타겟 설정은 `.env`에서 읽는다: `GEMINI_API_KEY`, `KIDSNOTE_ID`/`KIDSNOTE_PW`, `TARGET_SCHOOL`/`TARGET_CLASS`(둘째 아이는 `_2` 접미사), `CHILD_NAME_1`/`CHILD_NAME_2`, 그리고 웹 대시보드의 구글 OAuth 클라이언트 자격증명 `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`.

`clean_calendar.py`는 두 파이프라인과 독립적으로 가끔 쓰이는 유틸리티로, 레거시 `token.json`/`credentials.json`/`TARGET_SCHOOL` 인증만 공유한다.

## 참고

- 다운로드된 공지 이미지(`notice_img_*.png`, `notice_web_img_*.jpg`)와 `run_log.txt`는 스크립트 옆에 생성되는 런타임 산출물로, 빌드 결과물이 아니며 gitignore 처리되어 있습니다.
- `users_config.json`, `token.json`/`credentials.json`, `.env`는 비밀번호·OAuth 토큰·API 키를 담고 있어 gitignore 처리되어 있습니다.
- 이 저장소는 2026-10-01에 통합 모노레포에서 분리되었으며, 이전 커밋 히스토리는 이관하지 않았습니다.
