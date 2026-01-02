from __future__ import annotations

"""Connectivity & configuration sanity checks.

This module is designed to answer: "Am I really connected to the right account/venue?"
without having to run the full live loop.

Examples
  - Spot (no order):
      python -m quantbot.preflight --venue binance --symbol BTCUSDT

  - Futures (no order):
      python -m quantbot.preflight --venue binance_futures --symbol BTCUSDT

  - (Optional) place a tiny MARKET order (DANGEROUS: real trade):
      python -m quantbot.preflight --venue binance_futures --symbol BTCUSDT --do-order --side BUY --qty 0.001
"""

import argparse
import asyncio
from typing import Any, Dict

import httpx
from rich.console import Console
from rich.table import Table

from quantbot.config import get_settings
from quantbot.execution.adapters.binance_adapter import BinanceAdapter
from quantbot.execution.adapters.binance_futures_adapter import BinanceFuturesAdapter
from quantbot.execution.adapters.upbit_adapter import UpbitAdapter
from quantbot.execution.executor import OrderExecutor
from quantbot.common.types import OrderRequest
from quantbot.utils.time import utc_now


console = Console()


async def _server_time_ms(venue: str, settings) -> int | None:
    try:
        if venue == "binance":
            url = f"{settings.BINANCE_BASE_URL.rstrip('/')}/api/v3/time"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                r.raise_for_status()
                return int(r.json().get("serverTime"))
        if venue == "binance_futures":
            url = f"{settings.BINANCE_FUTURES_BASE_URL.rstrip('/')}/fapi/v1/time"
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                r.raise_for_status()
                return int(r.json().get("serverTime"))
        if venue == "upbit":
            # Upbit doesn't expose a simple "time" endpoint in the same way; skip.
            return None
    except Exception:
        return None
    return None


def _make_adapter(venue: str, settings):
    if venue == "binance":
        return BinanceAdapter(
            api_key=settings.BINANCE_API_KEY or "",
            api_secret=settings.BINANCE_API_SECRET or "",
            base_url=settings.BINANCE_BASE_URL,
        )
    if venue == "binance_futures":
        return BinanceFuturesAdapter(
            api_key=settings.BINANCE_API_KEY or "",
            api_secret=settings.BINANCE_API_SECRET or "",
            base_url=settings.BINANCE_FUTURES_BASE_URL,
        )
    if venue == "upbit":
        return UpbitAdapter(access_key=settings.UPBIT_ACCESS_KEY, secret_key=settings.UPBIT_SECRET_KEY)
    raise SystemExit(f"Unsupported venue for preflight: {venue}")


async def run(args) -> int:
    settings = get_settings()
    venue = str(args.venue).lower()

    t = Table(title="QuantBot Preflight")
    t.add_column("key")
    t.add_column("value")
    t.add_row("venue", venue)
    t.add_row("TRADING_ENABLED", str(bool(settings.TRADING_ENABLED)))
    if venue in {"binance", "binance_futures"}:
        t.add_row("api_key_present", str(bool(settings.BINANCE_API_KEY)))
        t.add_row("api_secret_present", str(bool(settings.BINANCE_API_SECRET)))
        if venue == "binance":
            t.add_row("base_url", str(settings.BINANCE_BASE_URL))
        else:
            t.add_row("base_url", str(settings.BINANCE_FUTURES_BASE_URL))
    console.print(t)

    # Time drift check (Binance only)
    st = await _server_time_ms(venue, settings)
    if st is not None:
        import time
        local = int(time.time() * 1000)
        drift_ms = local - st
        console.print(f"[cyan]Server time drift:[/cyan] local-server = {drift_ms} ms")

    adapter = _make_adapter(venue, settings)
    executor = OrderExecutor(adapter)

    # Basic reads
    try:
        px = await adapter.get_last_price(args.symbol)
    except Exception as e:
        console.print(f"[red]get_last_price failed[/red]: {e}")
        return 2

    try:
        equity = await adapter.get_equity()
    except Exception as e:
        console.print(f"[red]get_equity failed[/red]: {e}")
        return 2

    console.print(f"[green]OK[/green] last_price={px} equity={equity}")

    if not args.do_order:
        return 0

    # WARNING: This places a real order.
    if venue in {"binance", "binance_futures"} and not bool(settings.TRADING_ENABLED):
        console.print("[bold red]Refusing to place order[/bold red]: TRADING_ENABLED=false")
        return 2

    req = OrderRequest(
        venue=venue,  # type: ignore[arg-type]
        symbol=args.symbol,
        side=str(args.side).upper(),
        order_type=str(args.order_type).upper(),
        qty=float(args.qty),
        price=float(args.price) if args.price is not None else None,
        client_order_id=f"PREFLIGHT-{int(utc_now().timestamp())}",
        meta={"reduceOnly": bool(args.reduce_only)} if args.reduce_only is not None else {},
    )
    res = await executor.execute(req)
    console.print(f"[bold yellow]ORDER RESULT[/bold yellow]: status={res.update.status} filled={res.update.filled_qty} avg={res.update.avg_fill_price} order_id={res.update.order_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--venue", required=True, choices=["binance", "binance_futures", "upbit"])
    p.add_argument("--symbol", required=True)
    p.add_argument("--do-order", action="store_true", help="Place a real order (DANGEROUS)")
    p.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    p.add_argument("--order-type", default="MARKET", choices=["MARKET", "LIMIT"])
    p.add_argument("--qty", type=float, default=0.0)
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--reduce-only", action="store_true", help="Futures only")
    args = p.parse_args(argv)

    if args.do_order and args.qty <= 0:
        raise SystemExit("--do-order requires --qty > 0")

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
