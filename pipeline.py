import os
import json
import time
import re
import fitz  # PyMuPDF
import difflib
from dotenv import load_dotenv
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from google import genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# .env 파일 로드
load_dotenv()

# ================= [개인 설정] =================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
KIDSNOTE_ID = os.getenv("KIDSNOTE_ID")
KIDSNOTE_PW = os.getenv("KIDSNOTE_PW")
DEFAULT_CHILD = os.getenv("DEFAULT_CHILD")
TARGET_SCHOOL = os.getenv("TARGET_SCHOOL")
TARGET_CLASS = os.getenv("TARGET_CLASS") # 예: "기쁨 2반"

TARGET_SCHOOL_2 = os.getenv("TARGET_SCHOOL_2")
TARGET_CLASS_2 = os.getenv("TARGET_CLASS_2")
CHILD_NAME_1 = os.getenv("CHILD_NAME_1") # 첫째 이름 (아이 전환 드롭다운을 열 때 클릭)
CHILD_NAME_2 = os.getenv("CHILD_NAME_2") # 둘째 이름 (드롭다운에서 선택)

# 관리 대상 아이 목록 (첫째, 둘째 순서로 순차 처리)
# 첫째는 로그인 시 기본으로 활성화되어 있으므로 기관(청솔어린이집→한별유치원) 드롭다운만 전환하면 되고,
# 둘째는 별도 아이 프로필이므로 이름 드롭다운(CHILD_NAME_1 → CHILD_NAME_2)으로 먼저 전환해야 함
CHILDREN = [
    {"default_child": DEFAULT_CHILD, "target_school": TARGET_SCHOOL, "target_class": TARGET_CLASS,
     "switch_from_name": None, "switch_to_name": None},
    {"default_child": None, "target_school": TARGET_SCHOOL_2, "target_class": TARGET_CLASS_2,
     "switch_from_name": CHILD_NAME_1, "switch_to_name": CHILD_NAME_2},
]

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_kidsnote_data(kidsnote_id, kidsnote_pw, default_child, target_school, switch_from_name=None, switch_to_name=None, days_back=7):
    print("▶ 1. 스크래핑 시작...")
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    context = browser.new_context(viewport={'width': 1200, 'height': 900})
    page = context.new_page()
    extracted_data = []

    def handle_popup():
        try:
            if page.get_by_text("호칭을 설정해 주세요").is_visible(timeout=3000):
                page.get_by_text("엄마", exact=True).first.click(force=True)
                page.wait_for_timeout(1000)
                page.get_by_text("확인", exact=True).last.click(force=True)
                page.wait_for_timeout(2000)

            if page.get_by_text("아이의 하루에 공감을 남길 수 있어요").is_visible(timeout=3000):
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
                if page.get_by_text("아이의 하루에 공감을 남길 수 있어요").is_visible():
                    page.mouse.click(10, 10)
                    page.wait_for_timeout(1000)
        except:
            pass

    try:
        page.goto("https://www.kidsnote.com/login/")
        page.fill("input[name='username']", kidsnote_id)
        page.fill("input[name='password']", kidsnote_pw)
        page.click("button[type='submit']")
        page.wait_for_timeout(5000)

        if switch_to_name:
            # 둘째 이하 자녀: 이름 드롭다운을 열어 아이 프로필 자체를 전환
            page.get_by_text(switch_from_name, exact=True).first.click()
            page.wait_for_timeout(1000)
            page.get_by_text(switch_to_name, exact=True).first.click()
            page.wait_for_timeout(2000)
        elif default_child and page.get_by_text(default_child).count() > 0:
            # 첫째: 로그인 시 기본 활성화된 프로필의 기관(소속) 드롭다운만 전환
            page.get_by_text(default_child).first.click()
            page.wait_for_timeout(1000)
            page.get_by_text(target_school).last.click()
            page.wait_for_timeout(3000)

        page.goto("https://www.kidsnote.com/service/notice")
        page.wait_for_timeout(4000)

        for _ in range(3): page.keyboard.press("PageDown"); page.wait_for_timeout(1000)

        cards = page.locator('div[data-testid*="notice-card-type-item"]')
        target_indices = []

        seven_days_ago = datetime.now() - timedelta(days=days_back)

        # 정기 문서(계획안/가정통신문) 외에, 우천 등으로 인한 취소·연기·변경
        # 공지도 놓치지 않도록 관련 키워드가 포함된 단발성 안내 게시물도 대상에 포함한다
        target_keywords = ["계획안", "가정통신문", "취소", "연기", "변경", "긴급"]
        for i in range(min(cards.count(), 10)):
            card_text = cards.nth(i).inner_text()
            if any(kw in card_text for kw in target_keywords):
                match = re.search(r"(202\d)[\.\-년\s]+(\d{1,2})[\.\-월\s]+(\d{1,2})", card_text)
                if match and datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))) < seven_days_ago:
                    continue
                target_indices.append(i)
                print(f"  > ✔️ [대상 확인] {card_text.splitlines()[0][:20]}")

        for idx in target_indices:
            page.goto("https://www.kidsnote.com/service/notice")
            page.wait_for_timeout(3000)
            for _ in range(3): page.keyboard.press("PageDown"); page.wait_for_timeout(1000)

            cards = page.locator('div[data-testid*="notice-card-type-item"]')
            cards.nth(idx).click()
            page.wait_for_timeout(3000)
            handle_popup()

            download_btn = page.get_by_role("button", name="다운로드")
            if download_btn.count() > 0:
                with page.expect_download() as dl:
                    download_btn.first.click()
                pdf_path = dl.value.path()

                doc = fitz.open(pdf_path)
                page_pdf = doc.load_page(0)
                pix = page_pdf.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_path = f"notice_img_{idx}.png"
                pix.save(img_path)
                doc.close()

                extracted_data.append({"type": "image", "content": img_path})
                print(f"  > 🖼️ {idx+1}번째 글: PDF를 이미지로 변환 완료")
            else:
                # 다운로드 버튼이 없는 공지(예: 아이꿈어린이집): 웹페이지에 노출된
                # 본문 텍스트와 첨부 이미지를 그대로 읽어서 분석 대상에 추가한다

                # CSS 클래스가 배포마다 해시로 바뀌어 불안정하므로,
                # 항상 노출되는 "공지사항 내용" 헤더 텍스트를 기준으로 본문을 잘라낸다
                page_text = page.locator('body').inner_text()
                marker = "공지사항 내용"
                marker_idx = page_text.find(marker)
                if marker_idx != -1:
                    content_text = page_text[marker_idx + len(marker):].strip()
                    extracted_data.append({"type": "text", "content": content_text})
                    print(f"  > 📝 {idx+1}번째 글: 본문 텍스트 추출 완료")

                # 지연 로딩되는 첨부 사진까지 로드되도록 스크롤 후, 실제 첨부 사진만 다운로드
                # (작은 프로필 아이콘·CS 배너 등은 폭 200px 미만이라 걸러짐)
                for _ in range(3): page.keyboard.press("End"); page.wait_for_timeout(500)
                imgs = page.locator("img")
                img_count = 0
                for i in range(imgs.count()):
                    src = imgs.nth(i).get_attribute("src")
                    if not src or not src.startswith("http"):
                        continue
                    box = imgs.nth(i).bounding_box()
                    if not box or box["width"] < 200:
                        continue
                    try:
                        resp = page.request.get(src)
                        if resp.ok:
                            ext = ".jpg" if src.lower().split("?")[0].endswith((".jpg", ".jpeg")) else ".png"
                            web_img_path = f"notice_web_img_{idx}_{img_count}{ext}"
                            with open(web_img_path, "wb") as f:
                                f.write(resp.body())
                            extracted_data.append({"type": "image", "content": web_img_path})
                            img_count += 1
                    except Exception:
                        pass
                if img_count:
                    print(f"  > 🖼️ {idx+1}번째 글: 첨부 이미지 {img_count}장 다운로드 완료")

        return extracted_data, browser, p
    except Exception as e:
        print(f"❌ 스크래핑 오류: {e}")
        return [], browser, p

