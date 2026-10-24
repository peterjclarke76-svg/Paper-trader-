# PJ Paper Trader

A robot that paper trades the signal bot's rules every weekday at 7am Perth time and updates a review page.

- `signal_bot.py`: the rules, the backtest scorecard and today's signals
- `paper_bot.py`: places paper trades and builds `docs/index.html`
- `state.json`: the paper portfolio, created on the first run
- `.github/workflows/daily.yml`: the daily schedule

## One-time setup (about 10 minutes)

1. Create a free account at github.com.
2. Create a new repository called `paper-trader`, set to Public, which free GitHub Pages needs. Only paper trades are shown.
3. Upload all the files in this folder, keeping the `.github/workflows` folder.
4. Go to Settings → Pages → Source: "Deploy from a branch" → Branch `main`, folder `/docs` → Save.
   Your page will be at `https://<your-username>.github.io/paper-trader/`
5. To get phone alerts, install the **ntfy** app and subscribe to a hard-to-guess topic name, such as `pj-paper-7f3k9q`.
   On GitHub, go to Settings → Secrets and variables → Actions → New secret: name `NTFY_TOPIC`, value = that topic name.
6. Go to Actions → Paper trader → **Run workflow** to do the first run now.

To change the money or the watchlist, edit the settings at the top of `signal_bot.py`.

Paper money only. Not financial advice.
