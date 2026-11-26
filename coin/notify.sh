#!/bin/sh
# 사용법: notify.sh "제목" "본문" [우선순위 1-5, 기본 4] [받는 채팅ID들, 쉼표 구분. 기본 TELEGRAM_CHAT_ID]
# 텔레그램 봇으로 알림을 보낸다. 환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요.
# 본문에서는 <b>굵게</b> 만 태그로 쓰이고 나머지 <, >, & 는 글자로 보인다.
# 우선순위 3 이하는 무음(disable_notification)으로 보낸다. 출력: HTTP 상태코드
python3 -I -c '
import html, json, os, sys, urllib.request
title, body, pr, to = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
if not token or not chat:
    print("no-telegram-secrets"); sys.exit(0)
safe = html.escape(body, quote=False).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
text = f"<b>{html.escape(title, quote=False)}</b>\n\n{safe}"[:4000]
codes = []
for cid in [x.strip() for x in (to or chat).split(",") if x.strip()]:
    data = json.dumps({"chat_id": cid, "text": text, "parse_mode": "HTML",
                       "disable_notification": pr <= 3, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            codes.append(str(r.status))
    except Exception as e:
        codes.append(str(getattr(e, "code", "err")))
print(",".join(codes))
' "$1" "$2" "${3:-4}" "${4:-}"
