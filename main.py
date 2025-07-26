import requests, os
import openai
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

def get_klines(symbol="bitcoin", interval="1h", limit=100):
    # Convert symbol to CoinGecko format
    # First, try to map common USDT pairs
    symbol_mapping = {
        "BTCUSDT": "bitcoin",
        "ETHUSDT": "ethereum", 
        "BNBUSDT": "binancecoin",
        "ADAUSDT": "cardano",
        "SOLUSDT": "solana",
        "DOTUSDT": "polkadot",
        "AVAXUSDT": "avalanche-2",
        "MATICUSDT": "matic-network",
        "LINKUSDT": "chainlink",
        "UNIUSDT": "uniswap",
        "ATOMUSDT": "cosmos",
        "LTCUSDT": "litecoin",
        "XRPUSDT": "ripple",
        "BCHUSDT": "bitcoin-cash",
        "ETCUSDT": "ethereum-classic",
        "FILUSDT": "filecoin"
    }
    
    # Try to get coin_id from mapping first
    coin_id = symbol_mapping.get(symbol.upper())
    
    # If not found in mapping, try to search for the coin
    if not coin_id:
        # Remove USDT suffix if present
        clean_symbol = symbol.upper().replace('USDT', '').replace('USD', '')
        
        # Try to search for the coin
        try:
            search_url = "https://api.coingecko.com/api/v3/search"
            search_response = requests.get(search_url, timeout=10)
            search_response.raise_for_status()
            search_data = search_response.json()
            
            # Look for exact match first
            for coin in search_data.get('coins', []):
                if (coin['symbol'].upper() == clean_symbol or 
                    coin['id'].lower() == clean_symbol.lower() or
                    coin['name'].lower() == clean_symbol.lower()):
                    coin_id = coin['id']
                    break
            
            # If still not found, try partial match
            if not coin_id:
                for coin in search_data.get('coins', []):
                    if (clean_symbol.lower() in coin['symbol'].lower() or
                        clean_symbol.lower() in coin['name'].lower()):
                        coin_id = coin['id']
                        break
                        
        except Exception as e:
            logger.error(f"Error searching for coin {symbol}: {e}")
            return []
    
    # If still no coin_id found, try using the symbol directly
    if not coin_id:
        coin_id = symbol.lower()
    
    try:
        # Get current price and market data
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Get historical data for technical analysis
        days = 30  # Get 30 days of data for analysis
        hist_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        hist_params = {"vs_currency": "usd", "days": days}  # Removed interval parameter for free tier
        hist_response = requests.get(url=hist_url, params=hist_params, timeout=10)
        hist_response.raise_for_status()
        hist_data = hist_response.json()
        
        # Format data to match Binance structure
        prices = hist_data.get('prices', [])
        volumes = hist_data.get('total_volumes', [])
        
        if not prices:
            logger.error(f"No price data received for {symbol}")
            return []
        
        klines = []
        for i, (timestamp, price) in enumerate(prices):
            volume = volumes[i][1] if i < len(volumes) else 0
            # Create OHLC data (using close price as approximation)
            klines.append([
                timestamp,  # timestamp
                price,      # open
                price,      # high  
                price,      # low
                price,      # close
                volume,     # volume
                timestamp,  # close_time
                0,          # quote_asset_volume
                0,          # num_trades
                0,          # taker_buy_base
                0,          # taker_buy_quote
                0           # ignore
            ])
        
        return klines
        
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed for {symbol}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error processing data for {symbol}: {e}")
        return []

def get_crypto_news(currency="BTC"):
    # CoinGecko doesn't have a direct news API, so we'll use a general crypto news source
    # For now, we'll return a placeholder or use a free news API
    try:
        # Using CryptoCompare news API (free tier)
        url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('Data'):
            news_items = data['Data'][:5]  # Get top 5 news
            news_text = "\n".join([f"- {item['title']}" for item in news_items])
            return news_text
        else:
            return "Tidak ada berita penting saat ini."
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        return "Gagal mengambil berita. Menggunakan data teknikal saja."

def get_top_cryptocurrencies(limit=20):
    """Get top cryptocurrencies by market cap"""
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": False
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error fetching top cryptocurrencies: {e}")
        return []

def search_cryptocurrencies(query, limit=10):
    """Search for cryptocurrencies by name or symbol"""
    try:
        url = "https://api.coingecko.com/api/v3/search"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
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

