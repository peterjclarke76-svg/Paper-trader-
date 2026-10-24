"""
PJ's Robot Paper Trader
=======================
Runs once each weekday morning (GitHub Actions does this for free).
  1. Gets the latest daily prices
  2. Applies the signal_bot rules and places PAPER trades (fake money)
  3. Saves everything in state.json
  4. Rebuilds docs/index.html - the page you review on your phone
  5. Sends a push message to the ntfy app (if NTFY_TOPIC is set)

No real money, no broker connection. Not financial advice.

Run by hand:   python paper_bot.py
Test offline:  python paper_bot.py --demo --simulate 120
"""
import json, os, sys, math, datetime as dt
from pathlib import Path
import pandas as pd

import signal_bot as sb

HERE = Path(__file__).parent
STATE = HERE / "state.json"
PAGE = HERE / "docs" / "index.html"

MARKETS = {
    "ASX":    {"label": "SMSF · ASX", "tickers": sb.ASX_WATCHLIST, "capital": sb.ASX_CAPITAL,
               "slots": sb.MAX_POSITIONS_ASX, "bench": "STW.AX", "bench_name": "ASX 200 ETF (STW)", "decimals": 0},
    "CRYPTO": {"label": "Crypto", "tickers": sb.CRYPTO_WATCHLIST, "capital": sb.CRYPTO_CAPITAL,
               "slots": sb.MAX_POSITIONS_CRYPTO, "bench": "BTC-AUD", "bench_name": "Just holding Bitcoin", "decimals": 6},
}


# ---------------- state ----------------
def new_state():
    return {"started": None, "trades": [],
            "markets": {m: {"cash": c["capital"], "start_capital": c["capital"], "positions": {},
                            "equity": [], "bench_start": None, "last_date": None}
                        for m, c in MARKETS.items()}}


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return new_state()


# ---------------- one trading day ----------------
def trade_day(state, mkey, close, bench_close):
    """close: prices up to and including the day being traded."""
    cfg, m = MARKETS[mkey], state["markets"][mkey]
    day = close.index[-1].date().isoformat()
    if m["last_date"] and day <= m["last_date"]:
        return []                                   # already traded this day
    pos = sb.positions(close)
    today = pos.iloc[-1]
    px = close.iloc[-1]
    fast = close.rolling(sb.FAST).mean().iloc[-1]
    strength = (px / fast - 1).fillna(-9)
    done = []

    def log(side, t, units, price, reason):
        fee = units * price * sb.COST_PER_TRADE
        rec = {"date": day, "market": mkey, "ticker": t, "side": side, "units": units,
               "price": round(float(price), 6), "fees": round(fee, 2), "reason": reason}
        state["trades"].append(rec)
        done.append(rec)
        return fee

    # 1) sells: anything held whose trend has broken
    for t, p in list(m["positions"].items()):
        if t in today.index and today[t] == 0 and not math.isnan(px[t]):
            fee = log("SELL", t, p["units"], px[t], f"closed below 50-day avg ({fast[t]:.2f})")
            proceeds = p["units"] * px[t] - fee
            state["trades"][-1]["pl"] = round(proceeds - p["cost"], 2)
            m["cash"] += proceeds
            del m["positions"][t]

    # 2) buys: strongest uptrends first, while slots and cash remain
    held_value = sum(p["units"] * px.get(t, p["cost"] / p["units"]) for t, p in m["positions"].items())
    equity = m["cash"] + held_value
    slot = equity / cfg["slots"]
    for t in strength.sort_values(ascending=False).index:
        if len(m["positions"]) >= cfg["slots"]:
            break
        if today[t] != 1 or t in m["positions"] or math.isnan(px[t]):
            continue
        budget = min(slot, m["cash"]) / (1 + sb.COST_PER_TRADE)
        units = round(budget / px[t], cfg["decimals"]) if cfg["decimals"] else math.floor(budget / px[t])
        if units <= 0:
            continue
        fee = log("BUY", t, units, px[t], f"uptrend, {strength[t]*100:.1f}% above 50-day avg")
        cost = units * px[t] + fee
        m["cash"] -= cost
        m["positions"][t] = {"units": units, "cost": round(cost, 2), "opened": day}

    # 3) mark to market + benchmark
    held_value = sum(p["units"] * float(px[t]) for t, p in m["positions"].items())
    b = float(bench_close.iloc[-1]) if bench_close is not None and len(bench_close.dropna()) else None
    if m["bench_start"] is None and b:
        m["bench_start"] = b
    m["equity"].append({"date": day, "value": round(m["cash"] + held_value, 2),
                        "bench": round(m["start_capital"] * b / m["bench_start"], 2) if b and m["bench_start"] else None})
    m["last_prices"] = {t: round(float(px[t]), 6) for t in m["positions"]}
    m["last_date"] = day
    state["started"] = state["started"] or day
    return done


