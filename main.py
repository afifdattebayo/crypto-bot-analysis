import aiohttp
import asyncio
import time
import os
import openai
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import logging
from cryptography.fernet import Fernet
from html import escape

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# Decrypt API keys
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if ENCRYPTION_KEY:
    cipher = Fernet(ENCRYPTION_KEY.encode())
    openai.api_key = cipher.decrypt(os.getenv("ENCRYPTED_OPENAI_KEY").encode()).decode()
else:
    openai.api_key = os.getenv("OPENAI_API_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Binance API configuration
BINANCE_API_URL = "https://api.binance.com/api/v3/klines"
EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"

# News sources
NEWS_SOURCES = [
    "https://min-api.cryptocompare.com/data/v2/news/?lang=EN",
    "https://api.cryptopanic.com/v1/posts/?auth_token={}&public=true"
]

async def make_api_call(url, params=None, max_retries=3):
    async with aiohttp.ClientSession() as session:
        for attempt in range(max_retries):
            try:
                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 429:
                        wait_time = 2 ** attempt
                        logger.warning(f"Rate limit exceeded. Waiting {wait_time} seconds.")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    response.raise_for_status()
                    return await response.json()
                    
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"API request failed (attempt {attempt+1}): {e}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(1)
                
        raise Exception("Max retries exceeded")

async def get_exchange_info():
    """Get all trading pairs from Binance"""
    try:
        data = await make_api_call(EXCHANGE_INFO_URL)
        return {symbol['symbol'] for symbol in data['symbols']}
    except Exception as e:
        logger.error(f"Error getting exchange info: {e}")
        return set()

async def normalize_symbol(symbol):
    """Normalize symbol to Binance format"""
    symbol = symbol.upper().replace('/', '').replace('-', '')
    
    # Check USDT pairs first
    usdt_pair = f"{symbol}USDT"
    btc_pair = f"{symbol}BTC"
    
    # Cache exchange info
    if not hasattr(normalize_symbol, 'trading_pairs'):
        normalize_symbol.trading_pairs = await get_exchange_info()
    
    if usdt_pair in normalize_symbol.trading_pairs:
        return usdt_pair
    elif btc_pair in normalize_symbol.trading_pairs:
        return btc_pair
    return None

async def get_coin_id_and_name(input_str):
    input_lower = input_str.lower()
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{input_lower}"
        data = await make_api_call(url)
        if data:
            return data['id'], data['symbol'].upper(), data['name']
    except Exception:
        pass  # Not a valid coin_id

    try:
        search_url = "https://api.coingecko.com/api/v3/search"
        params = {"query": input_str}
        search_data = await make_api_call(search_url, params)
        coins = search_data.get('coins', [])
        if not coins:
            return None, [], None
            
        symbol_upper = input_str.upper()
        exact_matches = [coin for coin in coins if coin['symbol'].upper() == symbol_upper]
        if exact_matches:
            coin = exact_matches[0]
            return coin['id'], coin['symbol'].upper(), coin['name']
        else:
            suggestions = [{
                'id': coin['id'], 
                'symbol': coin['symbol'].upper(), 
                'name': coin['name']
            } for coin in coins[:5]]
            return None, suggestions, None
    except Exception as e:
        logger.error(f"Error searching for coin {input_str}: {e}")
        return None, [], None

async def get_klines(symbol, days=30):
    """Get OHLC data from Binance"""
    try:
        # Normalize symbol to Binance format
        normalized_symbol = await normalize_symbol(symbol)
        if not normalized_symbol:
            logger.error(f"No trading pair found for {symbol}")
            return []
            
        params = {
            'symbol': normalized_symbol,
            'interval': '1h',
            'limit': days * 24
        }
        
        data = await make_api_call(BINANCE_API_URL, params)
        klines = []
        for item in data:
            klines.append([
                int(item[0]),         # open_time
                float(item[1]),       # open
                float(item[2]),       # high
                float(item[3]),       # low
                float(item[4]),       # close
                float(item[5]),       # volume
                int(item[6]),         # close_time
                float(item[7]),       # quote_asset_volume
                int(item[8]),         # num_trades
                float(item[9]),       # taker_buy_base
                float(item[10]),      # taker_buy_quote
                0                    # ignore
            ])
        return klines
    except Exception as e:
        logger.error(f"Error fetching klines for {symbol}: {e}")
        return []

async def get_crypto_news(coin_name, symbol):
    news_items = []
    
    try:
        # Source 1: CryptoCompare
        data = await make_api_call(NEWS_SOURCES[0])
        items = data.get('Data', [])
        
        search_terms = [coin_name.lower(), symbol.lower()]
        for item in items:
            text = (item['title'] + " " + item['body']).lower()
            if any(term in text for term in search_terms):
                news_items.append({
                    'source': 'CryptoCompare',
                    'title': item['title'],
                    'url': item['url']
                })
                if len(news_items) >= 5:
                    break
    except Exception as e:
        logger.error(f"Error fetching CryptoCompare news: {e}")
    
    try:
        # Source 2: CryptoPanic
        cryptopanic_key = os.getenv("CRYPTOPANIC_API_KEY", "")
        url = NEWS_SOURCES[1].format(cryptopanic_key)
        data = await make_api_call(url)
        items = data.get('results', [])
        
        search_terms = [coin_name.lower(), symbol.lower()]
        for item in items:
            text = item['title'].lower()
            if any(term in text for term in search_terms):
                news_items.append({
                    'source': 'CryptoPanic',
                    'title': item['title'],
                    'url': item['url']
                })
                if len(news_items) >= 5:
                    break
    except Exception as e:
        logger.error(f"Error fetching CryptoPanic news: {e}")
    
    if news_items:
        news_text = "\n".join(
            [f"- [{item['title']}]({item['url']}) ({item['source']})" 
             for item in news_items[:5]]
        )
        return news_text
    
    return "Tidak ada berita terkini ditemukan."

async def get_top_cryptocurrencies(limit=20):
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": False
        }
        data = await make_api_call(url, params)
        return data
    except Exception as e:
        logger.error(f"Error fetching top cryptocurrencies: {e}")
        return []