def analyze_and_calendar(data_list, target_school, target_class):
    print("\n▶ 2. 여러 문서 종합 및 Gemini AI 분석 시작...")
    client = genai.Client(api_key=GEMINI_API_KEY)

    # 구글 캘린더 인증 로직
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('calendar', 'v3', credentials=creds)

    # 실행 회차마다 Gemini가 같은 행사를 다른 문구로 요약하면 제목 유사도 매칭이
    # 실패해 중복 등록되므로, 기존 등록 일정을 먼저 조회해 Gemini에게 참고자료로 제공한다
    existing_window_start = datetime.now().strftime('%Y-%m-%dT00:00:00+09:00')
    existing_window_end = (datetime.now() + timedelta(days=60)).strftime('%Y-%m-%dT23:59:59+09:00')
    existing_items = service.events().list(
        calendarId='primary',
        timeMin=existing_window_start,
        timeMax=existing_window_end,
        q=f"[{target_school}]",
        singleEvents=True,
        orderBy='startTime'
    ).execute().get('items', [])

    if existing_items:
        existing_list_text = "\n".join(
            f"- {ev['start'].get('date', ev['start'].get('dateTime', ''))[:10]} | "
            f"{ev.get('summary', '').replace(f'[{target_school}] ', '')} | "
            f"{ev.get('description', '')[:60]}"
            for ev in existing_items
        )
    else:
        existing_list_text = "(없음)"

    # [핵심 변경] 프롬프트 튜닝 (기쁨2반 생일 제한, 자유복 제외, 병합 지시)
    prompt = f"""
    당신은 유치원 공지사항(여러 개의 주간계획안, 가정통신문 등)을 종합적으로 분석하여 '학부모가 반드시 챙겨야 할 핵심 정보'만 캘린더 일정으로 추출하는 깐깐한 AI 비서입니다.
    여러 문서의 내용을 한 번에 파악하여 중복되거나 불필요한 일정을 걸러내는 것이 당신의 최우선 목표입니다.

    [추출 범위 및 중복 제거 지침 - 최우선]
    1. 여러 문서(주간계획안, 가정통신문 등)에 걸쳐 동일한 날짜, 동일한 내용의 행사가 중복 안내된 경우, 절대 여러 개의 일정으로 나누지 말고 **내용을 완벽히 병합(Merge)하여 단 1개의 일정으로만** 만드세요.
    2. 현재 관리 대상 학급은 **'{target_class}'** 입니다. '{target_class}'에 명확히 해당되는 내용만 필터링하세요.
    3. **취소/연기/변경 공지 처리:** 새 문서가 우천, 사정 등의 이유로 **아래 [기존 등록된 일정] 목록에 있는 항목을 취소하거나 연기하거나 변경**한다는 내용이라면, 절대 무시하지 말고 반드시 일정으로 추출하세요. 이때 title은 **아래 목록에 있는 해당 항목의 title과 완전히 동일한 문자열 앞에 "[취소] "를 그대로 붙인 문자열**로 만드세요 (예: 목록의 title이 "숲 체험학습"이면 새 title은 "[취소] 숲 체험학습"). **date는 반드시 취소된 행사 자체가 원래 열릴 예정이었던 날짜**로 작성하세요 — **공지가 게시/작성된 날짜가 아닙니다.** (예: "8월 31일에, 9월 1일 예정이었던 숲 체험학습이 우천으로 취소되었다"는 공지라면, date는 공지 게시일인 8월 31일이 아니라 **행사 원래 날짜인 2026-09-01**이어야 합니다.) description에는 **취소/연기/변경된 사유와 대체 일정 유무 등 안내된 상세 내용**을 반드시 작성하세요.

    [기존 등록된 일정 - 중복 방지 및 취소 처리용 참고자료]
    아래는 이 학교('{target_school}') 앞으로 이미 캘린더에 등록되어 있는 일정입니다. 새로 추출하려는 내용이 날짜와 실질 내용(같은 행사, 같은 준비물 등) 면에서 아래 목록의 어떤 항목과 같은 것을 가리킨다면, 문서마다 표현이 다르더라도 새 title을 만들지 말고 **반드시 아래 목록의 title과 완전히 동일한 문자열**을 그대로 사용하세요(단, 취소/연기/변경 공지인 경우 위 3번 지침에 따라 "[취소] " 접두사를 붙이세요). 그래야 시스템이 같은 일정으로 인식해 병합·업데이트합니다. 정말 새로운 내용일 때만 새 title을 만드세요.
    {existing_list_text}

    [세부 추출 원칙]
    1. **생일자 필터링:** 생일 파티 안내 시, **반드시 '{target_class}' 소속 유아의 생일이 명시된 경우만** 일정으로 추출하세요. 다른 반 친구의 생일이거나, 반 명시 없이 이름만 있다면 절대 추출하지 마세요. '{target_class}' 생일자가 없다면 '생일자 없음' 같은 더미 일정도 만들지 말고 아예 무시하세요.
    2. **복장 필터링:** '자유복', '원복' 등 일상적인 복장은 캘린더를 지저분하게 하므로 **절대 추출 대상에 포함하지 마세요.** '체육복', '수영복', '숲체험복' 등 특별히 챙겨야 하는 복장만 추출하세요.
    3. '특별한 준비물', '부모 참여 행사', '기한이 있는 과제' 등 학부모의 실제 Action이 필요한 항목만 추출하세요. 일상적인 수업 내용(놀이, 활동 등)은 철저히 무시하세요.
    4. 날짜(date)는 무조건 'YYYY-MM-DD' format으로만 작성하세요. 연도가 없으면 2026년으로 간주합니다.

    출력은 반드시 아래 JSON 배열 형식으로만 작성하고 다른 말은 절대 금지.
    [
      {{"date": "YYYY-MM-DD", "title": "핵심 행사명 또는 준비물 안내", "description": "종합된 상세 준비물 및 학부모 필수 확인 사항"}}
    ]
    """

    # [핵심 변경] 모든 문서를 하나의 컨텍스트로 묶어서 API에 1번만 전송
    contents_to_analyze = [prompt]
    print("  > 스크랩한 모든 문서를 모아 한 번에 병합 분석을 준비 중입니다...")

    for idx, item in enumerate(data_list):
        if item["type"] == "image":
            img_file = client.files.upload(file=item["content"])
            contents_to_analyze.append(f"[문서 {idx+1} - 이미지 파일]")
            contents_to_analyze.append(img_file)
        else:
            contents_to_analyze.append(f"[문서 {idx+1} - 텍스트 내용]\n{item['content']}")

    attempt = 1
    while True:
        try:
            print("  > 🧠 Gemini AI가 문서를 교차 검증하며 중복 및 불필요한 일정을 걸러내고 있습니다...")
            res = client.models.generate_content(
                model='gemini-3.1-flash-lite',
                contents=contents_to_analyze
            )

            json_str = res.text.replace("```json", "").replace("```", "").strip()
            if json_str == "[]" or not json_str:
                print("    ✔️ 등록할 핵심 일정이 없습니다. (일상적인 내용이거나 생일자가 없음)")
                break

            events = json.loads(json_str)

            for ev in events:
                event_title = f"[{target_school}] {ev['title']}"
                target_date = ev['date']
                is_cancellation = ev['title'].strip().startswith("[취소]")

                event_body = {
                    'summary': event_title,
                    'description': ev['description'],
                    'start': {'date': target_date, 'timeZone': 'Asia/Seoul'},
                    'end': {'date': target_date, 'timeZone': 'Asia/Seoul'},
                    'reminders': {
                        'useDefault': False,
                        'overrides': [{'method': 'popup', 'minutes': 300}],
                    }
                }

                is_duplicate = False
                target_event_id = None

                if is_cancellation:
                    # 취소 공지는 Gemini가 채운 date가 (행사일이 아니라 공지 게시일 등으로)
                    # 부정확할 수 있으므로, 같은 날짜만 뒤지지 않고 제목 유사도로 60일 창
                    # 전체(existing_items)에서 원본 일정을 찾아 그 일정 자체를 업데이트한다
                    core_title = ev['title'].replace("[취소]", "").strip()
                    best_ratio = 0.0
                    best_match = None
                    for ext_ev in existing_items:
                        ext_title_core = ext_ev.get('summary', '').replace(f"[{target_school}]", "").replace("[취소]", "").strip()
                        ratio = difflib.SequenceMatcher(None, core_title, ext_title_core).ratio()
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_match = ext_ev

                    if best_match and best_ratio >= 0.5:
                        is_duplicate = True
                        target_event_id = best_match['id']
                        # Gemini의 date 값 대신 원본 일정에 이미 등록된 날짜를 그대로 유지한다
                        target_date = best_match['start'].get('date', best_match['start'].get('dateTime', ''))[:10]
                        event_body['start'] = {'date': target_date, 'timeZone': 'Asia/Seoul'}
                        event_body['end'] = {'date': target_date, 'timeZone': 'Asia/Seoul'}
                    else:
                        print(f"    ⚠️ 취소 대상 원본 일정을 찾지 못해 새 일정으로 등록합니다: {ev['title']} ({target_date})")

                if not is_duplicate:
                    start_of_day = f"{target_date}T00:00:00+09:00"
                    end_of_day = f"{target_date}T23:59:59+09:00"

                    search_result = service.events().list(
                        calendarId='primary',
                        timeMin=start_of_day,
                        timeMax=end_of_day,
                        q=f"[{target_school}]",
                        singleEvents=True
                    ).execute()

                    existing_events = search_result.get('items', [])

                    for ext_ev in existing_events:
                        ext_title_full = ext_ev.get('summary', '')
                        ext_title_core = ext_title_full.replace(f"[{target_school}]", "").strip()

                        similarity = difflib.SequenceMatcher(None, ev['title'], ext_title_core).ratio()

                        # 제목 유사도가 50% 이상이면 중복 덮어쓰기
                        if similarity >= 0.5:
                            is_duplicate = True
                            target_event_id = ext_ev['id']
                            break

                if is_duplicate:
                    service.events().update(calendarId='primary', eventId=target_event_id, body=event_body).execute()
                    print(f"    🔄 구글 캘린더 업데이트 (일정 통합): {ev['title']} ({target_date})")
                else:
                    service.events().insert(calendarId='primary', body=event_body).execute()
                    print(f"    ✔️ 구글 캘린더 신규 등록 완료: {ev['title']} ({target_date})")

            time.sleep(15)
            break

        except Exception as e:
            error_msg = str(e)

            if "429" in error_msg or "503" in error_msg:
                if "limit: 0" in error_msg:
                    print("    ❌ [치명적 오류] 계정 무료 한도가 0으로 차단되었습니다. 재시도를 중단합니다.")
                    break

                print(f"    ⚠️ 단기 API 한도 도달 (시도 횟수: {attempt}). 할당량 초기화를 위해 65초 대기합니다...")
                attempt += 1
                time.sleep(65)
                continue
            else:
                print(f"    ❌ 분석/등록 오류: {e}")
                break


