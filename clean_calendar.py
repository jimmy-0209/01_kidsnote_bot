import os
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

# ================= [설정 유지] =================
SCOPES = ['https://www.googleapis.com/auth/calendar']
TARGET_SCHOOL = os.getenv("TARGET_SCHOOL")

def delete_next_week_events():
    # 1. 기존 인증 토큰 재활용
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
    
    # 2. 다음주 날짜 범위 지정 (2026년 6월 22일 월요일 ~ 6월 28일 일요일)
    # 한국 시간대(+09:00)를 명확히 지정하여 오차를 방지합니다.
    time_min = "2026-06-22T00:00:00+09:00"
    time_max = "2026-06-28T23:59:59+09:00"
    
    print(f"▶ 다음주(6/22 ~ 6/28) 캘린더에서 [{TARGET_SCHOOL}] 관련 일정을 검색합니다...")
    
    # 3. 해당 기간 내에 유치원 타이틀이 붙은 일정만 타겟팅 검색
    search_result = service.events().list(
        calendarId='primary', 
        timeMin=time_min, 
        timeMax=time_max, 
        q=f"[{TARGET_SCHOOL}]", # 안전장치: 유치원 일정만 골라내기
        singleEvents=True
    ).execute()
    
    events = search_result.get('items', [])
    
    if not events:
        print("\n❌ 해당 기간에 등록된 유치원 일정이 없습니다. 깨끗한 상태입니다.")
        return
        
    # 4. 삭제 대상 목록 먼저 시각적으로 보여주기
    print(f"\n📢 총 {len(events)}개의 일정이 발견되었습니다.")
    for ev in events:
        event_date = ev.get('start').get('date') or ev.get('start').get('dateTime')[:10]
        print(f"  - [대기] {event_date} : {ev.get('summary')}")
        
    # 5. 인간 실수 방지를 위한 최종 컨펌 인터록(Interlock)
    confirm = input("\n⚠️ 정말 위 일정들을 구글 캘린더에서 전부 삭제하시겠습니까? (y/n): ")
    if confirm.lower() != 'y':
        print("❌ 작업이 취소되었습니다. 대기 상태를 유지합니다.")
        return
        
    # 6. 루프 돌며 순차적 삭제 실행
    print("\n🚀 일괄 삭제 프로세스 시작...")
    for ev in events:
        event_id = ev['id']
        event_title = ev.get('summary')
        
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        print(f"  🗑️ 삭제 완료: {event_title}")
        
    print("\n🎉 다음주 복잡했던 유치원 일과가 모두 깔끔하게 청소되었습니다!")

if __name__ == "__main__":
    try:
        delete_next_week_events()
    except Exception as e:
        print(f"\n❌ 실행 중 오류 발생: {e}")
    finally:
        input("\n엔터 키를 누르면 창이 닫힙니다...")