#!/usr/bin/env python3
"""보유 종목 관리 + 텔레그램 명령 + 매도 신호.

텔레그램 봇 채팅(TELEGRAM_CHAT_ID와 같은 채팅에서 온 메시지만)으로 보내면 반영된다.
  보유 한미사이언스 55200      (매수가 생략하면 현재가로 등록)
  보유 008930 55200
  보유 SOL 85.2               (코인은 심볼)
  삭제 한미사이언스            (또는: 매도완료 한미사이언스)
  목록
  도움말
결과는 알림 주제로 답장한다.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exits as X  # noqa: E402

S = X.S
KST = timezone(timedelta(hours=9))
HOLD = os.path.join(HERE, "holdings.json")
UNIVERSE = json.load(open(os.path.join(os.path.dirname(S.__file__), "universe.json")))
HELP = ("입력창에 / 를 치면 명령 메뉴가 나와요. 그룹에서는 / 명령만 읽을 수 있어요.\n"
        "\n"
        "<b>종목 검색</b>\n"
        "/search 현대차  (또는 /검색 현대차, 검색 현대차)\n"
        "/search 005380\n"
        "/search SOL\n"
        "\n"
        "<b>보유 종목</b> (매도 신호 감시)\n"
        "/hold 한미사이언스 55200\n"
        "/hold SOL 85.2\n"
        "/del 한미사이언스\n"
        "/list  (보유 종목별 지금 판정)\n"
        "\n"
        "<b>관심 종목</b> (매수 신호 감시)\n"
        "/watch 제닉스로보틱스\n"
        "/unwatch 제닉스로보틱스\n"
        "/watchlist\n"
        "\n"
        "<b>다른 사람 초대</b>\n"
        "상대가 봇에게 /start 를 보내면 요청이 와요\n"
        "승인 123456789\n"
        "구독해제 123456789\n"
        "구독자\n"
        "\n"
        "매수가를 빼면 현재가로 등록돼요.\n"
        "명령은 다음 점검 때(최대 30분~1시간) 처리돼요.")


def load():
    try:
        return json.load(open(HOLD))
    except Exception:  # noqa: BLE001
        return {"stocks": [], "coins": [], "watch": [], "subscribers": [], "pending": {}}


def save(h):
    json.dump(h, open(HOLD, "w"), ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ 종목 찾기
KRX = os.path.join(HERE, "krx_all.json")


def krx_all():
    """KRX 전체 상장 종목 (코스피+코스닥). 7일마다 새로 받는다."""
    import time
    try:
        if time.time() - os.path.getmtime(KRX) < 7 * 86400:
            return json.load(open(KRX))
    except Exception:  # noqa: BLE001
        pass
    out = []
    for mk, suf in (("stockMkt", ".KS"), ("kosdaqMkt", ".KQ")):
        url = f"https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType={mk}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
            html = r.read().decode("euc-kr", "ignore")
        for row in re.findall(r"<tr>(.*?)</tr>", html, re.S):
            c = [x.strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(c) > 3:
                out.append({"code": c[2], "name": c[0], "yahoo": c[2] + suf})
    if out:
        json.dump(out, open(KRX, "w"), ensure_ascii=False)
        return out
    return json.load(open(KRX)) if os.path.exists(KRX) else []


def find_stock(q):
    q = q.strip()
    norm = lambda x: x.replace(" ", "").upper()  # noqa: E731
    for src in (UNIVERSE, krx_all()):
        for u in src:
            if q in (u["code"], u["name"]) or norm(q) == norm(u["name"]):
                return {"code": u["code"], "name": u["name"], "yahoo": u["yahoo"]}
    if re.fullmatch(r"[0-9A-Z]{6}", q):
        for suf in (".KS", ".KQ"):
            if S.yahoo(q + suf, "1mo"):
                return {"code": q, "name": q, "yahoo": q + suf}
    return None


def find_coin(q):
    sym = q.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,12}", sym):
        return None
    try:
        X.binance_rows(sym, 2)
        return {"symbol": sym}
    except Exception:  # noqa: BLE001
        return None


def current_price(item):
    if "yahoo" in item:
        rows = S.yahoo(item["yahoo"], "5d")
        return rows[-1][4] if rows else None
    return X.binance_rows(item["symbol"], 1)[-1][4]


# ------------------------------------------------------------------ 종목 검색
def _p(x):
    return f"{x:+.1f}%"


def search_stock(item):
    rows = S.yahoo(item["yahoo"])
    a = S.analyze(rows)
    if not a:
        return f"<b>{item['name']}</b>\n데이터가 부족해서 판정할 수 없어요 (상장 기간이 짧음)."
    ex = X.check(rows)
    mk = S.market()
    lv = a["levels"]
    url = f"https://alphasquare.co.kr/home/stock-summary?code={item['code']}"
    core = (not a["stagnant"]) and (a["domain_score"] >= 3 or a["macd_zero_cross"])
    a_pass = a["buy_ok"] == 6 and core
    b_pass = a["bb_ok"] == 3 and not a["stagnant"]
    ok = (a_pass or b_pass) and not a["blockers"]
    if a["stagnant"]:
        verdict = "침체 구간, 매수 대상 아님"
    elif ok and mk["gate_ok"]:
        verdict = "매수 조건 통과"
    elif ok:
        verdict = "종목 조건 통과, 시장 하락 추세라 대기"
    elif a["buy_ok"] >= 5 or a["bb_ok"] >= 2:
        verdict = "근접 (조건 일부 미충족)"
    else:
        verdict = "매수 조건 미충족"
    miss = [k for k, v in a["buy_checks"].items() if not v]
    L = [f'<b><a href="{url}">{item["name"]}</a></b> ({item["code"]})',
         f"{a['date']} 종가 {a['close']:,.0f}원 ({_p(a['chg_pct'])}), 거래대금 {a['trade_value_eok']:,.0f}억",
         "",
         "<b>판정</b>",
         verdict,
         "시장: " + ("상승 추세" if mk["gate_ok"] else "하락 추세 (코스피 60일선 아래 또는 하락 중)"),
         "",
         "<b>조건</b>",
         f"6조건 {a['buy_ok']}/6" + (f" (빠짐: {', '.join(miss)})" if miss else ""),
         f"볼린저/OBV {a['bb_ok']}/3",
         f"도메인 점수 {a['domain_score']}" + (f" ({', '.join(a['domain_hits'])})" if a["domain_hits"] else ""),
         f"RSI {a['indicators']['rsi9']}, 거래량 {a['indicators']['vol_ratio_20']}배"]
    if a.get("strong_hits"):
        L.append("강한 자리: " + ", ".join(a["strong_hits"]))
    if a["warnings"]:
        L += ["", "<b>주의</b>"] + a["warnings"]
    t = lv.get("time", {})
    L += ["", "<b>가격</b>",
          f"목표 {lv['target1']['price']:,} ({lv['target1']['pct']:+.0f}%)",
          f"손절 {lv['stop']['price']:,} ({lv['stop']['pct']:+.0f}%, {lv['stop']['basis']})",
          f"10일선 {lv['exit_line_ma10']['price']:,} ({lv['exit_line_ma10']['pct']:+.0f}%)"]
    if t:
        L.append(f"10일 내 목표 {t['target1']['hit_10d']}%, 손절 {t['stop']['hit_10d']}%")
    L += ["", "<b>보유 중이라면</b>", f"{ex['verdict']}: " + ", ".join(ex["reasons"][:3])]
    return "\n".join(L)


def search_coin(sym):
    rows = X.binance_rows(sym)
    ex = X.check(rows)
    c = [r[4] for r in rows]
    ma60 = sum(c[-60:]) / 60
    ma60_prev = sum(c[-65:-5]) / 60
    btc = [r[4] for r in X.binance_rows("BTC", 80)]
    btc_gate = btc[-1] > sum(btc[-60:]) / 60 > sum(btc[-65:-5]) / 60
    r7 = (c[-1] / c[-8] - 1) * 100
    b7 = (btc[-1] / btc[-8] - 1) * 100
    ln = ex["lines"]
    L = [f"<b>{sym}</b>",
         f"현재가 ${c[-1]:,.6g}",
         "",
         "<b>판정</b>",
         "코인 시장: " + ("상승 추세" if btc_gate else "하락 추세 (BTC 60일선 아래 또는 하락 중)"),
         f"60일선 ${ma60:,.6g} (" + ("위" if c[-1] > ma60 else "아래") + ", " + ("상승" if ma60 > ma60_prev else "하락") + " 중)",
         f"7일 {_p(r7)} (BTC 대비 {r7 - b7:+.1f}%p)",
         f"RSI {ex['rsi9']}",
         "",
         "<b>가격</b>",
         f"전고점 ${ln['prev_high']:,.6g} ({_p((ln['prev_high'] / c[-1] - 1) * 100)})",
         f"10일선 ${ln['ma10']:,.6g} ({_p((ln['ma10'] / c[-1] - 1) * 100)})",
         f"78.6% ${ln['fib786']:,.6g} ({_p((ln['fib786'] / c[-1] - 1) * 100)})",
         "", "<b>보유 중이라면</b>", f"{ex['verdict']}: " + ", ".join(ex["reasons"][:3])]
    return "\n".join(L)


# ------------------------------------------------------------------ 명령 처리
def handle(text, notify):
    t = text.strip()
    m = re.match(r"^(검색|분석|조회)\s+(.+)$", t)
    if m:
        q = m.group(2).strip()
        item = find_stock(q)
        if item:
            notify(f"종목 검색: {item['name']}", search_stock(item))
            return
        if find_coin(q):
            notify(f"코인 검색: {q.upper()}", search_coin(q.upper()))
            return
        notify("검색 실패", f"'{q}'을(를) 찾지 못했어요.\n종목명, 6자리 코드, 코인 심볼로 보내주세요.")
        return
    h = load()
    m = re.match(r"^(보유|추가|매수)\s+(.+?)(?:\s+([\d.,]+))?$", t)
    if m:
        name, price = m.group(2), m.group(3)
        item = find_stock(name) or find_coin(name)
        if not item:
            notify("보유 등록 실패", f"'{name}'을(를) 찾지 못했어요.\n주식은 종목명이나 6자리 코드, 코인은 심볼로 보내주세요.")
            return
        p = float(price.replace(",", "")) if price else current_price(item)
        item["buy_price"] = p
        item["added"] = datetime.now(KST).strftime("%Y-%m-%d")
        key = "stocks" if "yahoo" in item else "coins"
        idk = "code" if key == "stocks" else "symbol"
        h[key] = [x for x in h[key] if x[idk] != item[idk]] + [item]
        save(h)
        label = item.get("name") or item["symbol"]
        notify("보유 등록 완료", f"{label} 매수가 {p:,.6g}로 등록했어요.\n이제 매도 규칙을 정기적으로 확인해요.")
        return
    m = re.match(r"^(관심삭제|관심제거|관심해제)\s+(.+)$", t)
    if m:
        q = m.group(2).strip()
        before = len(h.get("watch", []))
        h["watch"] = [x for x in h.get("watch", []) if q not in (x["code"], x["name"])]
        save(h)
        ok = before != len(h["watch"])
        notify("관심 종목 삭제" if ok else "관심 삭제 실패", f"{q} {'관심 종목에서 뺐어요' if ok else '관심 목록에 없어요'}")
        return
    if t in ("관심목록", "관심 목록"):
        w = h.get("watch", [])
        notify("관심 종목 목록", "\n".join(f"{x['name']} ({x['code']})" for x in w) if w else "관심 종목이 없어요.")
        return
    m = re.match(r"^관심\s+(.+)$", t)
    if m:
        q = m.group(1).strip()
        item = find_stock(q)
        if not item:
            notify("관심 등록 실패", f"'{q}'을(를) 찾지 못했어요.\n종목명이나 6자리 코드로 보내주세요.")
            return
        in_uni = any(u["code"] == item["code"] for u in UNIVERSE)
        h["watch"] = [x for x in h.get("watch", []) if x["code"] != item["code"]] + [item]
        save(h)
        notify("관심 종목 등록", f"{item['name']} ({item['code']}) 등록했어요."
               + ("\n이미 기본 감시 대상(코스피200/코스닥150)이에요." if in_uni
                  else "\n장중 30분마다 매수 조건을 확인해요."))
        return
    m = re.match(r"^(삭제|매도완료|제거)\s+(.+)$", t)
    if m:
        q = m.group(2).strip()
        before = len(h["stocks"]) + len(h["coins"])
        h["stocks"] = [x for x in h["stocks"] if q not in (x["code"], x["name"])]
        h["coins"] = [x for x in h["coins"] if x["symbol"] != q.upper()]
        save(h)
        ok = before != len(h["stocks"]) + len(h["coins"])
        notify("보유 삭제" if ok else "보유 삭제 실패", f"{q} {'삭제했어요' if ok else '보유 목록에 없어요'}")
        return
    if t in ("목록", "보유목록", "리스트", "보유", "점검", "보유점검"):
        items = [(x["name"], x, x.get("code")) for x in h["stocks"]] + [(x["symbol"], x, None) for x in h["coins"]]
        if not items:
            notify("보유 점검", "보유 종목이 없어요.\n'보유 종목명 매수가'로 등록해 주세요.")
            return
        lines = []
        for label, x, code in items:
            try:
                rows = S.yahoo(x["yahoo"]) if "yahoo" in x else X.binance_rows(x["symbol"])
                r = X.check(rows, x.get("buy_price"))
                name = (f'<a href="https://alphasquare.co.kr/home/stock-summary?code={code}">{label}</a>'
                        if code else label)
                lines += [f"<b>{name}</b>  {r['close']:,.6g}  (매수가 {x['buy_price']:,.6g} 대비 {r['pnl_pct']:+.1f}%)",
                          f"판정: {r['verdict']}",
                          "이유: " + ", ".join(r["reasons"][:3]),
                          f"10일선 {r['lines']['ma10']:,.6g}, 78.6% {r['lines']['fib786']:,.6g}", ""]
            except Exception:  # noqa: BLE001
                lines += [f"{label}  확인 실패", ""]
        lines.append("판정 기준: 매도(바로 정리), 분할매도(일부 정리), 버티기(손실 중이지만 78.6% 위), 정상")
        notify("보유 점검", "\n".join(lines))
        return
    notify("명령 안내", HELP)


def poll_commands(st, notify):
    """텔레그램 봇으로 온 새 메시지 처리.
    주인(TELEGRAM_CHAT_ID): 모든 명령 가능.
    다른 사람: /start 를 보내면 주인에게 구독 요청이 간다. 주인이 '승인 ID' 하면 알림을 받는다.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    owner = (os.environ.get("TELEGRAM_CHAT_ID") or "").split(",")[0].strip()
    if not token or not owner:
        return 0
    set_bot_commands(token, st)
    offset = st.get("tg_offset", 0)
    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=0&offset={offset}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as r:
        ups = json.load(r).get("result", [])
    to_owner = lambda t, b, *a: notify(t, b, 4, "owner")  # noqa: E731
    n = 0
    for u in ups:
        st["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or u.get("edited_message") or u.get("my_chat_member") or {}
        cid = str(m.get("chat", {}).get("id", ""))
        text = normalize(m.get("text") or "")
        ctype = m.get("chat", {}).get("type")
        if cid and cid != owner and ctype in ("group", "supergroup"):
            # 아직 연결 안 된 그룹: 주인에게 그룹 ID를 한 번 알려준다
            told = st.setdefault("groups_told", [])
            if cid not in told:
                told.append(cid)
                notify("그룹 발견", f"그룹: {m['chat'].get('title', '')}\n그룹 ID: {cid}\n\n"
                       "이 그룹으로 알림을 받으려면 깃허브 Secrets의 TELEGRAM_CHAT_ID 값을 위 그룹 ID로 바꾸세요.",
                       4, "owner")
            continue
        if not cid or not text:
            continue
        n += 1
        if cid != owner and ctype == "private" and owner.startswith("-") and is_group_member(token, owner, cid):
            # 알림 그룹의 멤버가 개인 채팅으로 보낸 명령: 처리하고 개인 채팅으로 답장
            if text in ("/start", "/help"):
                text = "도움말"
            reply = lambda t, b, *a: notify(t, b, 4, [cid])  # noqa: E731
            try:
                if not handle_subs(text, reply):
                    handle(text, reply)
            except Exception as e:  # noqa: BLE001
                reply("명령 처리 실패", f"{text}\n{str(e)[:100]}")
            continue
        if cid != owner:
            who = m.get("from", {})
            name = (who.get("first_name", "") + " " + who.get("last_name", "")).strip() or who.get("username", "")
            h = load()
            if any(str(x["id"]) == cid for x in h.get("subscribers", [])):
                notify("안내", "이미 알림을 받고 있어요.\n시장 신호와 브리핑이 이 채팅으로 와요.", 3, [cid])
                continue
            notify("구독 요청", f"{name} (@{who.get('username', '-')})\nID: {cid}\n\n승인하려면 이렇게 보내세요:\n승인 {cid}",
                   4, "owner")
            notify("요청 보냄", "알림 받기 요청을 보냈어요.\n승인되면 이 채팅으로 알림이 와요.", 3, [cid])
            h.setdefault("pending", {})[cid] = name
            save(h)
            continue
        if text in ("/start", "/help"):
            text = "도움말"
        try:
            if not handle_subs(text, notify):
                handle(text, to_owner)
        except Exception as e:  # noqa: BLE001
            to_owner("명령 처리 실패", f"{text}\n{str(e)[:100]}")
    return n


ALIASES = {
    "search": "검색", "s": "검색", "hold": "보유", "buy": "보유", "del": "삭제", "sold": "삭제",
    "list": "목록", "check": "목록", "watch": "관심", "unwatch": "관심삭제", "watchlist": "관심목록",
    "help": "도움말", "start": "도움말", "subs": "구독자", "approve": "승인",
}
BOT_COMMANDS = [
    ("search", "종목/코인 검색. 예: /search 현대차"),
    ("list", "보유 종목 지금 판정 (매도/버티기)"),
    ("hold", "보유 등록. 예: /hold 한미사이언스 55200"),
    ("del", "보유 삭제. 예: /del 한미사이언스"),
    ("watch", "관심 종목 등록. 예: /watch 제닉스로보틱스"),
    ("unwatch", "관심 종목 삭제"),
    ("watchlist", "관심 종목 목록"),
    ("help", "명령 안내"),
]


def normalize(text):
    """'/search@봇 현대차' -> '검색 현대차', '/검색 현대차' -> '검색 현대차'"""
    t = text.strip()
    if not t.startswith("/"):
        return t
    head, _, rest = t[1:].partition(" ")
    head = head.split("@")[0]
    head = ALIASES.get(head.lower(), head)
    return (head + " " + rest).strip()


def set_bot_commands(token, st):
    if st.get("cmds_v") == 1:
        return
    try:
        data = json.dumps({"commands": [{"command": c, "description": d} for c, d in BOT_COMMANDS]}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/setMyCommands", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15):
            st["cmds_v"] = 1
    except Exception:  # noqa: BLE001
        pass


def is_group_member(token, group_id, user_id):
    try:
        url = f"https://api.telegram.org/bot{token}/getChatMember?chat_id={group_id}&user_id={user_id}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as r:
            st = json.load(r).get("result", {}).get("status")
        return st in ("creator", "administrator", "member", "restricted")
    except Exception:  # noqa: BLE001
        return False


def handle_subs(text, notify):
    """주인의 구독자 관리 명령. 처리했으면 True."""
    h = load()
    m = re.match(r"^승인\s+(-?\d+)$", text)
    if m:
        cid = m.group(1)
        name = h.get("pending", {}).pop(cid, cid)
        h["subscribers"] = [x for x in h.get("subscribers", []) if str(x["id"]) != cid] + [{"id": cid, "name": name}]
        save(h)
        notify("구독 승인", f"{name} ({cid}) 님이 이제 알림을 받아요.", 4, "owner")
        notify("알림 시작", "승인됐어요. 이제 시장 신호와 아침/마감 브리핑이 이 채팅으로 와요.", 4, [cid])
        return True
    m = re.match(r"^(구독해제|구독삭제)\s+(.+)$", text)
    if m:
        q = m.group(2).strip()
        before = len(h.get("subscribers", []))
        gone = [x for x in h.get("subscribers", []) if q in (str(x["id"]), x["name"])]
        h["subscribers"] = [x for x in h.get("subscribers", []) if x not in gone]
        save(h)
        notify("구독 해제", f"{q} {'해제했어요' if len(h['subscribers']) != before else '구독자 목록에 없어요'}", 4, "owner")
        for x in gone:
            notify("알림 종료", "알림 받기가 해제됐어요.", 3, [x["id"]])
        return True
    if text in ("구독자", "구독자목록"):
        subs = h.get("subscribers", [])
        pend = h.get("pending", {})
        lines = ["<b>구독자</b>"] + ([f"{x['name']} ({x['id']})" for x in subs] or ["없음"])
        if pend:
            lines += ["", "<b>승인 대기</b>"] + [f"{v} ({k})  승인 {k}" for k, v in pend.items()]
        notify("구독자 목록", "\n".join(lines), 4, "owner")
        return True
    return False


# ------------------------------------------------------------------ 매도 신호
def fmt_sell(label, res, buy_price, item_code=None):
    ln = res["lines"]
    code = (item_code or "")
    name = (f'<a href="https://alphasquare.co.kr/home/stock-summary?code={code}">{label}</a>' if code else label)
    out = [f"<b>{name}</b> 현재가: {res['close']:,.6g}"
           + (f" (매수가 {buy_price:,.6g} 대비 {res['pnl_pct']:+.1f}%)" if buy_price else ""),
           f"판정: {res['verdict']}",
           "이유: " + ", ".join(res["reasons"]),
           f"기준선: 78.6% {ln['fib786']:,.6g}, 10일선 {ln['ma10']:,.6g}, "
           f"60일선 {ln['ma60']:,.6g} ({'하락' if ln['ma60_falling'] else '상승'} 중)"]
    if ln.get("stop"):
        out.append(f"손절선: {ln['stop']:,.6g}")
    out.append("※ 장중 판정, 종가로 확정")
    return "\n".join(out)


def check_positions(st, notify, stock_hours_now):
    """보유 종목(직접 등록) + 매수 신호 종목(자동 추적)의 매도 판정. 판정이 올라갈 때 알림."""
    h = load()
    items = []
    if stock_hours_now:
        items += [("보유", x["name"], x, None, None) for x in h["stocks"]]
        items += [("신호", t["name"], t, t.get("stop"), t.get("strategy"))
                  for t in st.get("tracked_stocks", {}).values()]
    items += [("보유", x["symbol"], x, None, None) for x in h["coins"]]
    items += [("신호", t["symbol"], t, t.get("stop"), t.get("strategy"))
              for t in st.get("tracked_coins", {}).values()]
    last = st.setdefault("sell_state", {})
    done = []
    for kind, label, item, stop, strat in items:
        key = f"{kind}:{label}"
        try:
            rows = S.yahoo(item["yahoo"]) if "yahoo" in item else X.binance_rows(item["symbol"])
            res = X.check(rows, item.get("buy_price") or item.get("entry"), stop, strat)
        except Exception as e:  # noqa: BLE001
            done.append(f"{label}=ERR")
            continue
        lv = X.LEVEL.get(res["verdict"], 0)
        s = last.setdefault(key, {"lv": 0, "pend": 0, "cnt": 0})
        if lv > s["lv"]:
            s["cnt"] = s["cnt"] + 1 if s["pend"] == lv else 1
            s["pend"] = lv
            if s["cnt"] >= 2 and lv >= 2:          # 분할매도/매도만 알림, 2번 연속 확인
                title = f"{'보유 종목' if kind == '보유' else '신호 종목'} {res['verdict']} 신호: {label}"
                notify(title, fmt_sell(label, res, item.get("buy_price") or item.get("entry"), item.get("code")))
                s["lv"], s["pend"], s["cnt"] = lv, 0, 0
                if kind == "신호" and lv == 3:     # 신호 종목은 매도 알림 후 추적 종료
                    st.get("tracked_stocks", {}).pop(item.get("code", ""), None)
                    st.get("tracked_coins", {}).pop(item.get("symbol", ""), None)
                    last.pop(key, None)
            elif s["cnt"] >= 2:
                s["lv"], s["pend"], s["cnt"] = lv, 0, 0
        else:
            s["pend"], s["cnt"] = 0, 0
            if lv < s["lv"]:
                s["lv"] = lv                       # 회복하면 다음 악화 때 다시 알림
        done.append(f"{label}={res['verdict']}")
    return ",".join(done) or "없음"
