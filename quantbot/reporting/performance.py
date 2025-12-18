from __future__ import annotations
import pandas as pd
from sqlalchemy import select
from quantbot.storage.db import get_session
from quantbot.storage.models import OrderModel

def compute_trade_ledger() -> pd.DataFrame:
    """Build a simple FIFO ledger from filled orders.

    This is intentionally conservative/simplified:
      - Uses avg_fill_price when available.
      - If avg_fill_price is missing, falls back to order price.
      - Realized PnL is computed when SELL reduces a long position.
    """
    with get_session() as s:
        rows = list(s.execute(select(OrderModel).order_by(OrderModel.ts.asc())).scalars())

    orders = pd.DataFrame([{
        "ts": r.ts,
        "venue": r.venue,
        "symbol": r.symbol,
        "side": r.side,
        "status": r.status,
        "qty": float(r.filled_qty or r.qty or 0.0),
        "px": float(r.avg_fill_price or r.price or 0.0),
        "fee": float(r.fee or 0.0),
        "client_order_id": r.client_order_id,
        "order_id": r.order_id,
    } for r in rows])

    if orders.empty:
        return pd.DataFrame(columns=[
            "ts","venue","symbol","side","qty","px","fee",
            "pos_qty","avg_cost","realized_pnl","realized_pnl_net"
        ])

    orders = orders[orders["status"].isin(["FILLED", "DONE", "filled", "done"])].copy()
    if orders.empty:
        return pd.DataFrame(columns=[
            "ts","venue","symbol","side","qty","px","fee",
            "pos_qty","avg_cost","realized_pnl","realized_pnl_net"
        ])

    # FIFO per (venue, symbol)
    ledgers = []
    state: dict[tuple[str, str], dict[str, float]] = {}

    for _, o in orders.iterrows():
        key = (o["venue"], o["symbol"])
        st = state.setdefault(key, {"pos_qty": 0.0, "avg_cost": 0.0})
        pos_qty = float(st["pos_qty"])
        avg_cost = float(st["avg_cost"])
        qty = float(o["qty"])
        px = float(o["px"])
        fee = float(o["fee"])

        realized = 0.0
        if o["side"].upper() == "BUY":
            new_qty = pos_qty + qty
            if new_qty > 0:
                avg_cost = ((avg_cost * pos_qty) + (px * qty)) / new_qty
            pos_qty = new_qty
        else:
            sell_qty = min(qty, pos_qty) if pos_qty > 0 else 0.0
            realized = (px - avg_cost) * sell_qty
            pos_qty = max(0.0, pos_qty - qty)
            if pos_qty == 0.0:
                avg_cost = 0.0

        st["pos_qty"] = pos_qty
        st["avg_cost"] = avg_cost

        ledgers.append({
            **o.to_dict(),
            "pos_qty": pos_qty,
            "avg_cost": avg_cost,
            "realized_pnl": realized,
            "realized_pnl_net": realized - fee,
        })

    return pd.DataFrame(ledgers)


def performance_summary(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame([{
            "trades": 0,
            "realized_pnl": 0.0,
            "realized_pnl_net": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
        }])

    sells = ledger[ledger["side"].str.upper() == "SELL"].copy()
    trades = int(len(sells))
    pnl = float(sells["realized_pnl"].sum())
    pnl_net = float(sells["realized_pnl_net"].sum())
    wins = int((sells["realized_pnl"] > 0).sum())
    win_rate = (wins / trades) if trades else 0.0
    gross_profit = float(sells.loc[sells["realized_pnl"] > 0, "realized_pnl"].sum())
    gross_loss = float(-sells.loc[sells["realized_pnl"] < 0, "realized_pnl"].sum())
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    return pd.DataFrame([{
        "trades": trades,
        "realized_pnl": pnl,
        "realized_pnl_net": pnl_net,
        "win_rate": win_rate,
        "profit_factor": pf,
    }])

def export_orders_csv(path: str = "orders.csv") -> str:
    with get_session() as s:
        rows = list(s.execute(select(OrderModel).order_by(OrderModel.ts.asc())).scalars())
    df = pd.DataFrame([{
        "ts": r.ts, "venue": r.venue, "symbol": r.symbol, "side": r.side,
        "status": r.status, "qty": r.qty, "filled_qty": r.filled_qty,
        "avg_fill_price": r.avg_fill_price, "fee": r.fee,
        "client_order_id": r.client_order_id, "order_id": r.order_id
    } for r in rows])
    df.to_csv(path, index=False)
    return path


def export_ledger_csv(path: str = "ledger.csv") -> str:
    ledger = compute_trade_ledger()
    ledger.to_csv(path, index=False)
    return path