async def search_cryptocurrencies(query, limit=10):
    try:
        url = "https://api.coingecko.com/api/v3/search"
        params = {"query": query}
        data = await make_api_call(url, params)
        results = []
        query_lower = query.lower()
        for coin in data.get('coins', []):
            if (query_lower in coin['symbol'].lower() or 
                query_lower in coin['name'].lower()):
                results.append({
                    'id': coin['id'],
                    'symbol': coin['symbol'].upper(),
                    'name': coin['name'],
                    'market_cap_rank': coin.get('market_cap_rank', 'N/A')
                })
                if len(results) >= limit:
                    break
        return results
    except Exception as e:
        logger.error(f"Error searching cryptocurrencies: {e}")
        return []

async def analyze_technical(symbol):
    klines = await get_klines(symbol)
    if not klines:
        logger.error(f"No data available for {symbol}")
        return None
        
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    
    # Convert to datetime and set timezone
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('timestamp')
    df = df.tz_convert('Asia/Jakarta')  # Adjust to your timezone
    
    # Ensure numeric types
    numeric_cols = ["open", "high", "low", "close", "volume"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
    df = df.dropna()
    
    if len(df) < 50:
        logger.error(f"Insufficient data for {symbol}: {len(df)} points")
        return None
        
    try:
        # Technical indicators
        rsi = RSIIndicator(df["close"]).rsi().iloc[-1]
        ema20 = EMAIndicator(df["close"], window=20).ema_indicator().iloc[-1]
        ema50 = EMAIndicator(df["close"], window=50).ema_indicator().iloc[-1]
        macd = MACD(df["close"]).macd().iloc[-1]
        price = df["close"].iloc[-1]
        
        # Volume analysis
        current_volume = df["volume"].iloc[-1]
        previous_volume = df["volume"].iloc[-2]
        volume_change_1h = ((current_volume - previous_volume) / previous_volume) * 100 if previous_volume > 0 else 0
        
        # 24-hour volume change
        if len(df) > 24:
            vol_24h_ago = df["volume"].iloc[-25]
            volume_change_24h = ((current_volume - vol_24h_ago) / vol_24h_ago) * 100 if vol_24h_ago > 0 else 0
        else:
            volume_change_24h = 0
            
        return {
            "price": price,
            "rsi": round(rsi, 2) if not pd.isna(rsi) else 50,
            "ema20": round(ema20, 2) if not pd.isna(ema20) else price,
            "ema50": round(ema50, 2) if not pd.isna(ema50) else price,
            "macd": round(macd, 4) if not pd.isna(macd) else 0,
            "volume_change_1h": round(volume_change_1h, 2),
            "volume_change_24h": round(volume_change_24h, 2)
        }
    except Exception as e:
        logger.error(f"Error calculating indicators for {symbol}: {e}")
        return None

def build_prompt(symbol, data, btc_data, news):
    """Sanitize inputs to prevent prompt injection"""
    safe_symbol = escape(symbol)
    safe_news = escape(news)
    
    return f"""
Simbol: {safe_symbol}
Harga saat ini: ${data['price']}
RSI: {data['rsi']}
EMA 20: {data['ema20']}
EMA 50: {data['ema50']}
MACD: {data['macd']}
Volume Change (1h): {data['volume_change_1h']}%
Volume Change (24h): {data['volume_change_24h']}%

Referensi BTC:
Harga BTC: ${btc_data['price']}
RSI BTC: {btc_data['rsi']}

Berita terbaru:
{safe_news}

Tugas:
1. Prediksi apakah harga {safe_symbol} akan naik atau turun dalam 6 jam ke depan.
2. Tentukan support, resistance, entry point, TP dan SL.
3. Gunakan indikator di atas + berita untuk menjelaskan alasannya.

Jawaban dalam format berikut:
Prediksi: Naik/Turun
Support:
Resistance:
Entry:
Take Profit:
Stop Loss:
Penjelasan:
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Selamat datang di Bot Prediksi Kripto AI v4!\n\n"
        "🌍 **Mendukung SEMUA cryptocurrency di Binance!**\n\n"
        "**Pembaruan Utama:**\n"
        "• Sumber data Binance langsung\n"
        "• Analisis volume 1h & 24h\n"
        "• Multi-sumber berita kripto\n"
        "• Perlindungan keamanan ditingkatkan\n\n"
        "**Cara penggunaan:**\n"
        "• Ketik simbol kripto langsung (contoh: BTC, ETH, SOL)\n"
        "• Gunakan /top untuk melihat top 20 cryptocurrency\n"
        "• Gunakan /search <query> untuk mencari kripto\n"
        "• Gunakan /help untuk panduan lengkap"
    )

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Mengambil data top cryptocurrency...")
    try:
        top_coins = await get_top_cryptocurrencies(20)
        if not top_coins:
            await update.message.reply_text("❌ Gagal mengambil data cryptocurrency.")
            return
        message = "🏆 **Top 20 Cryptocurrency (Market Cap)**\n\n"
        for i, coin in enumerate(top_coins, 1):
            symbol = coin['symbol'].upper()
            name = coin['name']
            price = coin['current_price']
            change_24h = coin['price_change_percentage_24h']
            emoji = "🟢" if change_24h >= 0 else "🔴"
            message += f"{i:2d}. **{symbol}** ({name})\n"
            message += f"    💵 ${price:,.4f} {emoji} {change_24h:+.2f}%\n\n"
        message += "💡 Ketik simbol untuk menganalisis (contoh: BTC, ETH, SOL)"
        await update.message.reply_text(message, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in top command: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat mengambil data top cryptocurrency.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 **BOT PREDIKSI KRIPTO AI v4 - PANDUAN LENGKAP**

**PEMBARUAN UTAMA:**
✅ Sumber data Binance langsung
✅ Analisis volume 1h & 24h
✅ Multi-sumber berita (CryptoCompare + CryptoPanic)
✅ Perlindungan keamanan ditingkatkan

**PERINTAH:**
/start - Memulai bot
/help - Panduan ini
/top - Top 20 cryptocurrency
/search <query> - Cari cryptocurrency

**CARA ANALISIS:**
Ketik simbol cryptocurrency (BTC, ETH, dll)

**TEKNOLOGI:**
• Binance API (OHLC data)
• CoinGecko API (market data)
• GPT-4 (analisis AI)
• Technical Analysis Library
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not any(arg.strip() for arg in context.args):
        await update.message.reply_text(
            "🔍 **Cara penggunaan:**\n"
            "/search <query>\n\n"
            "**Contoh:**\n"
            "/search bitcoin\n"
            "/search doge"
        )
        return
    query = " ".join(context.args).strip()
    await update.message.reply_text(f"🔍 Mencari cryptocurrency: {query}")
    try:
        results = await search_cryptocurrencies(query, 10)
        if not results:
            await update.message.reply_text(
                f"❌ Tidak ditemukan cryptocurrency dengan query: {query}"
            )
            return
        message = f"🔍 **Hasil pencarian: {query}**\n\n"
        for i, coin in enumerate(results, 1):
            symbol = coin.get('symbol', '-')
            name = coin.get('name', '-')
            rank = coin.get('market_cap_rank', 'N/A')
            message += f"{i}. **{symbol}** - {name}\n"
            message += f"   📊 Rank: #{rank}\n\n"
        message += "💡 Ketik simbol untuk menganalisis (contoh: BTC, ETH, SOL)"
        await update.message.reply_text(message, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await update.message.reply_text(
            "❌ Terjadi kesalahan saat mencari cryptocurrency."
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_str = update.message.text.strip()
    await update.message.reply_text("🔍 Mencari cryptocurrency...")
    coin_id, symbol, result = await get_coin_id_and_name(input_str)
    
    if coin_id:
        coin_name = result
        await update.message.reply_text(f"📊 Menganalisis {coin_name} ({symbol})...")
        try:
            data = await analyze_technical(symbol)
            if data is None:
                await update.message.reply_text(f"❌ Tidak dapat menganalisis {coin_name}.")
                return
                
            btc_data = await analyze_technical("BTC") or {"price": 0, "rsi": 50}
            news = await get_crypto_news(coin_name, symbol)
            
            prompt = build_prompt(symbol, data, btc_data, news)
            
            client = openai.OpenAI(api_key=openai.api_key)
            res = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "Kamu adalah analis teknikal dan fundamental kripto profesional."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500
            )
            result = res.choices[0].message.content.strip()
            await update.message.reply_text(result, parse_mode='Markdown')
            
        except openai.RateLimitError:
            await update.message.reply_text("⚠️ Limit OpenAI tercapai. Silakan coba lagi nanti.")
        except openai.APIConnectionError:
            await update.message.reply_text("🔌 Gagal terhubung ke OpenAI. Cek koneksi internet Anda.")
        except Exception as e:
            logger.error(f"Error in handle_message: {str(e)}", exc_info=True)
            await update.message.reply_text(f"❌ Terjadi kesalahan: {str(e)}")
    else:
        suggestions = result
        if suggestions:
            message = "❌ Symbol tidak ditemukan. Mungkin Anda maksud:\n"
            for i, coin in enumerate(suggestions, 1):
                message += f"{i}. {coin['symbol']} - {coin['name']}\n"
            message += "\nSilakan ketik simbol yang tepat."
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(
                "❌ Tidak ditemukan cryptocurrency yang cocok.\n"
                "💡 Gunakan /search untuk mencari koin"
            )

def main():
    if not TELEGRAM_TOKEN:
        logger.error("❌ Error: TELEGRAM_TOKEN environment variable not set")
        return
    if not openai.api_key:
        logger.error("❌ Error: OPENAI_API_KEY environment variable not set")
        return
        
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("top", top_command))
        app.add_handler(CommandHandler("search", search_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("🚀 Bot siap dijalankan...")
        logger.info("✅ Menggunakan Binance API untuk OHLC data")
        logger.info("🤖 AI-powered analysis dengan GPT-4")
        logger.info("🌍 Mendukung SEMUA cryptocurrency di Binance!")
        
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ Error starting bot: {e}")

if __name__ == "__main__":
    main()
