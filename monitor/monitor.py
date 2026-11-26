#!/usr/bin/env python3
"""실시간 감시: 조건이 맞는 순간 휴대폰(ntfy)으로 알림.

launchd가 15분마다 실행한다. Claude 없이 파이썬만 돈다.
- 코인 (24시간): 코인 true 스캐너를 LIVE 모드(진행 중인 봉 포함)로 실행
- BTC: 61,300 돌파 / 58,900 이탈 (실시간 가격)
- 주식 (평일 09:00~15:40 KST): 주식 true 스캐너 실행 (오늘 진행 중인 일봉 포함)

흔들림 방지: 새 신호는 2번 연속(약 15~30분) 확인돼야 알리고,
빠짐도 2번 연속 빠져야 알린다. 같은 종목은 하루 1번만 '신규'로 알린다.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".claude", "scheduled-tasks")
HERE = os.path.join(BASE, "live-monitor")
STATE = os.path.join(HERE, "state.json")
LOG = os.path.join(HERE, "monitor.log")
NOTIFY = os.path.join(BASE, "coin-true-scanner", "notify.sh")
COIN_SCAN = os.path.join(BASE, "coin-true-scanner", "scan.py")
STOCK_SCAN = os.path.join(BASE, "stock-true-scanner", "stock_scan.py")
KST = timezone(timedelta(hours=9))
sys.path.insert(0, HERE)
import holdings as HD  # noqa: E402
BTC_UP, BTC_DOWN = 61_300, 58_900
CONFIRM = 2


def log(msg):
    with open(LOG, "a") as f:
        f.write(f"{datetime.now(KST):%Y-%m-%d %H:%M} {msg}\n")


def notify(title, body, pr=4):
    try:
        out = subprocess.run(["sh", NOTIFY, title, body[:1500], str(pr)], capture_output=True,
                             text=True, timeout=30).stdout.strip()
    except Exception as e:  # noqa: BLE001
        out = f"err {e}"
    log(f"NOTIFY [{title}] {body} -> {out}")


def run_json(script, env_extra=None):
    env = dict(os.environ, **(env_extra or {}))
    r = subprocess.run([sys.executable, "-I", script], capture_output=True, text=True,
                       timeout=600, cwd="/tmp", env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    return json.loads(r.stdout)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:  # noqa: BLE001
        return {}


def confirm_set(st, key, current, today):
    """2회 연속 확인된 신규/이탈만 돌려준다. st[key] = {active, pend_in, pend_out, told}"""
    s = st.setdefault(key, {"active": [], "pend_in": {}, "pend_out": {}, "told": {}})
    active = set(s["active"])
    new_in, new_out = [], []
    for k in current:
        if k in active:
            s["pend_out"].pop(k, None)
            continue
        s["pend_in"][k] = s["pend_in"].get(k, 0) + 1
        if s["pend_in"][k] >= CONFIRM:
            active.add(k)
            s["pend_in"].pop(k)
            if s["told"].get(k) != today:
                new_in.append(k)
                s["told"][k] = today
    for k in list(s["pend_in"]):
        if k not in current:
            s["pend_in"].pop(k)
    for k in list(active):
        if k in current:
            continue
        s["pend_out"][k] = s["pend_out"].get(k, 0) + 1
        if s["pend_out"][k] >= CONFIRM:
            active.discard(k)
            s["pend_out"].pop(k)
            new_out.append(k)
    s["active"] = sorted(active)
    s["told"] = {k: v for k, v in s["told"].items() if v == today}
    return new_in, new_out


# ------------------------------------------------------------------ BTC
def check_btc(st):
    p = float(HD.X.binance_get("/api/v3/ticker/price?symbol=BTCUSDT")["price"])
    zone = "up" if p > BTC_UP else "down" if p < BTC_DOWN else "mid"
    b = st.setdefault("btc", {"zone": None, "pend": None, "cnt": 0})
    if b["zone"] is None:
        b["zone"] = zone
        return p
    if zone == b["zone"]:
        b["pend"], b["cnt"] = None, 0
        return p
    if b["pend"] == zone:
        b["cnt"] += 1
    else:
        b["pend"], b["cnt"] = zone, 1
    if b["cnt"] >= CONFIRM:
        msg = {"up": f"[BTC 상승 전환 신호] 61.3k 돌파 유지\n현재가: ${p:,.0f}\n저점과 고점 모두 높아짐, 상승 전환 신호\n※ 일봉 종가(09:00)로 최종 확인",
               "down": f"[BTC 하락 경계] 58.9k 이탈 유지\n현재가: ${p:,.0f}\n반등 실패, 추가 하락 경계\n※ 일봉 종가(09:00)로 최종 확인",
               "mid": f"[BTC 박스 복귀]\n현재가: ${p:,.0f}\n58.9k~61.3k 사이, 바닥 다지기"}[zone]
        notify("BTC 가격대 변화", msg)
        b["zone"], b["pend"], b["cnt"] = zone, None, 0
    return p


# ------------------------------------------------------------------ 코인
def fmt_coin(r, label):
    lv = r.get("levels", {})
    st, t1, t2 = lv.get("stop", {}), lv.get("target1_30d_high", {}), lv.get("target2", {})
    tm = lv.get("time", {})
    ind = lv.get("indicators", {})
    h1 = tm.get("target1", {}).get("hit_14d")
    hs = tm.get("stop", {}).get("hit_14d")
    ex = "/".join(r.get("kr_exchanges", [])) or "-"
    return "\n".join([
        f"{r['symbol']}  ({ex})",
        f"현재가: ${r['now_price']:g}  (24h {r.get('chg24', 0):+.1f}%)",
        f"목표: ${t1.get('price', 0):g} ({t1.get('pct', 0):+.0f}%), 2차 ${t2.get('price', 0):g} ({t2.get('pct', 0):+.0f}%)",
        f"손절: ${st.get('price', 0):g} ({st.get('pct', 0):+.0f}%, {st.get('basis', '')})",
        f"기간: 14일 내 목표 {h1}%, 손절 {hs}%",
        f"지표: RSI {ind.get('rsi14', 0):.0f}, 거래량 {ind.get('volume_7d_vs_30d', 0)}배, BTC대비 7일 {r.get('rs7_vs_btc', 0):+.0f}%p",
        "※ 장중 신호, 일봉 마감 전 바뀔 수 있음",
    ])


def gate_step(st, key, raw):
    """시장 게이트도 2번 연속 확인 후에만 바꾼다. 바뀌면 새 값을, 아니면 None을 돌려준다."""
    g = st.setdefault(key, {"on": None, "pend": None, "cnt": 0})
    if g["on"] is None:
        g["on"] = raw
        return None
    if raw == g["on"]:
        g["pend"], g["cnt"] = None, 0
        return None
    g["cnt"] = g["cnt"] + 1 if g["pend"] == raw else 1
    g["pend"] = raw
    if g["cnt"] >= CONFIRM:
        g["on"], g["pend"], g["cnt"] = raw, None, 0
        return raw
    return None


def check_coins(st, today):
    d = run_json(COIN_SCAN, {"LIVE": "1"})
    b = d["btc"]
    changed = gate_step(st, "coin_gate", b["gate_ok"])
    if changed is True:
        notify("코인 시장 추세 변화", f"[코인 시장 상승 추세 복귀]\nBTC ${b['close']:,.0f} > 60일선 ${b['ma60']:,.0f} (상승 중)\n이제부터 코인 종목 알림을 보내요")
    elif changed is False:
        notify("코인 시장 추세 변화", f"[코인 시장 하락 추세 전환]\nBTC ${b['close']:,.0f}, 60일선 ${b['ma60']:,.0f}\n복귀할 때까지 코인 종목 알림을 멈춰요")
    if not st["coin_gate"]["on"]:
        st.pop("coins", None)          # 엄격 모드: 시장이 꺼져 있으면 신호를 추적·알림하지 않음
        return f"gate_off({len(d['passed'])} 후보 무시)"
    rows = {r["symbol"]: r for r in d["passed"]}
    new_in, new_out = confirm_set(st, "coins", set(rows), today)
    label = "TRUE"
    new_in.sort(key=lambda x: -(rows[x].get("rs30_vs_btc", 0) + rows[x].get("rs7_vs_btc", 0)))
    tc = st.setdefault("tracked_coins", {})
    for x in new_in:
        r = rows[x]
        tc[x] = {"symbol": x, "entry": r["now_price"], "stop": r.get("levels", {}).get("stop", {}).get("price"),
                 "strategy": None, "date": today}
    if len(new_in) == 1:
        notify(f"코인 매수 신호: {new_in[0]}", fmt_coin(rows[new_in[0]], label))
    elif new_in:
        notify(f"코인 매수 신호 {len(new_in)}개", bundle_coin([rows[x] for x in new_in]))
    if new_out:
        notify("코인 신호 해제", "조건에서 빠짐: " + ", ".join(new_out))
    return len(rows)


# ------------------------------------------------------------------ 주식
def fmt_stock(r, label):
    lv = r["levels"]
    t = lv["time"]
    strat = " + ".join(r.get("strategies", [])) or "-"
    lines = [
        f"{r['name']} ({r['code']})" + (" [관심 종목]" if r.get("index") == "관심" else ""),
        f"전략: {strat}, 점수 {r.get('rank_score', r['domain_score'])}",
        f"현재가: {r['close']:,.0f}원 ({r['chg_pct']:+.1f}%), 거래대금 {r['trade_value_eok']:,.0f}억",
        f"목표: {lv['target1']['price']:,} ({lv['target1']['pct']:+.0f}%), 2차 {lv['target2']['price']:,} ({lv['target2']['pct']:+.0f}%)",
        f"손절: {lv['stop']['price']:,} ({lv['stop']['pct']:+.0f}%, {lv['stop']['basis']})",
        f"매도선: 10일선 {lv['exit_line_ma10']['price']:,}" + (f", 볼린저 상단 {r['bb_exit_upper']:,}" if "B(볼린저/OBV)" in r.get("strategies", []) else ""),
        f"기간: 10일 내 목표 {t['target1']['hit_10d']}%, 손절 {t['stop']['hit_10d']}%",
        f"근거: {', '.join(r['domain_hits']) or '-'}, RSI {r['indicators']['rsi9']}",
    ]
    if r.get("strong_hits"):
        lines.insert(2, "강한 자리: " + ", ".join(r["strong_hits"]))
    if r.get("warnings"):
        lines.append("주의: " + ", ".join(r["warnings"]))
    lines.append("※ 장중 신호, 종가로 확정")
    return "\n".join(lines)


BUNDLE_MAX = 10


def bundle_stock(rs):
    """신호 2개 이상: 알림 1개에 점수 순으로 종목당 2줄."""
    out = []
    for n, r in enumerate(rs[:BUNDLE_MAX], 1):
        lv = r["levels"]
        out.append(f"{n}. {r['name']} {r['close']:,.0f}원 ({r['chg_pct']:+.1f}%), 점수 {r.get('rank_score', r['domain_score'])}"
                   + (" [강한 자리]" if r.get("strong_hits") else ""))
        out.append(f"   목표 {lv['target1']['price']:,} ({lv['target1']['pct']:+.0f}%), "
                   f"손절 {lv['stop']['price']:,} ({lv['stop']['pct']:+.0f}%)")
    if len(rs) > BUNDLE_MAX:
        out.append(f"외 {len(rs) - BUNDLE_MAX}종목: " + ", ".join(r["name"] for r in rs[BUNDLE_MAX:]))
    out.append("※ 장중 신호, 종가로 확정")
    return "\n".join(out)


def bundle_coin(rs):
    out = []
    for n, r in enumerate(rs[:BUNDLE_MAX], 1):
        lv = r.get("levels", {})
        t1, st = lv.get("target1_30d_high", {}), lv.get("stop", {})
        out.append(f"{n}. {r['symbol']} ${r['now_price']:g} (24h {r.get('chg24', 0):+.1f}%), "
                   f"BTC대비 7일 {r.get('rs7_vs_btc', 0):+.0f}%p")
        out.append(f"   목표 ${t1.get('price', 0):g} ({t1.get('pct', 0):+.0f}%), "
                   f"손절 ${st.get('price', 0):g} ({st.get('pct', 0):+.0f}%)")
    if len(rs) > BUNDLE_MAX:
        out.append(f"외 {len(rs) - BUNDLE_MAX}개: " + ", ".join(r["symbol"] for r in rs[BUNDLE_MAX:]))
    out.append("※ 장중 신호, 일봉 마감 전 바뀔 수 있음")
    return "\n".join(out)


def stock_hours(now):
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 900 <= hm <= 1540


def check_stocks(st, today):
    d = run_json(STOCK_SCAN)
    if not d.get("market_open_today"):
        return "휴장"
    k = d["market"]["detail"].get("kospi") or {}
    st.pop("stock_gate", None) if not isinstance(st.get("stock_gate"), dict) else None
    changed = gate_step(st, "stock_gate", d["market"]["gate_ok"])
    if changed is True:
        notify("주식 시장 추세 변화", f"[코스피 상승 추세 복귀]\n코스피 {k.get('close', 0):,.0f} (60일선 위, 60일선 상승)\n이제부터 종목 알림을 보내요")
    elif changed is False:
        notify("주식 시장 추세 변화", f"[코스피 하락 추세 전환]\n코스피 {k.get('close', 0):,.0f} (60일선 아래 또는 60일선 하락)\n복귀할 때까지 종목 알림을 멈춰요")
    if not st["stock_gate"]["on"]:
        st.pop("stocks", None)         # 엄격 모드: 시장이 꺼져 있으면 신호를 추적·알림하지 않음
        return f"gate_off({len(d['passed'])} 후보 무시)"
    rows = {r["code"]: r for r in d["passed"]}
    new_in, new_out = confirm_set(st, "stocks", set(rows), today)
    label = "TRUE"
    new_in.sort(key=lambda x: (-rows[x].get("rank_score", rows[x]["domain_score"]), -(rows[x]["levels"]["reward_risk"] or 0)))
    ts = st.setdefault("tracked_stocks", {})
    for x in new_in:
        r = rows[x]
        ts[x] = {"code": x, "name": r["name"], "yahoo": r.get("yahoo") or x + (".KS" if r["index"] == "KOSPI200" else ".KQ"),
                 "entry": r["close"], "stop": r["levels"]["stop"]["price"],
                 "strategy": "B" if any(z.startswith("B") for z in r.get("strategies", [])) else "A",
                 "date": today}
    if len(new_in) == 1:
        r0 = rows[new_in[0]]
        notify(f"주식 {'강한 ' if r0.get('strong_hits') else ''}매수 신호: {r0['name']}", fmt_stock(r0, label))
    elif new_in:
        ns = sum(1 for x in new_in if rows[x].get("strong_hits"))
        notify(f"주식 매수 신호 {len(new_in)}종목" + (f" (강한 자리 {ns})" if ns else ""),
               bundle_stock([rows[x] for x in new_in]))
    if new_out:
        prev = st.get("stock_names", {})
        notify("주식 신호 해제", "조건에서 빠짐: " + ", ".join(prev.get(c, c) for c in new_out))
    st["stock_names"] = {**st.get("stock_names", {}), **{c: r["name"] for c, r in rows.items()}}
    return len(rows)


def main():
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    try:
        commands_only()                               # 텔레그램 명령 먼저 처리
    except Exception as e:  # noqa: BLE001
        log(f"commands ERR {str(e)[:120]}")
    st = load_state()
    parts = []
    for name, fn in [("btc", lambda: check_btc(st)),
                     ("coins", lambda: check_coins(st, today)),
                     ("stocks", (lambda: check_stocks(st, today)) if stock_hours(now) else None)]:
        if fn is None:
            continue
        try:
            parts.append(f"{name}={fn()}")
        except Exception as e:  # noqa: BLE001
            parts.append(f"{name}=ERR {str(e)[:120]}")
    try:
        sh = stock_hours(now) or (now.weekday() < 5 and 1540 < now.hour * 100 + now.minute <= 1600)
        parts.append("sell=" + HD.check_positions(st, notify, sh))
    except Exception as e:  # noqa: BLE001
        parts.append(f"sell=ERR {str(e)[:120]}")
    for key in ("tracked_stocks", "tracked_coins"):   # 30일 지난 신호 종목은 추적 종료
        cut = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        st[key] = {k: v for k, v in st.get(key, {}).items() if v.get("date", today) >= cut}
    json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=1)
    log("run " + " ".join(parts))


def commands_only():
    """1분마다: 휴대폰 명령만 처리 (상태는 별도 파일)."""
    path = os.path.join(HERE, "cmd_state.json")
    try:
        cst = json.load(open(path))
    except Exception:  # noqa: BLE001
        cst = {}
    n = HD.poll_commands(cst, notify)
    json.dump(cst, open(path, "w"))
    if n:
        log(f"commands {n}")


def report(kind):
    """하루 1번 요약 (무음 알림). kind: stock | coin"""
    now = datetime.now(KST)
    head = f"{now:%Y-%m-%d} "
    try:
        commands_only()
    except Exception:  # noqa: BLE001
        pass
    h = HD.load()
    if kind == "stock":
        d = run_json(STOCK_SCAN)
        if not d.get("market_open_today"):
            notify("오늘의 주식 점검", head + "휴장 (데이터 " + str(d.get("data_date")) + ")", )
            return
        k = d["market"]["detail"].get("kospi") or {}
        lines = [f"시장: 코스피 {k.get('close', 0):,.0f} ({k.get('chg_1d', 0):+.2f}%), "
                 + ("상승 추세 (매수 신호 ON)" if d["market"]["gate_ok"] else "하락 추세 (매수 대기)")]
        if d["market"]["notes"]:
            lines.append("메모: " + ", ".join(d["market"]["notes"]))
        lines.append("")
        if d["market"]["gate_ok"]:
            for r in d["passed"][:3]:
                lv = r["levels"]
                lines += [f"{r['name']} {r['close']:,.0f}원 ({r['chg_pct']:+.1f}%)"
                          + (" [강한 자리]" if r.get("strong_hits") else ""),
                          f"목표: {lv['target1']['price']:,} ({lv['target1']['pct']:+.0f}%), "
                          f"손절: {lv['stop']['price']:,} ({lv['stop']['pct']:+.0f}%)",
                          f"기간: 10일 내 목표 {lv['time']['target1']['hit_10d']}%, 손절 {lv['time']['stop']['hit_10d']}%",
                          ""]
            if not d["passed"]:
                lines.append("조건 통과 종목 없음")
        else:
            lines.append(f"조건만 본 관찰 종목 {len(d['passed'])}개 (매수 신호 아님)")
        names = [x["name"] for x in h.get("stocks", [])]
        if names:
            lines.append("보유: " + ", ".join(names))
        lines.append("※ 1년 반 백테스트(코스피 +80%) 현재 방식 +125%, 최대 낙폭 -14%")
        notify("오늘의 주식 점검", head + "\n".join(lines), 3)
    else:
        d = run_json(COIN_SCAN)
        b = d["btc"]
        lines = [f"BTC ${b['close']:,.0f} (7일 {b['ret7']:+.1f}%, 30일 {b['ret30']:+.1f}%)",
                 f"60일선 ${b['ma60']:,.0f} ({(b['close'] / b['ma60'] - 1) * 100:+.1f}%), "
                 + ("상승 추세 (매수 신호 ON)" if b["gate_ok"] else "하락 추세 (매수 대기)"),
                 f"단기 박스: 58.9k ~ 61.3k", ""]
        if b["gate_ok"]:
            for r in d["passed"][:3]:
                lv = r.get("levels", {})
                lines += [f"{r['symbol']} ${r['now_price']:g}",
                          f"목표: ${lv.get('target1_30d_high', {}).get('price', 0):g}, "
                          f"손절: ${lv.get('stop', {}).get('price', 0):g}", ""]
            if not d["passed"]:
                lines.append("조건 통과 코인 없음")
        else:
            lines.append(f"조건만 본 관찰 코인 {len(d['passed'])}개 (매수 신호 아님)")
        coins = [x["symbol"] for x in h.get("coins", [])]
        if coins:
            lines.append("보유: " + ", ".join(coins))
        notify("오늘의 코인 점검", head + "\n".join(lines), 3)


if __name__ == "__main__":
    if "--commands" in sys.argv:
        commands_only()
    elif "--report" in sys.argv:
        report(sys.argv[sys.argv.index("--report") + 1])
    else:
        main()
