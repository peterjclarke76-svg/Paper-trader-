"""
PJ's Signal Bot  -  ASX (SMSF) + Crypto
=======================================
The bot does NOT place trades. It tells you what to do, and you place the
trades yourself in Macquarie / your crypto exchange.

What it does each time you run it:
  1. Downloads daily prices (free, from Yahoo Finance)
  2. BACKTEST: replays ~5 years of history through the rules -> scorecard
  3. TODAY: prints an action list -> BUY / SELL / HOLD / STAY OUT + how many units

THE RULES (simple trend-following - one of the oldest, most tested ideas):
  - IN (own it)  when the 50-day average is above the 200-day average
                 AND today's price is above the 50-day average
  - OUT (sell)   when price closes below the 50-day average
  Idea: only own things that are trending up, step aside when they turn down.

How to run in Google Colab:
  Box 1:  !pip install yfinance
  Box 2:  paste this whole file, press play

Not financial advice. It is a rules calculator. Paper trade it first.
"""

# ============ YOUR SETTINGS (edit these) ============
ASX_CAPITAL    = 50_000      # $ the SMSF will put into this strategy
CRYPTO_CAPITAL = 5_000       # $ for the crypto account
MAX_POSITIONS_ASX    = 5     # most shares to hold at once (money is split evenly)
MAX_POSITIONS_CRYPTO = 3
COST_PER_TRADE = 0.001       # 0.1% brokerage/spread per buy or sell
BACKTEST_YEARS = 5

ASX_WATCHLIST = [
    "BHP.AX", "RIO.AX", "FMG.AX", "CBA.AX", "NAB.AX", "WBC.AX", "ANZ.AX",
    "MQG.AX", "CSL.AX", "WES.AX", "WOW.AX", "TLS.AX", "WDS.AX", "GMG.AX",
    "ALL.AX", "XRO.AX", "NST.AX", "PLS.AX", "JBH.AX", "QBE.AX",
]
CRYPTO_WATCHLIST = ["BTC-AUD", "ETH-AUD", "SOL-AUD", "XRP-AUD", "ADA-AUD"]

FAST, SLOW = 50, 200
# ====================================================

import sys
import math
import numpy as np
import pandas as pd


def get_prices(tickers, years):
    """Daily closing prices, one column per ticker."""
    if "--demo" in sys.argv:              # offline test mode with fake data
        rng = np.random.default_rng(1)
        idx = pd.bdate_range(end=pd.Timestamp.today(), periods=252 * years + SLOW)
        steps = rng.normal(0.0004, 0.018, (len(idx), len(tickers)))
        return pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=idx, columns=tickers)

    import yfinance as yf
    data = yf.download(tickers, period=f"{years + 1}y", interval="1d",
                       auto_adjust=True, progress=False)
    close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
    if not isinstance(data.columns, pd.MultiIndex):
        close.columns = tickers
    return close.dropna(how="all").ffill()


def positions(close):
    """1 = in, 0 = out, for every day and ticker."""
    fast = close.rolling(FAST).mean()
    slow = close.rolling(SLOW).mean()
    pos = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    pos[(fast > slow) & (close > fast)] = 1
    pos[close < fast] = 0
    return pos.ffill().fillna(0)


def backtest(close, pos, years):
    """Per-ticker results of following the rules vs just buying and holding."""
    start = close.index[-1] - pd.DateOffset(years=years)
    close, pos = close[close.index >= start], pos[pos.index >= start]
    rows = []
    for t in close.columns:
        px = close[t].dropna()
        if len(px) < 100:
            continue
        p = pos[t].reindex(px.index)
        held = p.shift(1).fillna(0)                      # act on next day's move
        rets = px.pct_change().fillna(0)
        trades = p.diff().abs().fillna(0)
        strat = (1 + held * rets - trades * COST_PER_TRADE).cumprod()
        hold = (1 + rets).cumprod()
        yrs = (px.index[-1] - px.index[0]).days / 365.25

        # win rate per round trip
        wins = n = 0
        entry = None
        for d, v in p.diff().fillna(0).items():
            if v > 0:
                entry = px[d]
            elif v < 0 and entry:
                n += 1
                wins += px[d] > entry
                entry = None

        rows.append({
            "Ticker": t,
            "Bot %/yr": (strat.iloc[-1] ** (1 / yrs) - 1) * 100,
            "Hold %/yr": (hold.iloc[-1] ** (1 / yrs) - 1) * 100,
            "Bot worst drop %": (strat / strat.cummax() - 1).min() * 100,
            "Hold worst drop %": (hold / hold.cummax() - 1).min() * 100,
            "Trades": n,
            "Win %": (wins / n * 100) if n else float("nan"),
        })
    return pd.DataFrame(rows).set_index("Ticker").round(1)


def todays_actions(close, pos, capital, max_pos):
    """What to do today. Strongest trends get priority if too many are IN."""
    fast = close.rolling(FAST).mean()
    today, yday = pos.iloc[-1], pos.iloc[-2]
    strength = (close.iloc[-1] / fast.iloc[-1] - 1).fillna(-9)
    per_slot = capital / max_pos

    ins = [t for t in strength.sort_values(ascending=False).index if today[t] == 1]
    allowed = set(ins[:max_pos])

    rows = []
    for t in close.columns:
        price = close[t].iloc[-1]
        if math.isnan(price):
            continue
        if today[t] == 1 and yday[t] == 0:
            action = "BUY" if t in allowed else "SKIP (full)"
        elif today[t] == 0 and yday[t] == 1:
            action = "SELL ALL"
        elif today[t] == 1:
            action = "HOLD" if t in allowed else "IN TREND (no slot)"
        else:
            action = "stay out"
        units = per_slot / price
        rows.append({
            "Ticker": t,
            "Action": action,
            "Price": round(price, 4 if price < 5 else 2),
            "Units if buying": (round(units, 4) if "-AUD" in t else int(units)) if action == "BUY" else "",
            "Exit if closes below": round(fast[t].iloc[-1], 2),
        })
    order = {"BUY": 0, "SELL ALL": 1, "HOLD": 2}
    df = pd.DataFrame(rows)
    df["o"] = df["Action"].map(order).fillna(9)
    return df.sort_values(["o", "Ticker"]).drop(columns="o").set_index("Ticker")


def run(name, tickers, capital, max_pos):
    print("\n" + "=" * 70)
    print(f"  {name}")
    print("=" * 70)
    close = get_prices(tickers, BACKTEST_YEARS)
    pos = positions(close)

    score = backtest(close, pos, BACKTEST_YEARS)
    print(f"\nBACKTEST - last {BACKTEST_YEARS} years (after {COST_PER_TRADE:.1%} costs per trade)")
    print(score.to_string())
    print(f"\n  Average: bot {score['Bot %/yr'].mean():.1f}%/yr, worst drop {score['Bot worst drop %'].mean():.0f}%"
          f"  |  buy & hold {score['Hold %/yr'].mean():.1f}%/yr, worst drop {score['Hold worst drop %'].mean():.0f}%")

    print(f"\nTODAY'S ACTIONS - prices as of {close.index[-1].date()}  (${capital:,.0f} split into {max_pos} slots)")
    print(todays_actions(close, pos, capital, max_pos).to_string())


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    run("SMSF - ASX", ASX_WATCHLIST, ASX_CAPITAL, MAX_POSITIONS_ASX)
    run("PERSONAL - CRYPTO", CRYPTO_WATCHLIST, CRYPTO_CAPITAL, MAX_POSITIONS_CRYPTO)
    print("\nReminder: signals use yesterday's close. Place ASX orders during market hours.")
    print("Not financial advice - paper trade first.")
