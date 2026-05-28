#!/usr/bin/env python3
"""Live arbitrage scanner: cross-exchange crypto, gold tokens, triangular, fee-adjusted."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

TIMEOUT = 12
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "arb-scanner/1.0"})


@dataclass
class Quote:
    source: str
    symbol: str
    bid: float
    ask: float
    fee_bps: float  # one-way taker fee in basis points

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        if self.mid <= 0:
            return 0.0
        return (self.ask - self.bid) / self.mid * 10000


def get_json(url: str, params: dict | None = None) -> Any:
    r = SESSION.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_binance_book(symbols: list[str]) -> dict[str, Quote]:
    out: dict[str, Quote] = {}
    data = get_json("https://data-api.binance.vision/api/v3/ticker/bookTicker")
    sym_set = set(symbols)
    for row in data:
        s = row["symbol"]
        if s not in sym_set:
            continue
        bid, ask = float(row["bidPrice"]), float(row["askPrice"])
        if bid <= 0 or ask <= 0:
            continue
        out[s] = Quote("Binance", s, bid, ask, fee_bps=10.0)  # ~0.10% taker
    return out


def fetch_bybit_spot(symbols: list[str]) -> dict[str, Quote]:
    out: dict[str, Quote] = {}
    for sym in symbols:
        try:
            data = get_json(
                "https://api.bybit.com/v5/market/tickers",
                {"category": "spot", "symbol": sym},
            )
            items = data.get("result", {}).get("list", [])
            if not items:
                continue
            row = items[0]
            bid, ask = float(row["bid1Price"]), float(row["ask1Price"])
            if bid <= 0 or ask <= 0:
                continue
            out[sym] = Quote("Bybit", sym, bid, ask, fee_bps=10.0)
        except Exception as e:
            print(f"  [warn] Bybit {sym}: {e}", file=sys.stderr)
    return out


def fetch_kraken(pairs: dict[str, str]) -> dict[str, Quote]:
    """pairs: our_symbol -> kraken pair name e.g. BTCUSDT -> XBTUSDT"""
    out: dict[str, Quote] = {}
    try:
        data = get_json(
            "https://api.kraken.com/0/public/Ticker",
            {"pair": ",".join(pairs.values())},
        )
        result = data.get("result", {})
        inv = {v: k for k, v in pairs.items()}
        for kpair, row in result.items():
            our = inv.get(kpair.replace("/", ""))
            if not our:
                for kp, sym in pairs.items():
                    if kp in kpair or kpair.startswith(kp[:3]):
                        our = sym
                        break
            # Kraken returns a/b/c arrays; index 0 bid, 1 ask
            bid = float(row["b"][0])
            ask = float(row["a"][0])
            label = our or kpair
            out[label] = Quote("Kraken", label, bid, ask, fee_bps=26.0)  # ~0.26% taker
    except Exception as e:
        print(f"  [warn] Kraken: {e}", file=sys.stderr)
    return out


def fetch_coingecko(ids: list[str]) -> dict[str, Quote]:
    """Mid only — useful as reference, not for executable arb."""
    out: dict[str, Quote] = {}
    try:
        data = get_json(
            "https://api.coingecko.com/api/v3/simple/price",
            {
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "false",
            },
        )
        mapping = {
            "bitcoin": "BTCUSDT",
            "ethereum": "ETHUSDT",
            "pax-gold": "PAXGUSDT",
            "tether-gold": "XAUTUSDT",
        }
        for cid, px in data.items():
            sym = mapping.get(cid, cid)
            p = float(px["usd"])
            # synthetic zero spread reference
            out[sym] = Quote("CoinGecko(ref)", sym, p, p, fee_bps=0)
    except Exception as e:
        print(f"  [warn] CoinGecko: {e}", file=sys.stderr)
    return out


def fetch_gold_refs() -> dict[str, Quote]:
    """Gold USD from free sources + tokenized gold on Binance."""
    out: dict[str, Quote] = {}
    # Metals-API style via goldprice.org alternative: use exchangerate.host + yfinance fallback
    try:
        # Free XAU from frankfurter doesn't exist; use metals.live or similar
        data = get_json("https://api.metals.live/v1/spot/gold")
        if isinstance(data, list) and data:
            px = float(data[0].get("price", data[0].get("spot", 0)))
            if px > 0:
                out["XAUUSD_ref"] = Quote("metals.live", "XAUUSD", px * 0.9995, px * 1.0005, fee_bps=0)
    except Exception:
        pass

    try:
        import yfinance as yf

        for ticker, name in [("GC=F", "GC_futures"), ("GLD", "GLD_etf")]:
            t = yf.Ticker(ticker)
            fi = t.fast_info
            last = float(fi.get("last_price") or fi.get("regularMarketPrice") or 0)
            if last <= 0:
                hist = t.history(period="1d")
                if not hist.empty:
                    last = float(hist["Close"].iloc[-1])
            if last <= 0:
                continue
            if ticker == "GLD":
                # GLD ~ 1/10 oz gold per share (approx); convert rough
                gold_per_share = 0.1
                px = last / gold_per_share
                label = "XAUUSD_via_GLD"
            else:
                px = last
                label = "XAUUSD_via_GC"
            spread = px * 0.0003
            out[label] = Quote("Yahoo", label, px - spread, px + spread, fee_bps=0)
    except Exception as e:
        print(f"  [warn] yfinance gold: {e}", file=sys.stderr)

    return out


def cross_exchange_arb(
    q1: Quote, q2: Quote, notional_usd: float = 1000.0
) -> dict | None:
    """
    Buy on cheaper ask venue, sell on higher bid venue.
    Profit if bid_high > ask_low after both taker fees.
    """
    # Direction A: buy q1, sell q2
    fee1 = q1.ask * (q1.fee_bps / 10000)
    fee2 = q2.bid * (q2.fee_bps / 10000)
    gross_a = q2.bid - q1.ask
    net_a = gross_a - fee1 - fee2
    net_a_pct = (net_a / q1.ask) * 100 if q1.ask else 0

    # Direction B: buy q2, sell q1
    fee1b = q2.ask * (q2.fee_bps / 10000)
    fee2b = q1.bid * (q1.fee_bps / 10000)
    gross_b = q1.bid - q2.ask
    net_b = gross_b - fee1b - fee2b
    net_b_pct = (net_b / q2.ask) * 100 if q2.ask else 0

    best_dir = None
    best_net = max(net_a, net_b)
    if net_a >= net_b and net_a > 0:
        best_dir = f"BUY@{q1.source} SELL@{q2.source}"
    elif net_b > 0:
        best_dir = f"BUY@{q2.source} SELL@{q1.source}"

    if best_net <= 0:
        return None

    units = notional_usd / max(q1.mid, q2.mid, 1e-9)
    return {
        "pair": q1.symbol,
        "leg_a": q1.source,
        "leg_b": q2.source,
        "bid_a": q1.bid,
        "ask_a": q1.ask,
        "bid_b": q2.bid,
        "ask_b": q2.ask,
        "spread_a_bps": round(q1.spread_bps, 2),
        "spread_b_bps": round(q2.spread_bps, 2),
        "direction": best_dir,
        "net_per_unit_usd": round(best_net, 6),
        "net_pct": round(max(net_a_pct, net_b_pct), 4),
        "est_profit_on_1k_usd": round(best_net * units, 2),
        "executable": q1.bid > 0 and q2.ask > 0,
    }


def gold_vs_token_arb(gold: Quote, token: Quote, notional: float = 1000.0) -> dict | None:
    """Compare tokenized gold (PAXG/XAUT) vs spot gold reference."""
    # PAXG should be ~1 troy oz
    ref_mid = gold.mid
    tok_mid = token.mid
    if ref_mid <= 0 or tok_mid <= 0:
        return None
    diff_pct = (tok_mid - ref_mid) / ref_mid * 100
    # Executable: sell expensive, buy cheap
    if token.bid > gold.ask:
        gross = token.bid - gold.ask
        fees = token.bid * token.fee_bps / 10000 + gold.ask * gold.fee_bps / 10000
        net = gross - fees
        direction = f"SELL {token.symbol}@{token.source} BUY gold@{gold.source}"
    elif gold.bid > token.ask:
        gross = gold.bid - token.ask
        fees = gold.bid * gold.fee_bps / 10000 + token.ask * token.fee_bps / 10000
        net = gross - fees
        direction = f"SELL gold@{gold.source} BUY {token.symbol}@{token.source}"
    else:
        net = 0
        direction = "no executable cross"

    return {
        "gold_ref": gold.source,
        "gold_mid": round(ref_mid, 2),
        "token": token.symbol,
        "token_source": token.source,
        "token_mid": round(tok_mid, 2),
        "diff_pct": round(diff_pct, 4),
        "direction": direction,
        "net_per_oz_usd": round(net, 4),
        "net_pct": round(net / ref_mid * 100, 4) if ref_mid else 0,
        "profitable_after_fees": net > 0,
        "est_profit_1k": round(net * (notional / ref_mid), 2) if net > 0 else 0,
    }


def triangular_arb(q: dict[str, Quote], a: str, b: str, c: str) -> dict | None:
    """
    Triangle: USDT->A via a/b, A->B via cross, B->USDT via c.
    Symbols: a=BTCUSDT, b=ETHUSDT, c=ETHBTC (Binance style)
    """
    if a not in q or b not in q or c not in q:
        return None
    qa, qb, qc = q[a], q[b], q[c]

    def fee(qt: Quote, px: float) -> float:
        return px * qt.fee_bps / 10000

    # Path 1: USDT -> BTC (buy a) -> ETH (sell BTC buy ETH on ETHBTC) -> USDT (sell b)
    # Start 1 USDT conceptually scaled
    usdt = 1000.0
    btc = (usdt / qa.ask) * (1 - qa.fee_bps / 10000)
    # On ETHBTC, to get ETH from BTC: buy ETH at ask of ETHBTC means spend BTC
    eth = (btc / qc.ask) * (1 - qc.fee_bps / 10000)
    usdt_back = (eth * qb.bid) * (1 - qb.fee_bps / 10000)
    profit1 = usdt_back - usdt
    profit1_pct = profit1 / usdt * 100

    # Path 2: USDT -> ETH -> BTC -> USDT
    eth2 = (usdt / qb.ask) * (1 - qb.fee_bps / 10000)
    btc2 = (eth2 * qc.bid) * (1 - qc.fee_bps / 10000)
    usdt_back2 = (btc2 * qa.bid) * (1 - qa.fee_bps / 10000)
    profit2 = usdt_back2 - usdt
    profit2_pct = profit2 / usdt * 100

    best = max(profit1, profit2)
    path = "USDT→BTC→ETH→USDT" if profit1 >= profit2 else "USDT→ETH→BTC→USDT"

    return {
        "triangle": f"{a}/{b}/{c}",
        "venue": qa.source,
        "path_best": path,
        "profit_usd_on_1k": round(best, 2),
        "profit_pct": round(max(profit1_pct, profit2_pct), 4),
        "profitable": best > 0,
        "path1_usd": round(profit1, 2),
        "path2_usd": round(profit2, 2),
    }


def main() -> None:
    print("=" * 70)
    print("ARBITRAGE SCANNER — live public feeds (fee-adjusted)")
    print(f"Time (UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}")
    print("=" * 70)

    crypto_syms = [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "PAXGUSDT",
        "XAUTUSDT",
        "ETHBTC",
    ]

    print("\n[1] Fetching quotes...")
    binance = fetch_binance_book(crypto_syms)
    bybit = fetch_bybit_spot([s for s in crypto_syms if s != "ETHBTC"])
    kraken = fetch_kraken(
        {
            "BTCUSDT": "XBTUSDT",
            "ETHUSDT": "ETHUSDT",
            "SOLUSDT": "SOLUSDT",
            "XRPUSDT": "XRPUSDT",
        }
    )
    gecko = fetch_coingecko(["bitcoin", "ethereum", "pax-gold", "tether-gold"])
    gold_refs = fetch_gold_refs()

    all_q: dict[str, list[Quote]] = {}
    for bucket in [binance, bybit, kraken, gecko, gold_refs]:
        for sym, q in bucket.items():
            all_q.setdefault(sym, []).append(q)

    print("\n[2] Latest prices (bid / ask | spread bps | fee bps):")
    print("-" * 70)
    for sym in sorted(all_q.keys()):
        for q in all_q[sym]:
            print(
                f"  {sym:14} {q.source:16} "
                f"bid={q.bid:>12.4f} ask={q.ask:>12.4f} "
                f"spr={q.spread_bps:>6.1f}bps fee={q.fee_bps:.0f}bps"
            )

    print("\n[3] CROSS-EXCHANGE crypto (same symbol, taker fees included):")
    print("-" * 70)
    opps = []
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "PAXGUSDT", "XAUTUSDT"]:
        quotes = [q for q in all_q.get(sym, []) if q.source != "CoinGecko(ref)"]
        for i in range(len(quotes)):
            for j in range(i + 1, len(quotes)):
                r = cross_exchange_arb(quotes[i], quotes[j])
                if r:
                    opps.append(r)

    opps.sort(key=lambda x: x["net_pct"], reverse=True)
    if not opps:
        print("  No profitable cross-exchange opportunities right now.")
    else:
        for o in opps[:15]:
            flag = "✓ PROFIT" if o["net_pct"] > 0.05 else "~ marginal"
            print(
                f"  [{flag}] {o['pair']} {o['leg_a']} vs {o['leg_b']}: "
                f"{o['direction']} | net={o['net_pct']:.4f}% "
                f"~${o['est_profit_on_1k_usd']}/1k"
            )

    print("\n[4] GOLD vs CRYPTO (PAXG/XAUT vs spot gold reference):")
    print("-" * 70)
    gold_spot = None
    for k in ["XAUUSD_ref", "XAUUSD_via_GC", "XAUUSD_via_GLD"]:
        if k in gold_refs:
            gold_spot = gold_refs[k]
            break

    gold_opps = []
    if gold_spot and "PAXGUSDT" in binance:
        r = gold_vs_token_arb(gold_spot, binance["PAXGUSDT"])
        if r:
            gold_opps.append(r)
    if gold_spot and "XAUTUSDT" in binance:
        r = gold_vs_token_arb(gold_spot, binance["XAUTUSDT"])
        if r:
            gold_opps.append(r)

    if not gold_opps:
        print("  Could not compare gold token vs reference (or no data).")
    else:
        for g in gold_opps:
            status = "✓ PROFIT" if g["profitable_after_fees"] else "✗ no edge after fees"
            print(
                f"  [{status}] {g['token']} ({g['token_source']}) vs {g['gold_ref']}: "
                f"gold~${g['gold_mid']} token~${g['token_mid']} diff={g['diff_pct']:.3f}%"
            )
            print(f"       {g['direction']} | net/oz=${g['net_per_oz_usd']} ({g['net_pct']:.4f}%)")

    print("\n[5] TRIANGULAR (single venue — Binance, fee-adjusted):")
    print("-" * 70)
    tri = triangular_arb(binance, "BTCUSDT", "ETHUSDT", "ETHBTC")
    if tri:
        st = "✓ PROFIT" if tri["profitable"] else "✗ no edge"
        print(
            f"  [{st}] {tri['triangle']} @ {tri['venue']}: best={tri['path_best']} "
            f"profit=${tri['profit_usd_on_1k']} ({tri['profit_pct']:.4f}%) on $1k"
        )

    print("\n[6] NEAR-MISS (largest gaps still below fee threshold):")
    print("-" * 70)
    near = []
    for sym in ["BTCUSDT", "ETHUSDT", "PAXGUSDT"]:
        quotes = [q for q in all_q.get(sym, []) if "ref" not in q.source]
        for i in range(len(quotes)):
            for j in range(i + 1, len(quotes)):
                q1, q2 = quotes[i], quotes[j]
                mid_diff = abs(q1.mid - q2.mid) / min(q1.mid, q2.mid) * 100
                combined_fee = (q1.fee_bps + q2.fee_bps) / 100
                near.append((mid_diff - combined_fee, sym, q1.source, q2.source, mid_diff, combined_fee))
    near.sort(reverse=True)
    for item in near[:8]:
        edge, sym, s1, s2, md, cf = item
        print(f"  {sym} {s1} vs {s2}: mid gap {md:.4f}% vs round-trip fees ~{cf:.3f}% → net ~{edge:.4f}%")

    print("\n" + "=" * 70)
    print("NOTES:")
    print("  • MT5 broker prices NOT included (need terminal/API keys).")
    print("  • Marginal edges <0.1% usually lost to slippage + withdrawal.")
    print("  • Cross-asset gold arb needs same settlement (PAXG=1oz, transfer time).")
    print("  • For high-frequency small profits: focus on latency, not public REST.")
    print("=" * 70)


if __name__ == "__main__":
    main()