def analyze_technical(symbol):
    klines = get_klines(symbol)
    
    if not klines:
        logger.error(f"No data available for {symbol}")
        return None
    
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    
    # Convert to numeric and handle errors
    df["close"] = pd.to_numeric(df["close"], errors='coerce')
    df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
    
    # Remove any NaN values
    df = df.dropna()
    
    if len(df) < 50:  # Need at least 50 data points for indicators
        logger.error(f"Insufficient data for {symbol}: {len(df)} points")
        return None

    try:
        # Technical indicators
        rsi = RSIIndicator(df["close"]).rsi().iloc[-1]
        ema20 = EMAIndicator(df["close"], window=20).ema_indicator().iloc[-1]
        ema50 = EMAIndicator(df["close"], window=50).ema_indicator().iloc[-1]
        macd = MACD(df["close"]).macd().iloc[-1]
        price = df["close"].iloc[-1]

        # Volume change with division by zero protection
        current_volume = df["volume"].iloc[-1]
        previous_volume = df["volume"].iloc[-2]
        
        if previous_volume > 0:
            volume_change = ((current_volume - previous_volume) / previous_volume) * 100
        else:
            volume_change = 0

        return {
            "price": price,
            "rsi": round(rsi, 2) if not pd.isna(rsi) else 50,
            "ema20": round(ema20, 2) if not pd.isna(ema20) else price,
            "ema50": round(ema50, 2) if not pd.isna(ema50) else price,
            "macd": round(macd, 4) if not pd.isna(macd) else 0,
            "volume_change": round(volume_change, 2)
        }
    except Exception as e:
        logger.error(f"Error calculating indicators for {symbol}: {e}")
        return None

