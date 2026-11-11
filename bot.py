import os, io, logging, asyncio
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('jio-flexi-tracker')
TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
ALLOWED_CHAT_ID = os.getenv('ALLOWED_CHAT_ID', '').strip()
BASE_DATE = date.fromisoformat(os.getenv('BASE_DATE', '2026-11-11'))
BASE_INDEX = float(os.getenv('BASE_INDEX', '1000'))
MIN_HOLDINGS = int(os.getenv('MIN_HOLDINGS', '80'))
MIN_WEIGHT_COVERAGE = float(os.getenv('MIN_WEIGHT_COVERAGE', '75'))
HOLDINGS_FILE = os.getenv('HOLDINGS_FILE', 'holdings.csv')
HOLDINGS_DATE = '31 October 2026'
NSE_SYMBOLS_URL = 'https://archives.nseindia.com/content/equities/EQUITY_L.csv'
TZ = ZoneInfo('Asia/Kolkata')


def load_holdings():
    df = pd.read_csv(HOLDINGS_FILE, dtype=str)
    required = {'company', 'isin', 'industry', 'weight_pct'}
    if not required.issubset(df.columns):
        raise RuntimeError(f'holdings.csv must contain columns: {", ".join(sorted(required))}')
    df['weight_pct'] = pd.to_numeric(df['weight_pct'], errors='coerce')
    df = df.dropna(subset=['weight_pct'])
    df = df[df['isin'].str.match(r'^INE[A-Z0-9]{9}$', na=False)]
    if len(df) < MIN_HOLDINGS:
        raise RuntimeError(f'Only {len(df)} holdings found in holdings.csv; expected at least {MIN_HOLDINGS}.')
    return df


def resolve_nse_symbols(holdings):
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'text/csv,*/*'}
    response = requests.get(NSE_SYMBOLS_URL, headers=headers, timeout=30)
    response.raise_for_status()
    nse = pd.read_csv(io.BytesIO(response.content))
    nse.columns = [str(c).strip().upper() for c in nse.columns]
    if 'ISIN NUMBER' not in nse.columns or 'SYMBOL' not in nse.columns:
        raise RuntimeError('NSE symbol directory format changed; cannot safely map holdings by ISIN.')
    nse['ISIN NUMBER'] = nse['ISIN NUMBER'].astype(str).str.strip().str.upper()
    nse['SYMBOL'] = nse['SYMBOL'].astype(str).str.strip().str.upper()
    isin_to_symbol = dict(zip(nse['ISIN NUMBER'], nse['SYMBOL']))
    result = holdings.copy()
    result['symbol'] = result['isin'].str.upper().map(isin_to_symbol)
    return result


def download_prices(symbols, start, end):
    """Download in small batches so one large Yahoo request cannot stall the bot."""
    prices = {}
    unique = list(dict.fromkeys(f"{s}.NS" for s in symbols))
    batch_size = 20
    for offset in range(0, len(unique), batch_size):
        batch = unique[offset:offset + batch_size]
        try:
            data = yf.download(
                tickers=batch,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
                timeout=15,
            )
        except Exception:
            log.exception("Yahoo Finance batch failed: %s-%s", offset + 1, min(offset + batch_size, len(unique)))
            continue

        if data is None or data.empty:
            continue

        for symbol in batch:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if symbol not in data.columns.get_level_values(0):
                        continue
                    series = data[symbol]["Close"].dropna()
                else:
                    # yfinance returns flat columns when a batch contains one ticker.
                    series = data["Close"].dropna()
                if not series.empty:
                    prices[symbol] = series
            except (KeyError, TypeError, AttributeError):
                continue

        log.info("Downloaded price data for %s/%s tickers", min(offset + batch_size, len(unique)), len(unique))
    return prices


