#!/bin/sh
# 사용법: notify.sh "제목" "본문" [우선순위 1-5, 기본 4]
# 텔레그램 봇으로 알림을 보낸다. 환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요.
# 우선순위 3 이하는 무음(disable_notification)으로 보낸다. 출력: HTTP 상태코드
python3 -I -c '
import json, os, sys, urllib.request
title, body, pr = sys.argv[1], sys.argv[2], int(sys.argv[3])
token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
if not token or not chat:
    print("no-telegram-secrets"); sys.exit(0)
data = json.dumps({"chat_id": chat, "text": f"[{title}]\n{body}"[:4000],
                   "disable_notification": pr <= 3}).encode()
req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print(r.status)
except Exception as e:
    print(getattr(e, "code", "err"))
' "$1" "$2" "${3:-4}"
