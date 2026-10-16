# -*- coding: utf-8 -*-
"""
도매몰 입고 알림 - GitHub Actions 버전

.github/workflows/restock.yml 이 5분마다 이 파일을 한 번씩 실행합니다.
실행할 때마다 지난번 상태(.restock_state.json, 저장소에 커밋하지 않고
GitHub Actions 캐시에 보관)와 비교해서, 새로 구매 가능해진 상품이 있으면
텔레그램으로 알리고 끝납니다.

토큰과 채팅 ID는 파일에 적지 않고 GitHub 저장소의 Secrets에 넣습니다.
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""

import html
import json
import os
import re
import sys
import time

import requests

# ------------------------------------------------------------------
# 설정
# ------------------------------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

STATE_FILE = ".restock_state.json"
FAIL_ALERT_AFTER = 5        # 연속 몇 번 확인에 실패하면 알려줄지
MAX_PAGES = 5               # 검색 결과를 몇 쪽까지 볼지

# 감시 목록: (사이트, 검색어). 추가하려면 같은 모양으로 한 줄 넣으면 됩니다.
WATCHES = [
    ("달달몰", "포켓몬카드"),
    ("현동몰", "포켓몬카드"),
    ("현동몰", "치이카와"),
]
# ------------------------------------------------------------------

SITES = {
    "달달몰": {
        "search_url": "https://m.daldaldome.com/product/search.html",
        "link_re": r"href=[\"'][^\"']*(?:/product/[^\"']*?/(\d+)/category/|/product/detail\.html\?[^\"']*product_no=(\d+))[^\"']*[\"']",
        "product_url": "https://m.daldaldome.com/product/detail.html?product_no=%s",
    },
    "현동몰": {
        "search_url": "https://m.hicorporation.co.kr/goods/goods_search.php",
        "link_re": r"href=[\"'][^\"']*goods_view\.php\?[^\"']*goodsNo=(\d+)()[^\"']*[\"']",
        "product_url": "https://m.hicorporation.co.kr/goods/goods_view.php?goodsNo=%s",
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; restock-notifier; personal use)",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

SKIP_NAMES = ("닫기", "할인기간", "찜하기")


def send_telegram(text, buttons=None):
    """텔레그램으로 메시지 전송. buttons 는 [(버튼글자, 주소), ...]. 성공하면 True."""
    data = {"chat_id": CHAT_ID, "text": text}
    if buttons:
        keyboard = [[{"text": label, "url": link}] for label, link in buttons]
        data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    try:
        r = requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_TOKEN,
            data=data,
            timeout=15,
        )
        if r.status_code != 200:
            print("  [텔레그램 오류] %s %s" % (r.status_code, r.text[:200]))
            return False
        return True
    except requests.RequestException as e:
        print("  [텔레그램 전송 실패] %s" % e)
        return False


def clean_text(fragment):
    """HTML 조각에서 태그를 떼고 글자만 남긴다."""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return re.sub(r"^상품명\s*:\s*", "", text)


def squash(text):
    return text.replace(" ", "")


def parse_page(page, link_re, keyword):
    """검색 결과 한 쪽을 읽는다.
    돌려주는 값: (이 쪽에 나온 상품번호 전체, {상품번호: {"name", "soldout"}} 중 검색어가 들어간 것만)"""
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)

    starts, seen = [], set()
    for m in re.finditer(link_re, page, flags=re.I):
        no = m.group(1) or m.group(2)
        if no and no not in seen:
            seen.add(no)
            starts.append((m.start(), no))

    key = squash(keyword)
    matched = {}
    for i, (pos, no) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else min(len(page), pos + 8000)
        seg_start = page.rfind("<", 0, pos)
        segment = page[seg_start if seg_start >= 0 else pos:end]

        texts = [clean_text(t) for t in re.findall(r"<a\b[^>]*>(.*?)</a>", segment, flags=re.S | re.I)]
        texts += [html.unescape(a).strip() for a in re.findall(r"alt=[\"']([^\"']+)[\"']", segment)]
        texts = [t for t in texts if t and not t.startswith(SKIP_NAMES)]
        hits = [t for t in texts if key in squash(t)]
        if not hits:
            continue   # 검색어가 상품명에 없으면 감시 대상이 아님

        # 현동몰 설명 줄("온라인재판매불가 * 상품명 * 12입...")에서 검색어가 든 조각만 뽑는다
        parts = [p.strip(" *") for t in hits for p in t.split(" * ")]
        parts = [p for p in parts if p and key in squash(p)]
        name = min(parts, key=len) if parts else hits[0]
        soldout = bool(re.search(r"soldout|sold_out|alt=[\"']품절[\"']|>\s*품절\s*<", segment, flags=re.I))
        matched[no] = {"name": name, "soldout": soldout}
    return seen, matched


def fetch(site_name, keyword):
    """사이트에서 검색어로 상품을 읽는다. 실패하면 None."""
    site = SITES[site_name]
    all_seen, products = set(), {}
    got_page = False
    for page_no in range(1, MAX_PAGES + 1):
        try:
            r = requests.get(
                site["search_url"],
                params={"keyword": keyword, "page": page_no},
                headers=HEADERS,
                timeout=20,
            )
        except requests.RequestException as e:
            print("  [%s] 접속 실패: %s" % (site_name, e))
            return None
        if r.status_code != 200:
            print("  [%s] 응답 코드 %s" % (site_name, r.status_code))
            return None
        r.encoding = "utf-8"
        text = r.text
        got_page = True

        seen, matched = parse_page(text, site["link_re"], keyword)
        products.update(matched)
        new = seen - all_seen
        all_seen |= seen
        # 다음 쪽이 없거나, 이 쪽에 검색어 상품이 하나도 없으면 그만 본다
        if not new or not matched:
            break
        time.sleep(2)

    if not got_page:
        return None
    # 페이지 모양이 아예 이상하면(상품 링크도, '없음' 문구도 없음) 실패로 본다
    if not all_seen and not re.search(r"상품이 존재하지 않습니다|검색 결과가 없습니다|검색결과\s*\(\s*0\s*\)|상품\s*수\s*0", text):
        print("  [%s] 페이지를 읽을 수 없습니다." % site_name)
        return None
    return products


def summary_line(label, products):
    if products is None:
        return "- %s: 확인 불가" % label
    sold = sum(1 for p in products.values() if p["soldout"])
    return "- %s: 상품 %d개 (품절 %d, 구매 가능 %d)" % (label, len(products), sold, len(products) - sold)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("GitHub Secrets에 TELEGRAM_TOKEN, TELEGRAM_CHAT_ID 를 넣어주세요.")
        sys.exit(1)

    state = load_state()
    first_run = state is None
    if first_run:
        state = {"products": {}, "fails": {}}

    lines = ["입고 감시를 시작합니다. (GitHub 자동 실행)"]
    buttons = []

    for site_name, keyword in WATCHES:
        key = "%s|%s" % (site_name, keyword)
        label = "%s %s" % (site_name, keyword)
        current = fetch(site_name, keyword)

        if current is None:
            n = state["fails"].get(key, 0) + 1
            state["fails"][key] = n
            print("%s: 확인 불가 (%d회 연속)" % (label, n))
            if n == FAIL_ALERT_AFTER and not first_run:
                send_telegram("⚠️ %s 확인이 계속 실패하고 있습니다. 사이트 구조가 바뀌었거나 접속이 막혔을 수 있습니다." % label)
            lines.append(summary_line(label, None))
            continue

        state["fails"][key] = 0
        print(summary_line(label, current)[2:])
        lines.append(summary_line(label, current))
        before_all = state["products"].get(key)

        for no, p in current.items():
            if p["soldout"]:
                continue
            link = SITES[site_name]["product_url"] % no
            if first_run:
                if len(buttons) < 8:
                    buttons.append(("구매페이지 열기: %s" % p["name"][:30], link))
                continue
            before = (before_all or {}).get(no)
            if before is None:
                send_telegram("🔔 %s 에 '%s' 상품이 올라왔습니다!\n%s" % (site_name, keyword, p["name"]), [("구매페이지 열기", link)])
            elif before["soldout"]:
                send_telegram("🔔 %s 입고! 품절이 풀렸습니다.\n%s" % (site_name, p["name"]), [("구매페이지 열기", link)])

        state["products"][key] = current
        time.sleep(2)

    if first_run:
        send_telegram("\n".join(lines), buttons)

    save_state(state)


if __name__ == "__main__":
    main()