# ---------------- review page ----------------
def money(n):
    return ("-$" if n < 0 else "$") + f"{abs(n):,.2f}"


def sparkline(points, bench):
    vals = [p for p in points] + [b for b in bench if b is not None]
    if len(points) < 2:
        return '<p class="muted">The chart appears after the second trading day.</p>'
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    W, H, P = 600, 140, 6
    def path(series):
        xs = [P + i * (W - 2 * P) / (len(points) - 1) for i in range(len(series))]
        return " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{H - P - (v - lo) / span * (H - 2 * P):.1f}"
                        for i, (x, v) in enumerate(zip(xs, series)) if v is not None)
    bpath = path(bench) if any(b is not None for b in bench) else ""
    return (f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Bot value versus benchmark over time">'
            f'<path d="{bpath}" fill="none" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="4 4"/>'
            f'<path d="{path(points)}" fill="none" stroke="var(--accent)" stroke-width="2.5"/></svg>')


def market_section(state, mkey):
    cfg, m = MARKETS[mkey], state["markets"][mkey]
    eq = m["equity"]
    if not eq:
        return f'<section><h2>{cfg["label"]}</h2><p class="muted">No trading days yet.</p></section>'
    last = eq[-1]
    ret = (last["value"] / m["start_capital"] - 1) * 100
    bret = (last["bench"] / m["start_capital"] - 1) * 100 if last["bench"] else None
    beat = None if bret is None else ret - bret
    verdict = ("" if beat is None else
               f'<span class="pill {"good" if beat >= 0 else "bad"}">{"Beating" if beat >= 0 else "Behind"} '
               f'{cfg["bench_name"]} by {abs(beat):.1f} pts</span>')
    sells = [t for t in state["trades"] if t["market"] == mkey and t["side"] == "SELL"]
    wins = sum(1 for t in sells if t.get("pl", 0) > 0)

    rows = []
    for t, p in sorted(m["positions"].items()):
        now = m.get("last_prices", {}).get(t)
        val = p["units"] * now if now else None
        pl = val - p["cost"] if val is not None else None
        cls = "" if pl is None else ("gain" if pl >= 0 else "loss")
        rows.append(f'<tr><td class="l"><b>{t}</b></td><td>{p["units"]:,}</td><td>{money(p["cost"] / p["units"])}</td>'
                    f'<td>{money(now) if now else "–"}</td><td class="{cls}">{money(pl) if pl is not None else "–"}</td>'
                    f'<td class="l muted">{p["opened"]}</td></tr>')
    open_tbl = "".join(rows) or '<tr><td colspan="6" class="l muted">Holding cash. No shares are in an uptrend right now.</td></tr>'

    return f'''
<section>
  <div class="sechead"><h2>{cfg["label"]}</h2>{verdict}</div>
  <div class="stats">
    <div><span class="k">Value now</span><span class="v">{money(last["value"])}</span></div>
    <div><span class="k">Bot return</span><span class="v {"gain" if ret >= 0 else "loss"}">{ret:+.1f}%</span></div>
    <div><span class="k">{cfg["bench_name"]}</span><span class="v">{"–" if bret is None else f"{bret:+.1f}%"}</span></div>
    <div><span class="k">Cash</span><span class="v">{money(m["cash"])}</span></div>
    <div><span class="k">Win rate</span><span class="v">{f"{wins/len(sells)*100:.0f}% of {len(sells)}" if sells else "–"}</span></div>
  </div>
  {sparkline([e["value"] for e in eq], [e["bench"] for e in eq])}
  <p class="legend"><span class="sw bot"></span>Bot <span class="sw bm"></span>{cfg["bench_name"]} (same starting money)</p>
  <h3>Open positions</h3>
  <div class="scroll"><table><thead><tr><th class="l">Ticker</th><th>Units</th><th>Avg cost</th><th>Last price</th><th>P/L</th><th class="l">Bought</th></tr></thead>
  <tbody>{open_tbl}</tbody></table></div>
</section>'''