def run_all_children():
    """CHILDREN 목록을 순서대로 처리하는 전체 파이프라인.

    기존 main.py의 `if __name__ == "__main__":` 블록 내용을 그대로 옮긴 것으로,
    CLI(main.py)와 웹 서버(web_app.py) 양쪽에서 동일하게 재사용하기 위해 함수로 분리했다.
    브라우저/Playwright 정리(finally)도 그대로 포함되어 있어, 웹에서 트리거해도
    좀비 프로세스 없이 정상 종료된다.
    """
    for child in CHILDREN:
        b, p = None, None
        print(f"\n===== ▶ [{child['target_school']}] 처리 시작 =====")
        try:
            data_list, b, p = get_kidsnote_data(
                KIDSNOTE_ID, KIDSNOTE_PW,
                child["default_child"], child["target_school"],
                child["switch_from_name"], child["switch_to_name"]
            )
            if data_list:
                analyze_and_calendar(data_list, child["target_school"], child["target_class"])
            else:
                print(f"\n⚠️ [{child['target_school']}] 조건에 맞는 공지사항이 없습니다.")
        except Exception as e:
            print(f"\n❌ [{child['target_school']}] 프로그램 실행 오류: {e}")
        finally:
            if b: b.close()
            if p: p.stop()

    print("\n✅ 모든 작업이 완벽하게 완료되었습니다!")


def run_single_job(job: dict):
    """다중 사용자 프로토타입용: job 딕셔너리 하나(키즈노트 ID/PW + 학교/반)를 받아
    get_kidsnote_data + analyze_and_calendar를 그대로 재사용해 실행한다.

    기존 CHILDREN은 한 계정 안에서 아이 전환(이름 드롭다운)이 필요했지만,
    다중 사용자 job은 "로그인 1개 + 학교/반 1개"로 단순화했으므로
    switch_from_name/switch_to_name 없이 기본 흐름(default_child 없이 바로 소속 학교로 진입)만 사용한다.
    """
    b, p = None, None
    target_school = job["target_school"]
    print(f"\n===== ▶ [job:{job['id']} / {target_school}] 처리 시작 =====")
    try:
        data_list, b, p = get_kidsnote_data(
            job["kidsnote_id"], job["kidsnote_pw"],
            default_child=None, target_school=target_school,
        )
        if data_list:
            analyze_and_calendar(data_list, target_school, job["target_class"])
        else:
            print(f"\n⚠️ [job:{job['id']}] 조건에 맞는 공지사항이 없습니다.")
    finally:
        if b: b.close()
        if p: p.stop()