def calculate_index():
    holdings = resolve_nse_symbols(load_holdings())
    today = datetime.now(TZ).date()
    if today < BASE_DATE:
        raise RuntimeError(f'The configured base date {BASE_DATE} is in the future.')
    mapped = holdings.dropna(subset=['symbol']).copy()
    if mapped.empty:
        raise RuntimeError('No holdings could be matched to NSE symbols by ISIN.')
    prices = download_prices(mapped['symbol'].tolist(), BASE_DATE, today)

    weighted_return_sum = 0.0
    covered_weight = 0.0
    movers = []
    missing = []
    for _, row in mapped.iterrows():
        ticker = f"{row['symbol']}.NS"
        series = prices.get(ticker)
        if series is None:
            missing.append(row['company'])
            continue
        base_rows = series[series.index.date >= BASE_DATE]
        if base_rows.empty:
            missing.append(row['company'])
            continue
        base_price = float(base_rows.iloc[0])
        latest_price = float(series.iloc[-1])
        if base_price <= 0:
            missing.append(row['company'])
            continue
        weight = float(row['weight_pct']) / 100.0
        stock_return = latest_price / base_price - 1.0
        weighted_return_sum += weight * stock_return
        covered_weight += weight
        movers.append((row['company'], stock_return * 100.0, weight * 100.0))

    if len(movers) < MIN_HOLDINGS or covered_weight < MIN_WEIGHT_COVERAGE / 100.0:
        raise RuntimeError(
            f'Insufficient live price coverage: {len(movers)}/{len(holdings)} stocks; '
            f'{covered_weight * 100:.1f}% of portfolio weight. Required: at least '
            f'{MIN_HOLDINGS} stocks and {MIN_WEIGHT_COVERAGE:.0f}% weight. No index shown.'
        )

    # Preserve original disclosed weights. Unpriced holdings are not silently redistributed.
    index_value = BASE_INDEX * (1.0 + weighted_return_sum)
    change_pct = (index_value / BASE_INDEX - 1.0) * 100.0
    movers.sort(key=lambda item: item[1])
    return {
        'index': index_value,
        'change': change_pct,
        'total': len(holdings),
        'priced': len(movers),
        'coverage': covered_weight * 100.0,
        'unmapped': int(holdings['symbol'].isna().sum()),
        'date': today,
        'top': movers[-5:][::-1],
        'bottom': movers[:5],
    }


def format_movers(items):
    return '\n'.join(f'• {name}: {ret:+.2f}% (weight {weight:.2f}%)' for name, ret, weight in items) or '• Not available'


async def send_reply(update: Update, text: str):
    await update.effective_message.reply_text(text, disable_web_page_preview=True)


def is_allowed(update: Update) -> bool:
    return not ALLOWED_CHAT_ID or str(update.effective_chat.id) == ALLOWED_CHAT_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await send_reply(update, 'JioBlackRock Flexi Cap — Portfolio Index Bot\nSend Hi or /index to calculate the weighted movement of all 129 disclosed equity holdings.')


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await send_reply(update, 'Send Hi, /index or /help. This is a custom equity-basket indicator based on the 31-Oct-2026 holdings and share-price movement since 11-Nov-2026. It is not the fund NAV, an official index, or an exact intraday portfolio value.')


async def index_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await send_reply(update, 'Checking the disclosed holdings and latest available share prices. This can take up to a few minutes; I will send the result in a separate message.')
    try:
        result = await asyncio.to_thread(calculate_index)
        marker = '🟢' if result['change'] >= 0 else '🔴'
        message = (
            f"{marker} JioBlackRock Flexi Cap — Custom Portfolio Index\n\n"
            f"Index: {result['index']:.2f}\n"
            f"Base index: {BASE_INDEX:.2f} on {BASE_DATE:%d-%b-%Y}\n"
            f"Movement since base: {result['change']:+.2f}%\n\n"
            f"Holdings snapshot: {HOLDINGS_DATE}\n"
            f"Price check date: {result['date']:%d-%b-%Y}\n"
            f"Stocks priced: {result['priced']}/{result['total']}\n"
            f"Portfolio weight covered: {result['coverage']:.2f}%\n"
            f"Holdings not matched to NSE: {result['unmapped']}\n\n"
            f"Top 5 stock returns:\n{format_movers(result['top'])}\n\n"
            f"Bottom 5 stock returns:\n{format_movers(result['bottom'])}\n\n"
            'How to read it: above 1,000 means the tracked equity basket is up since the base date; below 1,000 means it is down. '
            'This is an estimate, not the fund NAV or a sell/hold recommendation. Monthly holdings can change; cash, debt, derivatives, '
            'expenses, fund flows and trades after the disclosure are not fully represented. Prices may be delayed.'
        )
        await send_reply(update, message)
    except Exception as exc:
        log.exception('Index calculation failed')
        await send_reply(update, f"I couldn't safely calculate the full-portfolio index.\nReason: {exc}\nNo partial index has been shown.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or '').strip().lower()
    if text in {'hi', 'hello', 'hey'}:
        await index_cmd(update, context)


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('index', index_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info('Telegram polling started')
    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
