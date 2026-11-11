# JioBlackRock Flexi Cap — Telegram Portfolio Index Bot (Render Free Web Service)

Send `Hi` or `/index` to calculate a custom weighted share-price indicator using the 129 listed equity holdings in the supplied JioBlackRock Flexi Cap portfolio statement dated **31 October 2026**.

## What it reports

- Custom index with a starting value of 1,000 on 11-Nov-2026
- Percentage movement since the base date
- Number of holdings with usable prices and portfolio-weight coverage
- Top 5 and bottom 5 stock returns

## Important limitations

This is an estimated equity-basket indicator, **not** the fund NAV, an official benchmark, or an exact intraday portfolio valuation. It uses month-end disclosed weights and share-price changes. It does not fully include cash, debt, derivatives, expenses, fund flows, or trades after the disclosure. Prices may be delayed or unavailable. Do not make a sell/hold decision from this number alone.

The bot maps ISINs to NSE symbols using the NSE equity directory and downloads share prices through Yahoo Finance (`yfinance`). If the minimum number of stocks or portfolio-weight coverage cannot be priced, it refuses to show an index rather than presenting an incomplete result as complete.

## Deploy on GitHub + Render Free

1. Extract this ZIP file.
2. Create a GitHub repository and upload the contents of this folder directly into the repository root. Include `bot.py`, `webhook.py`, `holdings.csv`, `requirements.txt`, `render.yaml`, and `README.md`.
3. On Render, choose **New + → Blueprint** and connect the repository. Render reads `render.yaml` and creates a **Free Web Service** (not a Background Worker).
4. When prompted, enter your Telegram BotFather token as `TELEGRAM_BOT_TOKEN`. Never commit the token to GitHub.
5. Deploy. The app registers a Telegram webhook automatically using Render's `RENDER_EXTERNAL_URL`.
6. Open the bot in Telegram, press Start, and send `Hi`.

Create a bot with [@BotFather](https://t.me/BotFather): send `/newbot`, follow the prompts, and copy the token.

## Free-tier behaviour

Render Free Web Services can spin down after a period without inbound traffic. Telegram's webhook request should wake the service, but the first reply after an idle period can be delayed while the service starts. Free services also have monthly usage limits and are not intended for guaranteed always-on financial monitoring. Check Render's current free-tier terms.

## Optional environment variables

- `ALLOWED_CHAT_ID`: set your Telegram numeric chat ID to restrict access to yourself. Leave blank to allow anyone who finds the bot to use it.
- `BASE_DATE`: default `2026-11-11`
- `BASE_INDEX`: default `1000`
- `MIN_HOLDINGS`: default `80`
- `MIN_WEIGHT_COVERAGE`: default `75` (percent)

## Files

- `bot.py` — Telegram handlers and portfolio-index calculation
- `webhook.py` — FastAPI webhook server for Render's free web service
- `holdings.csv` — the 129 listed holdings extracted from the supplied statement
- `requirements.txt` — Python dependencies
- `render.yaml` — Render Free Web Service Blueprint


## Slow or missing replies — updated build

This build runs Telegram command handlers without blocking the webhook acknowledgement and downloads Yahoo Finance prices in batches of 20 with a 15-second request timeout per batch. The bot sends an immediate acknowledgement, then sends the calculated result separately. Some Yahoo Finance/NSE data may still be delayed or unavailable; the bot will report insufficient coverage rather than fabricate an index.

After replacing the files in GitHub, trigger a new Render deployment. Check Render Logs if no final result arrives.
