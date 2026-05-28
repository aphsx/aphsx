# Arb Discovery Mantra

Adapted from [9arm-skills debug-mantra](https://github.com/thananon/9arm-skills/tree/main/skills/engineering/debug-mantra).

**Problem:** Static “compare two broker mids” almost always shows **no arb** — that is the *steady-state*, not proof that edge never exists.

**Goal:** Find **when** and **where** edge appears (regime, event, instrument, latency), then reproduce it reliably.

## Recite (start of every discovery session)

> **Mantra:**
> 1. **First is reproducibility.** Can we reproduce edge under a *defined regime* (news, volatility, session, feed lag)?
> 2. **Know the no-edge path.** Why does the naive scan fail? Enumerate every knob that opens the window.
> 3. **Question the hypothesis.** What would disprove “arb exists here”?
> 4. **Every run is a breadcrumb.** Cross-reference all samples — one lucky tick is not arb.

---

## 1. Reproduce reliably (regime, not snapshot)

| State | Action |
|-------|--------|
| **Steady market** | Expect **no** cross-broker REST arb. Log as baseline breadcrumb. |
| **Flaky edge** | Raise event rate: poll 100ms during NFP/CPI; archive tick CSV; measure max gap vs fees. |
| **No repro** | Do not claim arb. Instrument MT5 + fast feed or wait for next calendar event. |

**Repro artifacts:** `scripts/arb_event_logger.py`, tick dumps, economic calendar IDs, VPS ping matrix.

## 2. Know the no-edge path (knob enumeration)

Why “broker A vs B mid” fails:

| Knob | Opens window when… |
|------|-------------------|
| **Feed latency** | Fast LP vs slow retail MT5 (50–200ms) |
| **News (NFP, CPI, FOMC)** | Spreads 5–50 pip; feeds desync 30–60s |
| **Volatility** | Gold token basis PAXG↔XAUT widens; tri residual spikes |
| **Session** | Asia illiquid vs London/NY open |
| **Instrument class** | Crypto perp **basis** vs spot; not FX spot vs spot |
| **Cross-asset lag** | Macro shock: DXY moves before EUR; BTC before ETH |
| **Broker policy** | Arb allowed + no freeze + no last-look |
| **Costs** | Raw/ECN + commission < embedded Standard spread |

Flip **one knob at a time** in experiments.

## 3. Falsify hypotheses (ranked)

Always hold 3–5 hypotheses; run **disproof first**.

| ID | Hypothesis | Disproof experiment | If survives |
|----|------------|---------------------|-------------|
| H1 | Cross-CEX REST arb is profitable | 10s poll Binance↔OKX; net after 0.2% fees | Only if gap > fees (rare) |
| H2 | Triangular spot arb profitable | 100× triangular residual bps | Need residual > 20bps sustained |
| H3 | PAXG/XAUT arb profitable | Track basis vs $9/oz fee floor | Trade only when basis > threshold |
| H4 | Edge only on **news window** | Log spread + cross-broker delta T-5→T+15 NFP | Build event bot |
| H5 | Edge only **latency** MT5 | Measure quote age vs CME/spot feed | FIX/VPS + slow broker |

## 4. Breadcrumb ledger (template)

```text
| # | Experiment | Knob | Observation | Rules in/out |
|---|------------|------|---------------|--------------|
| 1 | REST scan  | steady | BTC gap $10, fees $147 | OUT: no steady CEX arb |
| 2 | 5× tri     | steady | residual < 4bps | OUT: no tri arb |
| 3 | NFP window | news   | (pending) | … |
```

---

## Arb classes that survive scrutiny (not “two mids”)

1. **Latency arb** — fast feed → slow broker (forex/gold news).
2. **Event regime** — measure desync during NFP/CPI; broker must allow.
3. **Basis arb** — perp vs spot / funding harvest (crypto).
4. **Token basis** — PAXG vs XAUT when gap > fees + transfer.
5. **Cross-asset lag** — macro: DXY vs EUR, BTC vs ETH (statistical; not risk-free).
6. **Triangular** — only on volatile shitcoins or broken peg moments.

---

## Scrutinize the naive plan

Before building “compare Exness vs IC mids every minute”:

- **Simpler?** Monitor **one** slow broker vs **one** fast feed during **only** red-folder news.
- **Does diff prove behavior?** Trace: signal → order → fill → spread at fill → net.
- **Silent killers:** withdrawal, freeze, requote, swap, NFA FIFO.

**Verdict on steady REST scan:** reject as primary strategy; use as **baseline**, not hunter.

---

## Validation (before real money)

- [ ] Edge reproduced in **same regime** 3+ times
- [ ] Net positive after **measured** spread + commission + slippage
- [ ] Broker T&C allows strategy
- [ ] Leg-risk handler tested (one leg fails)
