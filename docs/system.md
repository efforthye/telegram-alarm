# 알림 시스템 구성

저장소: https://github.com/efforthye/telegram-alarm (2026-11-27 공개 전환). 규칙은 [매매 규칙](trading-rules.md).
- 실행: .github/workflows/bot.yml (alarm-bot) 이 monitor/bot.py 를 약 5시간 40분 돌리고 스스로 다음 실행을 띄움. 매시 7분 예약은 재시작용. alarm-monitor 는 수동 전용(test, report-*). 새 코드 반영하려면 진행 중인 alarm-bot 실행을 cancel → always 단계가 새 실행을 띄움.
- 텔레그램: 봇 @efforthye_telegram_alarm_bot, 알림은 그룹 채팅(TELEGRAM_CHAT_ID 시크릿 = 그룹 ID, 앞에 - 포함). 시크릿 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 는 사용자가 직접 등록.
- 명령: /search(국내 주식 전체, 코인 심볼·업비트 한글명, 미국 주식 한글명·티커), /list(보유 판정), /hold, /del, /watch, /unwatch, /watchlist. 메뉴로 누르면 입력창 열림, 다음 메시지를 인자로 처리.
- 감시: 장중 30분, 그 외 1시간. 브리핑: 평일 08:30 아침, 16:30 마감(관찰/추천 상위 5 + 매도 체크), 주말 09:00 코인. 엄격 모드(시장 60일선 조건)라 하락장엔 종목 매수 알림 없음.
- 상태: state 브랜치에 state.enc (봇 토큰 sha256 키로 AES 암호화, 커밋 1개 유지).
- 데이터: 야후(주식, 마지막 일봉 비면 5분봉으로 채움), 바이낸스 data-api.binance.vision(미국 서버에서 api.binance.com 차단), 업비트(한글명·원화 시세), KRX kind 상장목록.
- 로컬 맥의 예전 launchd 감시(~/Library/LaunchAgents/com.efforthye.coinstock-*.plist.disabled)와 Claude 예약 작업 4개는 꺼둠. 집 네트워크에서 ntfy.sh 가 막혀 텔레그램으로 옮김.

**Why:** 맥을 꺼도 휴대폰으로 알림을 받고, 그룹에서 다 같이 보며, 10초 안에 검색 답장을 받길 원함.
**How to apply:** 기능 수정 시 이 저장소를 고치고 push 후 alarm-bot 재시작. GitHub Actions 를 상시 서버로 쓰는 건 권장 용도가 아니라 막히면 다른 무료 서버로 옮길 것.
