# namoo_bridge (Windows only)

나무증권/​QV OpenAPI는 **32-bit Windows DLL(wmca.dll)** 기반이고, 이벤트(윈도우 메시지)로 TR 응답을 받는 구조라서
리눅스/서버 환경에서 바로 REST처럼 호출하기가 어렵습니다.

이 폴더는 **"Windows에서 32-bit 파이썬으로 DLL을 감싸서 FastAPI로 노출"** 하는 브릿지 템플릿입니다.

## 준비물
- Windows 10/11
- 나무증권(또는 NH QV) OpenAPI 설치 (wmca.dll 포함)
- Python **32-bit** (중요)
- (권장) 별도 PC 한 대를 브릿지 전용으로 두고, 본 봇은 Linux/Mac/WSL에서 실행

## 설치
```powershell
# 32-bit 파이썬 가상환경 생성 후
pip install fastapi uvicorn[standard] pywin32 ctypes-windows-sdk
```

## 실행
```powershell
set WMCA_DLL_PATH=C:\path\to\wmca.dll
uvicorn namoo_bridge.server:app --host 0.0.0.0 --port 8700
```

## 봇에서 사용
`.env`에

```
NAMOO_BRIDGE_URL=http://<bridge-ip>:8700
```

을 지정하면 `quantbot/execution/adapters/namoo_adapter.py`가 HTTP로 주문/시세를 호출합니다.

## 구현 상태
- `server.py`는 엔드포인트 스펙(quote/order/equity/positions)까지는 제공.
- 실제 DLL 연결/응답 파싱은 브로커 계정/버전에 따라 달라질 수 있어, **TR 스키마에 맞춰 `wmca_client.py`의 TODO 부분을 채워야** 합니다.

> 참고 TR 코드(커뮤니티에서 많이 언급):
> - 시세/호가: `IVWUTKMST04`
> - 잔고: `c8201`
> - 매수: `c8101`
> - 매도: `c8102`
> - 취소: `c8104`
