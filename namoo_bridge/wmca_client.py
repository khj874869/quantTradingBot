from __future__ import annotations

"""Minimal wmca.dll wrapper template.

IMPORTANT
- QV/나무 OpenAPI는 Windows 메시지 기반 이벤트 구조입니다.
- DLL 함수 시그니처와 TR 입력/출력 스키마는 계정/버전마다 차이가 있을 수 있습니다.

이 파일은 다음을 제공합니다:
- DLL 로딩/연결(connect)
- (예시) TR 코드 기반 quote/balance/order 메서드

실제 운용을 위해서는:
- 당신이 사용하는 OpenAPI 개발가이드(TR 스펙)
- 또는 커뮤니티에서 공개된 TR 입력 필드명
에 맞춰 `TODO` 부분을 채워야 합니다.

프로젝트는 **실전 자동매매용 프로덕션 코드가 아니라, 구조/배포를 위한 레퍼런스**입니다.
"""

import ctypes
from ctypes import wintypes
from typing import Any


class WmcaClient:
    def __init__(self, dll_path: str):
        # Load 32-bit wmca.dll in 32-bit Python process
        self.dll_path = dll_path
        self.dll = ctypes.WinDLL(dll_path)

        # ---- exported functions (names commonly observed in open-source wrappers) ----
        # NOTE: exact signatures may differ. Adjust argtypes/restype to your env.
        # These names match patterns seen in community wrappers and qvopenapi bindings.
        self._bind("wmcaLoad", restype=ctypes.c_int)
        self._bind("wmcaFree", restype=ctypes.c_int)
        self._bind("wmcaSetServer", [ctypes.c_char_p], ctypes.c_int)
        self._bind("wmcaSetPort", [ctypes.c_int], ctypes.c_int)
        self._bind("wmcaConnect", restype=ctypes.c_int)
        self._bind("wmcaDisconnect", restype=ctypes.c_int)

        # Request/Query/Transact 계열은 TR 스펙과 결합되어 있어서 시그니처를 맞춰야 합니다.
        # 아래는 "자리"만 잡아둔 것이고, 실제로는 개발가이드 기준으로 수정 필요.
        # 예: wmcaRequest(tr: str, in_block: str, ...) / wmcaQuery(...) / wmcaTransact(...)
        #
        # self._bind("wmcaRequest", [...], ctypes.c_int)
        # self._bind("wmcaQuery", [...], ctypes.c_int)
        # self._bind("wmcaTransact", [...], ctypes.c_int)

        rc = int(self.dll.wmcaLoad())
        if rc != 0:
            raise RuntimeError(f"wmcaLoad failed: rc={rc}")

        self.connected = False

    def _bind(self, name: str, argtypes=None, restype=None):
        try:
            fn = getattr(self.dll, name)
        except AttributeError as e:
            raise AttributeError(f"wmca.dll export not found: {name}") from e
        if argtypes is not None:
            fn.argtypes = argtypes
        if restype is not None:
            fn.restype = restype

    def connect(self):
        # Some environments need wmcaSetServer/Port before connect.
        # TODO: set proper server/port for your account type if required.
        rc = int(self.dll.wmcaConnect())
        if rc != 0:
            raise RuntimeError(f"wmcaConnect failed: rc={rc}")
        self.connected = True

    def close(self):
        try:
            if self.connected:
                self.dll.wmcaDisconnect()
        finally:
            self.dll.wmcaFree()

    # ---------------------------
    # Business-level methods
    # ---------------------------

    def get_quote(self, symbol: str) -> dict[str, Any]:
        """Fetch last price/orderbook snapshot.

        TODO:
        - QV/나무 OpenAPI는 TR 코드(예: IVWUTKMST04)로 요청/응답을 받아야 합니다.
        - 여기서는 반환 스펙만 고정해 두었습니다.

        반환 예:
          {"symbol": "005930", "last_price": 70000, "orderbook": {...}}
        """
        # TODO: implement TR request and parse
        return {"symbol": symbol, "last_price": 0.0, "orderbook": None, "note": "TODO: implement IVWUTKMST04"}

    def get_equity(self) -> float:
        # TODO: implement balance TR (ex: c8201)
        return 0.0

    def get_positions(self) -> dict[str, float]:
        # TODO: implement balance TR (ex: c8201)
        return {}

    def place_order(self, req: dict[str, Any]) -> dict[str, Any]:
        """Place an order.

        TODO:
        - Implement c8101/c8102/c8104 TR mapping.
        - Return 최소한 order_id/status.
        """
        side = str(req.get("side", "BUY")).upper()
        tr = "c8101" if side == "BUY" else "c8102"
        return {
            "order_id": "",
            "status": "REJECTED",
            "filled_qty": 0.0,
            "avg_fill_price": None,
            "fee": None,
            "tr": tr,
            "note": "TODO: implement TR request/response parsing",
        }