def build_page(state, today_trades):
    trades = sorted(state["trades"], key=lambda t: t["date"], reverse=True)[:200]
    log_rows = "".join(
        f'<tr><td class="l">{t["date"]}</td><td class="l"><b>{t["ticker"]}</b></td>'
        f'<td class="l"><span class="side {t["side"].lower()}">{t["side"]}</span></td><td>{t["units"]:,}</td>'
        f'<td>{money(t["price"])}</td><td class="{"gain" if t.get("pl", 0) >= 0 else "loss"}">{money(t["pl"]) if "pl" in t else ""}</td>'
        f'<td class="l muted why">{t["reason"]}</td></tr>' for t in trades) \
        or '<tr><td colspan="7" class="l muted">No trades yet.</td></tr>'
    todays = ("<ul>" + "".join(f'<li><b>{t["side"]}</b> {t["units"]:,} {t["ticker"]} at {money(t["price"])}</li>' for t in today_trades) + "</ul>"
              if today_trades else '<p class="muted">No trades this morning. Nothing changed trend.</p>')
    updated = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%a %d %b %Y, %I:%M %p")

    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PJ Paper Trader</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;700;800&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root{{--bg:#f3f5f6;--surface:#fff;--line:#d9e0e3;--fg:#14211f;--muted:#5d6f6c;--accent:#0f6b5f;--gain:#13794a;--loss:#b3342b;--chip:#e4ece9;
--display:"Archivo","Helvetica Neue",Arial,sans-serif;--mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1514;--surface:#16201f;--line:#2a3836;--fg:#e4ecea;--muted:#8fa29e;--accent:#3fb8a4;--gain:#4cc488;--loss:#f07468;--chip:#1f2d2b;color-scheme:dark}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 var(--display);padding:20px max(16px,env(safe-area-inset-left)) 48px max(16px,env(safe-area-inset-right))}}
.wrap{{max-width:960px;margin:0 auto;display:grid;gap:26px}}
h1{{font-size:1.7rem;font-weight:800;margin:0}} h2{{font-size:1.15rem;margin:0}} h3{{font-size:.75rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);margin:18px 0 8px}}
.muted{{color:var(--muted)}} .gain{{color:var(--gain)}} .loss{{color:var(--loss)}}
section{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px}}
.sechead{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:12px}}
.pill{{font-size:.78rem;font-weight:700;padding:4px 10px;border-radius:99px;background:var(--chip)}} .pill.good{{color:var(--gain)}} .pill.bad{{color:var(--loss)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}}
.stats div{{display:grid;gap:2px}} .k{{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}}
.v{{font:600 1.15rem var(--mono);font-variant-numeric:tabular-nums}}
.chart{{width:100%;height:auto;margin-top:14px;display:block}}
.legend{{font-size:.8rem;color:var(--muted);display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:6px 0 0}}
.sw{{display:inline-block;width:18px;height:0;border-top:2.5px solid var(--accent);margin-left:8px}} .sw.bm{{border-top:1.5px dashed var(--muted)}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;min-width:560px}}
th,td{{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap;font-variant-numeric:tabular-nums}}
td{{font-family:var(--mono);font-size:.88rem}} th{{font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}}
.l{{text-align:left}} td.why{{font-family:var(--display);white-space:normal;min-width:180px}}
.side{{font:700 .7rem var(--display);padding:2px 8px;border-radius:99px;background:var(--chip)}} .side.buy{{color:var(--gain)}} .side.sell{{color:var(--loss)}}
.today ul{{margin:6px 0 0;padding-left:18px}} footer{{font-size:.8rem;color:var(--muted)}}
</style></head><body><div class="wrap">
<header><h1>PJ Paper Trader</h1><p class="muted" style="margin:4px 0 0">Robot paper trading since {state["started"] or "today"} · Updated {updated} (Perth)</p></header>
<section class="today"><h2>This morning</h2>{todays}</section>
{market_section(state, "ASX")}
{market_section(state, "CRYPTO")}
<section><h2>Trade log</h2><div class="scroll"><table><thead><tr><th class="l">Date</th><th class="l">Ticker</th><th class="l">Side</th><th>Units</th><th>Price</th><th>Banked P/L</th><th class="l">Why</th></tr></thead>
<tbody>{log_rows}</tbody></table></div></section>
<footer>Paper money only. Prices are daily closes from Yahoo Finance, with {sb.COST_PER_TRADE:.1%} fees on each trade. Not financial advice.</footer>
</div></body></html>'''
    PAGE.parent.mkdir(exist_ok=True)
    PAGE.write_text(html)


# ---------------- phone alert ----------------
def notify(today_trades, state):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    import urllib.request
    lines = [f'{t["side"]} {t["units"]:,} {t["ticker"]} @ {money(t["price"])}' for t in today_trades] or ["No trades today."]
    for mkey in MARKETS:
        eq = state["markets"][mkey]["equity"]
        if eq:
            ret = (eq[-1]["value"] / state["markets"][mkey]["start_capital"] - 1) * 100
            lines.append(f'{MARKETS[mkey]["label"]}: {money(eq[-1]["value"])} ({ret:+.1f}%)')
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data="\n".join(lines).encode(),
                                 headers={"Title": "Paper Trader", "Tags": "chart_with_upwards_trend",
                                          **({"Click": os.environ["PAGE_URL"]} if os.environ.get("PAGE_URL") else {})})
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print("Push failed:", e)


# ---------------- main ----------------
def main():
    simulate = int(sys.argv[sys.argv.index("--simulate") + 1]) if "--simulate" in sys.argv else 0
    state = new_state() if simulate else load_state()
    today_trades = []
    for mkey, cfg in MARKETS.items():
        close_all = sb.get_prices(list(dict.fromkeys(cfg["tickers"] + [cfg["bench"]])), 2)
        bench = close_all[cfg["bench"]] if cfg["bench"] in close_all else None
        close = close_all[[t for t in cfg["tickers"] if t in close_all]]
        if simulate:
            for i in range(len(close) - simulate, len(close)):
                today_trades_m = trade_day(state, mkey, close.iloc[: i + 1],
                                           bench.iloc[: i + 1] if bench is not None else None)
            today_trades += today_trades_m
        else:
            today_trades += trade_day(state, mkey, close, bench)
    STATE.write_text(json.dumps(state, indent=1))
    build_page(state, today_trades)
    notify(today_trades, state)
    print(f"{len(today_trades)} trade(s) today.")
    for t in today_trades:
        print(f'  {t["side"]} {t["units"]} {t["ticker"]} @ {t["price"]}')


if __name__ == "__main__":
    main()
