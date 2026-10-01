# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 이 프로젝트는

키즈노트(한국 어린이집/유치원 알림장 앱)에서 두 아이의 공지사항을 두 학교에 걸쳐 스크래핑하고, Gemini로 학부모가 챙겨야 할 항목을 추출한 뒤 구글 캘린더에 동기화하는 봇입니다. Windows 작업 스케줄러의 `KidsnoteBot_MonFri` 작업이 `run_kidsnote_bot.bat`을 실행하며 무인으로 동작합니다.

## 명령어

- 수동 실행: `./venv/Scripts/python.exe main.py` (스케줄 작업에서는 `run_kidsnote_bot.bat`을 통해 `pythonw.exe`로 실행되며, 이 배치파일이 `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`을 설정하고 출력을 `run_log.txt`에 append함)
- 의존성 설치: `pip install -r requirements.txt`, 그리고 `playwright install chromium` (헤드리스 Chromium을 테스트 러너 없이 직접 실행)
- 구글 캘린더에 동기화된 특정 기간 일정 일괄 삭제: `python clean_calendar.py` — 실행 전 파일 내 하드코딩된 `time_min`/`time_max`를 수정해야 하며, 삭제 대상 목록을 보여준 뒤 `y` 확인 입력을 받아야 삭제가 진행됨
- 이 저장소에는 테스트, 린트, 빌드 단계가 없습니다

## 아키텍처

`main.py`는 `CHILDREN` 배열의 항목마다 아래 파이프라인을 순차적으로 한 번씩 실행합니다.

1. **스크래핑** (`get_kidsnote_data`): Playwright(헤드리스 Chromium)로 kidsnote.com에 로그인한 뒤 해당 아이/학교로 전환합니다. 전환 방식은 두 가지인데, 첫째 아이는 로그인 시 기본 활성화되어 있어 소속(기관) 드롭다운만 전환하면 되고, 둘째 이후 아이는 이름 전환 드롭다운(`CHILD_NAME_1` → `CHILD_NAME_2`)을 먼저 거친 뒤 소속 드롭다운을 전환해야 합니다. 이후 공지 피드를 열어 최근 7일 이내 게시물 중 `target_keywords`(계획안/가정통신문/취소/연기/변경/긴급)에 해당하는 카드만 남깁니다. 매칭된 카드는 공지 PDF를 다운로드해 PyMuPDF로 1페이지를 PNG로 변환하고, PDF가 없는 경우(일부 기관은 공지를 일반 웹페이지로 렌더링함)에는 "공지사항 내용" 마커 이후의 본문 텍스트와, UI 요소가 아닌 실제 첨부 이미지(`>200px` 너비)만 함께 스크래핑합니다.
2. **분석 및 동기화** (`analyze_and_calendar`): 한 아이에 대해 스크래핑된 모든 문서(이미지+텍스트)를 Gemini(`gemini-3.1-flash-lite`)에 **한 번의 요청**으로 묶어서 보내, 모델이 중복되거나 겹치는 공지를 직접 교차 검증하며 병합하도록 합니다. 프롬프트에는 해당 아이의 향후 60일치 기존 캘린더 일정(제목에 `[학교명]`이 포함된 것)도 함께 전달되어, Gemini가 비슷한 새 제목을 만드는 대신 기존 제목을 그대로 재사용하게 하고, 취소/연기 공지를 인식해 원래 일정에 매핑할 수 있게 합니다.
3. 반환된 JSON 일정은 Calendar API로 등록됩니다. 같은 날짜의 기존 일정과는 제목 유사도(`difflib.SequenceMatcher`, 임계값 0.5)로 중복 여부를 판단해, 임계값 이상이면 `update()`, 아니면 `insert()`를 호출합니다. 취소 일정은 처리 방식이 다른데, 공지에 적힌 날짜가 실제 행사일이 아니라 '공지 게시일'인 경우가 많아 같은 날짜가 아닌 60일 전체 범위에서 제목 유사도로 매칭하고, 매칭된 원본 일정의 날짜를 그대로 유지합니다.

구글 OAuth 토큰은 `token.json`에 캐시되며(`credentials.json` 기반, 만료 시 자동 갱신), 키즈노트/Gemini 인증 정보와 아이별 타겟 설정은 `.env`에서 읽습니다: `GEMINI_API_KEY`, `KIDSNOTE_ID`/`KIDSNOTE_PW`, `TARGET_SCHOOL`/`TARGET_CLASS`(둘째 아이는 `_2` 접미사), `CHILD_NAME_1`/`CHILD_NAME_2`.

`clean_calendar.py`는 메인 파이프라인과는 독립적으로 가끔 쓰이는 유틸리티로, `token.json`/`credentials.json` 인증만 공유합니다.

## 참고

- 다운로드된 공지 이미지(`notice_img_*.png`, `notice_web_img_*.jpg`)와 `run_log.txt`는 스크립트 옆에 생성되는 런타임 산출물로, 빌드 결과물이 아니며 gitignore 처리되어 있습니다.
- 이 저장소는 2026-10-01에 통합 모노레포에서 분리되었으며, 이전 커밋 히스토리는 이관하지 않았습니다.
