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
HELP = ("명령 예시\n"
        "보유 한미사이언스 55200\n"
        "보유 008930 55200\n"
        "보유 SOL 85.2\n"
        "삭제 한미사이언스\n"
        "목록\n"
        "관심 제닉스로보틱스\n"
        "관심삭제 제닉스로보틱스\n"
        "관심목록\n"
        "※ 매수가를 빼면 현재가로 등록")


def load():
    try:
        return json.load(open(HOLD))
    except Exception:  # noqa: BLE001
        return {"stocks": [], "coins": [], "watch": []}


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


# ------------------------------------------------------------------ 명령 처리
def handle(text, notify):
    t = text.strip()
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
    if t in ("목록", "보유목록", "리스트"):
        lines = [f"{x['name']} 매수가 {x['buy_price']:,.0f}" for x in h["stocks"]]
        lines += [f"{x['symbol']} 매수가 {x['buy_price']:,.6g}" for x in h["coins"]]
        notify("보유 목록", "\n".join(lines) if lines else "보유 종목이 없어요.\n" + HELP)
        return
    notify("명령 안내", HELP)


def poll_commands(st, notify):
    """텔레그램 봇으로 온 새 메시지를 읽어 처리한다. 내 채팅(TELEGRAM_CHAT_ID)에서 온 것만."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return 0
    offset = st.get("tg_offset", 0)
    url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=0&offset={offset}"
    with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as r:
        ups = json.load(r).get("result", [])
    n = 0
    for u in ups:
        st["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or u.get("edited_message") or {}
        if str(m.get("chat", {}).get("id")) != str(chat) or not m.get("text"):
            continue
        text = m["text"].strip()
        if text in ("/start", "/help"):
            text = "도움말"
        try:
            handle(text, notify)
        except Exception as e:  # noqa: BLE001
            notify("명령 처리 실패", f"{text}\n{str(e)[:100]}")
        n += 1
    return n


# ------------------------------------------------------------------ 매도 신호
def fmt_sell(label, res, buy_price):
    ln = res["lines"]
    out = [f"{label} 현재가: {res['close']:,.6g}"
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
                notify(title, fmt_sell(label, res, item.get("buy_price") or item.get("entry")))
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