def build_prompt(symbol, data, btc_data, news):
    return f"""
Simbol: {symbol}
Harga saat ini: ${data['price']}
RSI: {data['rsi']}
EMA 20: {data['ema20']}
EMA 50: {data['ema50']}
MACD: {data['macd']}
Volume Change (1h): {data['volume_change']}%

Referensi BTC:
Harga BTC: ${btc_data['price']}
RSI BTC: {btc_data['rsi']}

Berita terbaru:
{news}

Tugas:
1. Prediksi apakah harga {symbol} akan naik atau turun dalam 6 jam ke depan.
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
        "📊 Selamat datang di Bot Prediksi Kripto AI v3!\n\n"
        "🌍 **Mendukung SEMUA cryptocurrency di CoinGecko!**\n\n"
        "**Cara penggunaan:**\n"
        "• Ketik simbol kripto langsung (contoh: BTC, ETH, SOL)\n"
        "• Gunakan /top untuk melihat top 20 cryptocurrency\n"
        "• Gunakan /search <query> untuk mencari kripto\n"
        "• Gunakan /help untuk panduan lengkap\n\n"
        "**Contoh simbol populer:**\n"
        "• BTC, ETH, SOL, ADA, DOGE, SHIB\n"
        "• MATIC, LINK, UNI, ATOM, DOT\n"
        "• Dan ribuan cryptocurrency lainnya!\n\n"
        "✅ Menggunakan CoinGecko API (gratis)\n"
        "🤖 AI-powered analysis dengan GPT-4\n"
        "📈 Technical indicators: RSI, EMA, MACD\n"
        "🔍 Auto-search untuk semua cryptocurrency\n\n"
        "💡 Ketik /help untuk panduan lengkap!"
    )

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top cryptocurrencies by market cap"""
    await update.message.reply_text("📊 Mengambil data top cryptocurrency...")
    
    try:
        top_coins = get_top_cryptocurrencies(20)
        
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
    """Show comprehensive help information"""
    help_text = """
🤖 **BOT PREDIKSI KRIPTO AI v3 - PANDUAN LENGKAP**

📋 **DAFTAR PERINTAH:**

🔹 **Perintah Dasar:**
/start - Memulai bot dan melihat menu utama
/help - Menampilkan panduan lengkap ini
/top - Top 20 cryptocurrency berdasarkan market cap
/search <query> - Mencari cryptocurrency

🔹 **Analisis Langsung:**
Ketik simbol cryptocurrency langsung untuk menganalisis
Contoh: BTC, ETH, SOL, ADA, DOGE, SHIB

📊 **CARA MENGGUNAKAN:**

1️⃣ **Analisis Cryptocurrency:**
   • Ketik simbol: `BTC`, `ETH`, `SOL`
   • Bot akan menganalisis dan memberikan prediksi
   • Hasil: Support, Resistance, Entry, TP, SL

2️⃣ **Melihat Top Cryptocurrency:**
   • Ketik: `/top`
   • Menampilkan top 20 berdasarkan market cap
   • Pilih simbol untuk dianalisis

3️⃣ **Mencari Cryptocurrency:**
   • Ketik: `/search bitcoin`
   • Ketik: `/search doge`
   • Ketik: `/search shib`

🎯 **SIMBOL POPULER:**
• BTC (Bitcoin)
• ETH (Ethereum)
• SOL (Solana)
• ADA (Cardano)
• DOGE (Dogecoin)
• SHIB (Shiba Inu)
• MATIC (Polygon)
• LINK (Chainlink)
• UNI (Uniswap)
• ATOM (Cosmos)
• DOT (Polkadot)
• AVAX (Avalanche)

📈 **INDIKATOR TEKNIKAL:**
• RSI (Relative Strength Index)
• EMA 20 & 50 (Exponential Moving Average)
• MACD (Moving Average Convergence Divergence)
• Volume Analysis
• Price Change Analysis

🤖 **AI ANALYSIS:**
• Prediksi arah harga (6 jam ke depan)
• Support & Resistance levels
• Entry point, Take Profit, Stop Loss
• Analisis fundamental + teknikal
• Berita cryptocurrency terkini

⚠️ **PENTING:**
• Prediksi ini untuk tujuan informasi saja
• Selalu lakukan analisis sendiri sebelum investasi
• Tidak ada jaminan keakuratan prediksi
• Investasi cryptocurrency berisiko tinggi

🔧 **TROUBLESHOOTING:**
• Jika simbol tidak ditemukan, coba `/search`
• Jika error, coba lagi dalam beberapa menit
• Gunakan `/top` untuk melihat cryptocurrency populer

📞 **DUKUNGAN:**
Bot ini menggunakan:
• CoinGecko API (data cryptocurrency)
• OpenAI GPT-4 (analisis AI)
• Technical Analysis Library

🌍 **MENDUKUNG SEMUA CRYPTOCURRENCY DI COINGECKO!**
    """
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for cryptocurrencies"""
    if not context.args:
        await update.message.reply_text(
            "🔍 **Cara penggunaan:**\n"
            "/search <query>\n\n"
            "**Contoh:**\n"
            "/search bitcoin\n"
            "/search doge\n"
            "/search shib"
        )
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Mencari cryptocurrency: {query}")
    
    try:
        results = search_cryptocurrencies(query, 10)
        
        if not results:
            await update.message.reply_text(
                f"❌ Tidak ditemukan cryptocurrency dengan query: {query}\n\n"
                "💡 **Tips:**\n"
                "• Coba kata kunci yang berbeda\n"
                "• Gunakan /top untuk melihat cryptocurrency populer\n"
                "• Gunakan /help untuk panduan lengkap\n\n"
                "Contoh pencarian: bitcoin, ethereum, dogecoin, shiba"
            )
            return
        
        message = f"🔍 **Hasil pencarian: {query}**\n\n"
        
        for i, coin in enumerate(results, 1):
            symbol = coin['symbol']
            name = coin['name']
            rank = coin['market_cap_rank']
            
            message += f"{i}. **{symbol}** - {name}\n"
            message += f"   📊 Rank: #{rank}\n\n"
        
        message += "💡 Ketik simbol untuk menganalisis (contoh: BTC, ETH, SOL)"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await update.message.reply_text("❌ Terjadi kesalahan saat mencari cryptocurrency.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.upper().strip()
    await update.message.reply_text("🔍 Mengambil data dan menganalisis...")

    try:
        # Remove common suffixes for better matching
        clean_symbol = symbol.replace('USDT', '').replace('USD', '').replace('BTC', '').replace('ETH', '')
        
        # Check if symbol is too short
        if len(clean_symbol) < 2:
            await update.message.reply_text(
                "❌ Symbol terlalu pendek. Gunakan minimal 2 karakter.\n\n"
                "Contoh: BTC, ETH, SOL, ADA, DOGE, SHIB, etc."
            )
            return

        data = analyze_technical(symbol)
        if data is None:
            await update.message.reply_text(
                f"❌ Tidak dapat menganalisis {symbol}.\n\n"
                "Kemungkinan penyebab:\n"
                "• Symbol tidak ditemukan di CoinGecko\n"
                "• Data tidak cukup untuk analisis\n"
                "• Coba gunakan symbol yang berbeda\n\n"
                "💡 **Tips:**\n"
                "• Gunakan /search {symbol} untuk mencari\n"
                "• Gunakan /top untuk melihat cryptocurrency populer\n"
                "• Gunakan /help untuk panduan lengkap\n\n"
                "Contoh: BTC, ETH, SOL, ADA, DOGE, SHIB, MATIC, etc."
            )
            return
            
        btc_data = analyze_technical("BTCUSDT")
        if btc_data is None:
            btc_data = {"price": 0, "rsi": 50}  # Fallback values
            
        # Get news for the cryptocurrency
        news_symbol = symbol.replace('USDT', '').replace('USD', '')[:3]
        news = get_crypto_news(news_symbol)
        prompt = build_prompt(symbol, data, btc_data, news)

        # Use the new OpenAI API format
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
        await update.message.reply_text(result)
        
    except Exception as e:
        logger.error(f"Error in handle_message: {e}")
        await update.message.reply_text(f"❌ Terjadi kesalahan: {str(e)}")

def main():
    if not TELEGRAM_TOKEN:
        print("❌ Error: TELEGRAM_TOKEN environment variable not set")
        return
        
    if not openai.api_key:
        print("❌ Error: OPENAI_API_KEY environment variable not set")
        return
    
    try:
        app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("top", top_command))
        app.add_handler(CommandHandler("search", search_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        print("🚀 Bot siap dijalankan...")
        print("✅ Menggunakan CoinGecko API (gratis)")
        print("🤖 AI-powered analysis dengan GPT-4")
        print("🌍 Mendukung SEMUA cryptocurrency di CoinGecko!")
        print("📚 Help command tersedia: /help")
        app.run_polling()
    except Exception as e:
        print(f"❌ Error starting bot: {e}")

if __name__ == "__main__":
    main()
