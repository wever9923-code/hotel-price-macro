"""파타야 호텔 가격 확인 매크로.

GitHub Actions에서 하루 2번 실행되어
1) 몽키트래블·아고다·부킹닷컴·태초클럽 가격을 읽고
2) docs/history.json 에 기록한 뒤 (GitHub Pages 가격 페이지가 이 파일을 읽음)
3) 직전 기록과 달라진 가격이 있으면 카카오톡 "나에게 보내기"로 알린다.

로컬 테스트:  pip install playwright requests && playwright install chromium
              python scraper/check.py --dry-run
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "docs" / "history.json"
JS_DIR = Path(__file__).resolve().parent
DRY_RUN = "--dry-run" in sys.argv

# ── 여행 조건 (바꾸려면 여기만 수정) ─────────────────────────────
CHECK_IN, CHECK_OUT, NIGHTS = "2027-04-27", "2027-05-01", 4
ADULTS, CHILD_AGES = 2, [10]
PAGE_URL = os.environ.get("PAGE_URL", "")  # 예: https://아이디.github.io/hotel-price-macro/

HOTELS = {
    "voyage": {"name": "보야지", "monkey": 11462841179, "agoda": 86640187,
               "agoda_slug": "grande-centre-point-voyage-pattaya",
               "booking": "grande-centre-point-voyage-pattaya", "taecho": 57433},
    "space": {"name": "스페이스", "monkey": 1076864242, "agoda": 31068633,
              "agoda_slug": "grande-centre-point-space-pattaya",
              "booking": "grande-centre-space-pattaya", "taecho": 56387},
    "mirage": {"name": "미라지", "monkey": 1076635217, "agoda": 161923,
               "agoda_slug": "centara-grand-mirage-beach-resort",
               "booking": "centara-grand-mirage-beach-resort-pattaya", "taecho": 54992},
}
SITE_NAMES = {"monkey": "몽키", "official": "공홈", "booking": "부킹", "taecho": "태초", "google": "구글"}
COMPARE_SITES = ("monkey", "official", "booking", "google")  # 태초클럽은 참고용(성인 2인 기준)

# ── 호텔 공식 홈페이지 예약 엔진 ─────────────────────────────────
TH_TAX = 1.177  # 태국 봉사료 10% + VAT 7% (세금 제외로 표시되는 엔진용)
OFFICIAL = {
    "voyage": {"engine": "travelanium", "base": "https://reservation.voyagepattaya.com", "propertyId": 1240},
    "space": {"engine": "travelanium", "base": "https://reservation.spacepattaya.com", "propertyId": 891},
    "mirage": {"engine": "synxis", "chain": 27886, "hotel": 34973,
               "promo_page": "https://www.centarahotelsresorts.com/centaragrand/cmbr"},
}

# ── 항공권 조건 (구글 플라이트) ─────────────────────────────────
FLIGHT = {"from": "ICN", "to": "BKK", "out": "2027-04-23", "back": "2027-05-01",
          "airline": "Korean Air", "adults": 1, "nonstop": True}
NAMES = {**{k: v["name"] for k, v in HOTELS.items()}, "flight": "대한항공"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def log(*a):
    print(*a, flush=True)


# ── 사이트별 수집 ─────────────────────────────────────────────
def scrape_monkey(ctx):
    js = (JS_DIR / "monkey.js").read_text(encoding="utf-8")
    ages = ",".join(map(str, CHILD_AGES))
    out = {}
    page = ctx.new_page()
    for key, h in HOTELS.items():
        url = ("https://www.monkeytravel.com/th/ko/hotel/pattaya/nakluabeach/product/product_detail.php"
               f"?product_id={h['monkey']}&period={CHECK_IN}%7E{CHECK_OUT}&room=1&room_adult={ADULTS}"
               f"&child_count={len(CHILD_AGES)}&child_age_list={ages}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            r = page.evaluate(js)
            b = r.get("best")
            if b:
                note = "회원 전용가" if b.get("member") else ""
                out[key] = {"perNight": b["perNight"], "total": b["total"], "room": b["room"],
                            "freeCancel": b["cancel"].replace(" 무료 취소", "") if "무료 취소" in b.get("cancel", "") else "",
                            "note": note or (b.get("cancel") if b.get("cancel") == "환불 불가" else "")}
            else:
                log(f"[monkey] {key}: 조식 포함 요금을 찾지 못함 (offers={r.get('count')})")
        except Exception as e:
            log(f"[monkey] {key} 실패: {e}")
    page.close()
    return out


AGODA_PARSE = """async ({u, body, headers, pid}) => {
  const b = JSON.parse(body); b.propertyId = String(pid);
  const r = await fetch(u, {method:'POST', headers:Object.assign({'content-type':'application/json'}, headers), body:JSON.stringify(b), credentials:'include'});
  const j = await r.json(); const all = [];
  for (const room of (j.rooms||[])) for (const o of (room.offers||[])) {
    if (!(o.filterTags||[]).includes('breakfast-include')) continue;
    const m = (o.price?.averageInclusivePrice||'').match(/₩\\s*([0-9,]+)/); const n = m ? +m[1].replace(/,/g,'') : null;
    if (!n) continue;
    const eb = (o.benefits||[]).some(x => /간이침대/.test(x.text));
    all.push({room:room.name, perNight:n, cancel:o.specialOfferDetail?.cancellationBenefit?.text||'', extraBed:eb});
  }
  const withBed = all.filter(x => x.extraBed); const pool = withBed.length ? withBed : all;
  pool.sort((a,b) => a.perNight - b.perNight);
  return pool[0] || {perNight:null, status: j.isSoldOut ? '매진' : '조식 포함 요금 없음'};
}"""

SKIP_HEADERS = {"cookie", "content-length", "host", "user-agent", "referer", "origin", "accept-encoding",
                "accept-language", "connection", "accept"}


def scrape_agoda(ctx):
    page = ctx.new_page()
    cap = {}

    def on_req(req):
        if "room-grid" in req.url and req.method == "POST" and "body" not in cap:
            cap.update(u=req.url, body=req.post_data,
                       headers={k: v for k, v in req.headers.items()
                                if k.lower() not in SKIP_HEADERS and not k.startswith(":") and not k.lower().startswith("sec-")})

    page.on("request", on_req)
    first = next(iter(HOTELS.values()))
    ages = ",".join(map(str, CHILD_AGES))
    url = (f"https://www.agoda.com/ko-kr/{first['agoda_slug']}/hotel/pattaya-th.html?checkIn={CHECK_IN}"
           f"&los={NIGHTS}&rooms=1&adults={ADULTS}&children={len(CHILD_AGES)}&childAges={ages}&currencyCode=KRW")
    out = {}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(60):
            if "body" in cap:
                break
            page.wait_for_timeout(500)
        if "body" not in cap:
            log("[agoda] 객실 요청을 찾지 못함 (차단 또는 페이지 구조 변경)")
            return out
        for key, h in HOTELS.items():
            try:
                r = page.evaluate(AGODA_PARSE, {**cap, "pid": h["agoda"]})
                if r.get("perNight"):
                    cancel = "환불 불가" if r["cancel"].startswith("환불 불가") else r["cancel"]
                    extra = "간이침대 포함" if r["extraBed"] else "아동 조식비 별도 가능"
                    out[key] = {"perNight": r["perNight"], "total": r["perNight"] * NIGHTS, "room": r["room"],
                                "note": " · ".join(x for x in (cancel, extra) if x)}
                else:
                    out[key] = {"perNight": None, "status": r.get("status", "요금 없음")}
            except Exception as e:
                log(f"[agoda] {key} 실패: {e}")
    except Exception as e:
        log(f"[agoda] 실패: {e}")
    page.close()
    return out


BOOKING_PARSE = """async ({slug, q, nights}) => {
  const r = await fetch('/hotel/th/'+slug+'.ko.html'+q, {credentials:'include'});
  const d = new DOMParser().parseFromString(await r.text(), 'text/html');
  d.querySelectorAll('style,script').forEach(e => e.remove());
  let cur = ''; const out = []; const rows = d.querySelectorAll('#hprt-table tbody tr');
  for (const tr of rows) {
    const rn = tr.querySelector('.hprt-roomtype-icon-link, .hprt-roomtype-link'); if (rn) cur = rn.textContent.trim();
    const pe = tr.querySelector('.bui-price-display__value, .prco-valign-middle-helper'); if (!pe) continue;
    const total = +pe.textContent.replace(/[^0-9]/g,'');
    const cond = tr.querySelector('.hprt-table-cell-conditions')?.textContent.replace(/\\s+/g,' ') || '';
    const occ = tr.querySelector('.hprt-occupancy-occupancy-info, .c-occupancy-icons')?.textContent.replace(/\\s+/g,' ') || '';
    const adults = +((occ.match(/성인 최대 투숙 인원: (\\d)/)||[])[1]||0), kids = +((occ.match(/어린이 최대 투숙 인원: (\\d)/)||[])[1]||0);
    if (/조식 포함/.test(cond) && (adults >= 3 || (adults >= 2 && kids >= 1)))
      out.push({room:cur, total, perNight:Math.round(total/nights), cancel:((cond.match(/(무료 취소[^•]{0,40}|환불 불가)/)||[])[1]||'').trim()});
  }
  out.sort((a,b) => a.total - b.total);
  return out[0] || {perNight:null, status:'조건 맞는 요금 없음', tableRows: rows.length};
}"""


def scrape_booking(ctx):
    page = ctx.new_page()
    out = {}
    q = (f"?checkin={CHECK_IN}&checkout={CHECK_OUT}&group_adults={ADULTS}&group_children={len(CHILD_AGES)}"
         + "".join(f"&age={a}" for a in CHILD_AGES) + "&no_rooms=1&selected_currency=KRW&lang=ko")
    try:
        page.goto("https://www.booking.com/index.ko.html", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)  # 보안 확인 페이지가 있으면 자동으로 넘어갈 시간
        for key, h in HOTELS.items():
            try:
                r = page.evaluate(BOOKING_PARSE, {"slug": h["booking"], "q": q, "nights": NIGHTS})
                if r.get("perNight"):
                    out[key] = {"perNight": r["perNight"], "total": r["total"], "room": r["room"], "note": r["cancel"]}
                elif r.get("tableRows", 0) > 0:
                    # 객실표는 읽혔지만 인원 조건을 못 맞춘 경우: 페이지 형식이 달라진 일시적 현상일 수 있어
                    # 기록하지 않는다(잘못된 '가격 없음' 알림 방지)
                    log(f"[booking] {key}: 객실표 {r['tableRows']}행을 읽었지만 조건 맞는 요금을 못 찾음 → 이번엔 기록 안 함")
                else:
                    log(f"[booking] {key}: 객실표를 읽지 못함 (차단 가능성)")
            except Exception as e:
                log(f"[booking] {key} 실패: {e}")
    except Exception as e:
        log(f"[booking] 실패: {e}")
    page.close()
    return out


TAECHO_PARSE = """async ({hseq, s, e, n}) => {
  const r = await fetch(`/hotel/view_room.ajax.html?choice=day&hseq=${hseq}&view_type=1&s_start=${s}&s_end=${e}&s_lodg=${n}&s_rcnt=1`, {method:'POST'});
  const d = document.createElement('div'); d.innerHTML = await r.text(); d.querySelectorAll('script').forEach(x => x.remove());
  return d.innerText.replace(/[\\t ]+/g,' ').replace(/\\n\\s*\\n+/g,'\\n');
}"""


def scrape_taecho(ctx):
    import re
    page = ctx.new_page()
    out = {}
    try:
        page.goto("https://www.taechoclub.com/", wait_until="domcontentloaded", timeout=60000)
        for key, h in HOTELS.items():
            try:
                t = page.evaluate(TAECHO_PARSE, {"hseq": h["taecho"], "s": CHECK_IN, "e": CHECK_OUT, "n": NIGHTS})
                if "등록된 정보가 없습니다" in t:
                    out[key] = {"perNight": None, "status": "요금 미등록", "note": "성인 2인 조식 기준"}
                    continue
                lines = [x.strip() for x in t.split("\n") if x.strip()]
                best = None
                for i, L in enumerate(lines):
                    m = re.fullmatch(r"([0-9,]+)원", L)
                    if m and "포함" in lines[max(0, i - 3):i]:
                        v = int(m.group(1).replace(",", ""))
                        if not best or v < best[0]:
                            best = (v, lines[max(0, i - 3)])
                if best:
                    out[key] = {"perNight": best[0], "room": best[1], "note": "성인 2인 조식 기준"}
                elif "시크릿요금" in t or "예약요청시 확인" in t:
                    out[key] = {"perNight": None, "status": "시크릿요금 (문의 필요)", "note": "성인 2인 조식 기준"}
            except Exception as e:
                log(f"[taecho] {key} 실패: {e}")
    except Exception as e:
        log(f"[taecho] 실패: {e}")
    page.close()
    return out


GFLIGHTS_PARSE = """async ({airline, nonstop}) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const labels = () => [...document.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label')).filter(t => /South Korean won round trip total/.test(t));
  for (let i = 0; i < 40 && !labels().length; i++) await sleep(500);
  await sleep(1500);
  const seen = new Set(), flights = [];
  for (const t of labels()) {
    const price = +((t.match(/From ([0-9,]+) South Korean won/) || [])[1] || '').replace(/,/g, '');
    const air = (t.match(/flight with ([^.]+)\./) || [])[1] || '';
    const stops = /^From [0-9,]+ South Korean won round trip total\. Nonstop/.test(t) ? 0 : 1;
    const dep = (t.match(/at (\d{1,2}:\d{2}\s?[AP]M) on/) || [])[1] || '';
    const arr = (t.match(/arrives at .*? at (\d{1,2}:\d{2}\s?[AP]M)/) || [])[1] || '';
    if (!price) continue;
    if (airline && !air.toLowerCase().includes(airline.toLowerCase())) continue;
    if (nonstop && stops) continue;
    const key = dep + arr + price; if (seen.has(key)) continue; seen.add(key);
    flights.push({price, airline: air, dep, arr});
  }
  flights.sort((a, b) => a.price - b.price);
  const insight = (document.body.innerText.match(/Prices are currently (\w+)/) || [])[1] || '';
  return {count: flights.length, best: flights[0] || null, flights, insight, labels: labels().length};
}"""


def to24(t):
    try:
        return datetime.strptime(t.replace("\u202f", " ").strip(), "%I:%M %p").strftime("%H:%M")
    except Exception:
        return t


def scrape_flights(ctx):
    from urllib.parse import quote
    f = FLIGHT
    q = (f"Flights to {f['to']} from {f['from']} on {f['out']} through {f['back']}"
         + (" nonstop" if f["nonstop"] else "") + (f" {f['airline']}" if f["airline"] else "")
         + (f" {f['adults']} adults" if f["adults"] > 1 else ""))
    url = f"https://www.google.com/travel/flights?q={quote(q)}&curr=KRW&hl=en&gl=kr"
    page = ctx.new_page()
    out = {}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        r = page.evaluate(GFLIGHTS_PARSE, {"airline": f["airline"], "nonstop": f["nonstop"]})
        b = r.get("best")
        if b:
            deps = sorted({to24(x["dep"]) for x in r["flights"] if x["price"] == b["price"]})
            insight = {"low": "현재 낮은 편", "typical": "현재 보통", "high": "현재 높은 편"}.get(r.get("insight"), "")
            out["flight"] = {"perNight": b["price"], "total": b["price"],
                             "room": f"{b['airline'].replace('Korean Air', '대한항공')} {'직항 ' if f['nonstop'] else ''}{', '.join(deps)} 출발",
                             "note": " · ".join(x for x in (f"성인 {f['adults']}명 왕복 총액", insight) if x)}
        else:
            log(f"[google] 항공편을 찾지 못함 (labels={r.get('labels')})")
    except Exception as e:
        log(f"[google] 실패: {e}")
    page.close()
    return out


def official_url(key, promo=""):
    o = OFFICIAL[key]
    ages = ",".join(map(str, CHILD_AGES))
    if o["engine"] == "travelanium":
        return (f"{o['base']}/propertyibe2/rates?propertyId={o['propertyId']}&onlineId=4&checkin={CHECK_IN}"
                f"&checkout={CHECK_OUT}&numofroom=1&numofadult={ADULTS}&numofchild={len(CHILD_AGES)}"
                f"&childage={ages}&currency=KRW&lang=en")
    return (f"https://be.synxis.com/?adult={ADULTS}&agencyid=CENTARA&arrive={CHECK_IN}&chain={o['chain']}"
            f"&child={len(CHILD_AGES)}&childages={ages}&currency=KRW&depart={CHECK_OUT}&hotel={o['hotel']}"
            f"&level=hotel&locale=ko-KR&rooms=1&theme=CentaraGrand&themecode=CentaraGrand"
            + (f"&promo={promo}" if promo else ""))


def _cancel_ko(date_en):
    try:
        return datetime.strptime(date_en, "%d %b %Y").strftime("%Y.%-m.%-d까지")
    except Exception:
        return date_en


def scrape_official(ctx):
    import re
    t_js = (JS_DIR / "travelanium.js").read_text(encoding="utf-8")
    s_js = (JS_DIR / "synxis.js").read_text(encoding="utf-8")
    out = {}
    page = ctx.new_page()
    for key, o in OFFICIAL.items():
        try:
            if o["engine"] == "travelanium":
                page.goto(official_url(key), wait_until="domcontentloaded", timeout=60000)
                r = page.evaluate(t_js)
                b = r.get("best")
                if not b:
                    log(f"[official] {key}: 조식 포함 요금 없음 (offers={r.get('count')})")
                    continue
                perks = [re.sub(r"(\d+) tokens?/room/night", r"토큰 \1개/박(기념품·액티비티 교환)", p, flags=re.I)
                         for p in b.get("perks", []) if not re.match(r"breakfast", p, re.I)]
                notes = ["환불 불가" if not b["refundable"] else ""]
                if r.get("bestFlex") and r["bestFlex"] is not b:
                    f = r["bestFlex"]
                    notes.append(f"무료취소 요금 {f['perNight']:,}원" + (f"({_cancel_ko(f['freeCancel'])})" if f.get("freeCancel") else ""))
                promo = " / ".join(r.get("promos") or [])
                out[key] = {"perNight": b["perNight"], "total": b["perNight"] * NIGHTS,
                            "room": b["room"].title(),
                            "freeCancel": _cancel_ko(b["freeCancel"]) if b.get("freeCancel") else "",
                            "note": " · ".join(x for x in notes if x),
                            "promo": " · ".join(([promo] if promo else []) + (["직예약 특전: " + ", ".join(perks)] if perks else []))}
            else:
                # 공식 홈페이지에 걸린 프로모션 코드도 함께 시도
                codes = []
                try:
                    page.goto(o["promo_page"], wait_until="domcontentloaded", timeout=60000)
                    html = page.content()
                    codes = list(dict.fromkeys(re.findall(r"promo%3D([A-Z0-9]{4,20})", html)))[:3]
                except Exception as e:
                    log(f"[official] {key}: 프로모션 페이지 읽기 실패 {e}")
                cands, promo_hits = [], []
                for code in [""] + codes:
                    page.goto(official_url(key, code), wait_until="domcontentloaded", timeout=60000)
                    r = page.evaluate(s_js)
                    if r.get("best"):
                        cands.append((code, r))
                        if code:
                            promo_hits.append(f"코드 {code} 적용 가능")
                    elif code:
                        log(f"[official] {key}: 프로모션 코드 {code}는 이 날짜에 적용 안 됨")
                if not cands:
                    log(f"[official] {key}: 요금 없음")
                    continue
                code, r = min(cands, key=lambda c: c[1]["best"]["price"])
                b = r["best"]
                mult = TH_TAX if b.get("taxExcluded") else 1
                price = round(b["price"] * mult)
                notes = ["환불 불가" if not b["refundable"] else "", "세금 17.7% 포함 환산" if mult != 1 else ""]
                if b.get("member"):
                    notes.insert(0, f"Centara The1 회원가(무료 가입) · 비회원 {round(b['list'] * mult):,}원")
                if r.get("bestFlex") and not b["refundable"]:
                    notes.append(f"무료취소 요금 {round(r['bestFlex']['price'] * mult):,}원")
                rates = [x for x in (r.get("rates") or []) if x]
                def ko_rate(x):
                    x = re.sub(r" - CentaraThe1$", "", x)
                    x = re.sub(r"\bEXCL RB\b|\bGROSS\b|\bIBE\b", "", x)
                    x = x.replace("LONG STAY", "장기투숙 특가").replace("MEMBER", "(회원)")
                    return re.sub(r"\s+", " ", x).strip()
                promo = " / ".join(dict.fromkeys(ko_rate(x) for x in rates if not x.startswith("BAR")))
                if code:
                    promo = f"코드 {code} 적용가 · " + promo
                elif codes:
                    promo += " · 홈페이지 코드(" + ", ".join(codes) + ") 이 날짜 미적용"
                out[key] = {"perNight": price, "total": price * NIGHTS, "room": b["room"],
                            "note": " · ".join(x for x in notes if x), "promo": promo}
        except Exception as e:
            log(f"[official] {key} 실패: {e}")
    page.close()
    return out


# ── 카카오톡 ─────────────────────────────────────────────────
def kakao_send(text):
    key = os.environ.get("KAKAO_REST_KEY")
    refresh = os.environ.get("KAKAO_REFRESH_TOKEN")
    if not key or not refresh:
        log("[kakao] 키가 설정되지 않아 건너뜀")
        return
    data = {"grant_type": "refresh_token", "client_id": key, "refresh_token": refresh}
    if os.environ.get("KAKAO_CLIENT_SECRET"):
        data["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    tok = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=20).json()
    if "access_token" not in tok:
        log(f"[kakao] 토큰 갱신 실패: {tok.get('error')} {tok.get('error_description')}")
        return
    if tok.get("refresh_token"):  # 만료가 가까우면 새 refresh token이 옴 → 다음 단계에서 Secret 갱신
        (ROOT / ".new_refresh_token").write_text(tok["refresh_token"])
        log("[kakao] 새 refresh token 발급됨")
    link = {"web_url": PAGE_URL, "mobile_web_url": PAGE_URL} if PAGE_URL else {}
    tpl = {"object_type": "text", "text": text[:200], "link": link, "button_title": "가격 페이지"}
    r = requests.post("https://kapi.kakao.com/v2/api/talk/memo/default/send",
                      headers={"Authorization": f"Bearer {tok['access_token']}"},
                      data={"template_object": json.dumps(tpl, ensure_ascii=False)}, timeout=20)
    log(f"[kakao] 전송 결과: {r.status_code} {r.text[:200]}")


# ── 실행 ─────────────────────────────────────────────────────
def main():
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    latest = {}
    for r in sorted(history, key=lambda x: x["ts"]):
        latest[(r["hotel"], r["site"])] = r

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", user_agent=UA,
                                  viewport={"width": 1366, "height": 900})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        results = {}
        for site, fn in (("monkey", scrape_monkey), ("official", scrape_official),
                         ("booking", scrape_booking), ("taecho", scrape_taecho),
                         ("google", scrape_flights)):
            t0 = time.time()
            results[site] = fn(ctx)
            log(f"[{site}] {len(results[site])}/{1 if site == 'google' else 3} 완료 ({time.time() - t0:.0f}s): "
                + ", ".join(f"{k}={v.get('perNight')}" for k, v in results[site].items()))
        browser.close()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rows, changes = [], []
    for site, per in results.items():
        for hotel, v in per.items():
            row = {"ts": ts, "hotel": hotel, "site": site, "breakfast": True,
                   **{k: val for k, val in v.items() if val not in ("", None) or k == "perNight"}}
            new_rows.append(row)
            prev = latest.get((hotel, site))
            if prev is not None and prev.get("perNight") != row.get("perNight"):
                if site in COMPARE_SITES or row.get("perNight"):
                    changes.append((hotel, site, prev.get("perNight"), row.get("perNight")))

    if not new_rows:
        log("수집된 가격이 없어 기록하지 않음")
        return
    history.extend(new_rows)
    if not DRY_RUN:
        HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{len(new_rows)}건 기록, 변동 {len(changes)}건")

    if changes:
        now = {(r["hotel"], r["site"]): r for r in history}
        cands = [(r["perNight"], h, s) for (h, s), r in now.items()
                 if s in COMPARE_SITES and h != "flight" and isinstance(r.get("perNight"), int)]
        fmt = lambda v: f"{v:,}원" if isinstance(v, int) else "없음"
        lines = ["[파타야 호텔 가격 변동]"]
        for h, s, a, b in sorted(changes, key=lambda c: (c[1] not in COMPARE_SITES, -abs((c[3] or 0) - (c[2] or 0)))):
            diff = ""
            if isinstance(a, int) and isinstance(b, int):
                diff = f"({'▼' if b < a else '▲'}{abs(b - a):,})"
            lines.append(f"{NAMES[h]}·{SITE_NAMES[s]} {fmt(a)}→{fmt(b)}{diff}")
        if cands:
            v, h, s = min(cands)
            lines.append(f"최저: {NAMES[h]}·{SITE_NAMES[s]} {v:,}원")
        text = "\n".join(lines)
        while len(text) > 200 and len(lines) > 3:
            lines.pop(-2); text = "\n".join(lines)
        log(text)
        if not DRY_RUN:
            kakao_send(text)


if __name__ == "__main__":
    main()
