from playwright.sync_api import sync_playwright

# 자녀 이름/생년월일 블록: 현재 활성 자녀는 화면에 보이는 상태로, 나머지 자녀는
# 자녀 전환 드롭다운용으로 숨겨진 채 미리 렌더링되어 있다. 클릭 없이도 전부 읽을 수 있다.
NAME_BLOCK_SELECTOR = 'p[class*="e1pxacyl3"]'

# 소속(기관) 드롭다운: data-testid가 안정적으로 붙어 있어 자녀 이름을 몰라도 클릭 가능.
INSTITUTION_CONTAINER_SELECTOR = '[data-testid="center-sidebar-menu-select"]'
INSTITUTION_ITEM_SELECTOR = '[data-testid^="center-sidebar-menu-select-"]'


def discover_account(kidsnote_id: str, kidsnote_pw: str) -> dict:
    """로그인 후 자녀 목록과 각 자녀의 소속(기관) 후보 목록을 수집한다.

    각 소속 항목은 두 줄 텍스트(line1/line2)를 그대로 반환한다 — 어느 쪽이 '기관명'이고
    어느 쪽이 '반 이름'인지는 계정마다 다를 수 있어(치료지원 등 연계 서비스가 섞인 계정은
    줄 순서가 달라짐) 서버가 추측하지 않고, 사용자가 프론트엔드에서 직접 보고 고르게 한다.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        page = browser.new_context(viewport={'width': 1300, 'height': 950}).new_page()
        page.set_default_timeout(20000)
        try:
            page.goto("https://www.kidsnote.com/login/")
            page.fill("input[name='username']", kidsnote_id)
            page.fill("input[name='password']", kidsnote_pw)
            page.click("button[type='submit']")
            page.wait_for_timeout(4000)

            if "/login" in page.url:
                return {"ok": False, "error": "로그인에 실패했습니다. ID/비밀번호를 확인하세요."}

            children = _list_children(page)
            if not children:
                return {"ok": False, "error": "자녀 정보를 찾을 수 없습니다. 키즈노트 화면 구성이 변경되었을 수 있습니다."}

            current_name = children[0]["name"]  # 로그인 직후 기본으로 활성화된 자녀
            for child in children:
                if child["name"] != current_name:
                    _switch_child(page, current_name, child["name"])
                    current_name = child["name"]
                child["institutions"] = _list_institutions(page)

            return {"ok": True, "children": children}
        except Exception as e:
            return {"ok": False, "error": f"계정 확인 중 오류가 발생했습니다: {e}"}
        finally:
            browser.close()


def _list_children(page) -> list[dict]:
    name_ps = page.locator(NAME_BLOCK_SELECTOR)
    count = name_ps.count()

    pairs = []
    for i in range(0, count - 1, 2):
        name = name_ps.nth(i).inner_text().strip()
        birth = name_ps.nth(i + 1).inner_text().strip()
        if name and birth:
            pairs.append((name, birth))

    # 현재 활성 자녀는 (보이는 블록 + 숨겨진 드롭다운용 블록)으로 중복 렌더링되므로 이름 기준 중복 제거
    seen = set()
    children = []
    for name, birth in pairs:
        if name not in seen:
            seen.add(name)
            children.append({"name": name, "birth": birth})
    return children


def _switch_child(page, from_name: str, to_name: str):
    page.get_by_text(from_name, exact=True).first.click()
    page.wait_for_timeout(1000)
    page.get_by_text(to_name, exact=True).first.click()
    page.wait_for_timeout(1500)


def _list_institutions(page) -> list[dict]:
    container = page.locator(INSTITUTION_CONTAINER_SELECTOR).first
    if container.count() == 0:
        return []

    container.click()
    page.wait_for_timeout(800)

    items = []
    option_items = page.locator(INSTITUTION_ITEM_SELECTOR)
    for i in range(option_items.count()):
        item = option_items.nth(i)
        testid = item.get_attribute("data-testid")
        ps = item.locator("p")
        if ps.count() >= 2:
            items.append({
                "line1": ps.nth(0).inner_text().strip(),
                "line2": ps.nth(1).inner_text().strip(),
                "testid": testid,
            })

    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    return items
