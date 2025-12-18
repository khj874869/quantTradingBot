# VPS 배포 가이드 (Upbit / Binance / KIS + Namoo Bridge)

## 0) 공통 보안 체크리스트
- API 키 권한은 **조회 + 거래만**, **출금 권한은 절대 부여하지 않기**
- 가능하면 **IP 고정(Allowlist)** 켜기 (VPS 고정 IP 사용)
- `.env`는 절대 Git에 커밋하지 말기
- 초기에는 `TRADING_ENABLED=false`로 모니터링만 하며 로그/시그널 확인

## 1) Docker로 실행
```bash
cp .env.example .env
# .env 수정
docker compose up -d --build
docker compose logs -f bot
```

## 2) Upbit 설정 팁
- Upbit Open API는 **Fixed IP 등록 후에만 정상 호출되는 설정**을 지원합니다.
- VPS의 고정 IP를 등록하고, 권한은 조회/주문만 부여하세요.

## 3) Binance 설정 팁
- Binance API 키는 **IP Restriction(화이트리스트)**를 켜는 것을 권장합니다.
- Spot trading만 쓸 경우 Futures 권한은 끄세요.

## 4) KIS(한국투자증권) 설정 팁
- Access Token은 보통 **24시간 유효**입니다(봇이 자동 갱신).
- 실전/모의 도메인(`KIS_BASE_URL`)을 환경에 맞게 설정하세요.

## 5) Namoo(나무증권) 브릿지
- `namoo_bridge/README.md` 참고 (Windows + 32-bit Python 필요)
- 봇의 `.env`에서 `NAMOO_BRIDGE_URL`을 브릿지 주소로 지정
