"""
=============================================================
  TELEGRAM NEWS BOT — парсинг новостей по ключевым словам
=============================================================

БЫСТРЫЙ СТАРТ:
1. pip install python-telegram-bot feedparser apscheduler
2. Вставь TOKEN от @BotFather
3. Вставь CHAT_ID (получи через @userinfobot)
4. Добавь свои ключевые слова в KEYWORDS
5. python news_bot.py

=============================================================
"""

import asyncio
import logging
import urllib.parse
from datetime import datetime

import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# =============================================================
# ⚙️  НАСТРОЙКИ — редактируй здесь
# =============================================================

TOKEN = "8326800586:AAECnNqoc97CBzHraS7u1eHLaTAbDe6EkqY"       # от @BotFather
CHAT_ID = "8326800586"        # от @userinfobot или просто напиши боту /chatid

# Ключевые слова — добавляй/удаляй любые
KEYWORDS = [
    "КРТ",
    "Проект КРТ",
    "Комплексное развитие территорий",
    "Масштабный инвестиционный проект",
    "Сделка по продаже актива",
    "Девелопер купил",
    "Девелопер продал",
]

# Как часто проверять новости (в часах)
CHECK_INTERVAL_HOURS = 1

# Сколько новостей максимум в одном дайджесте
MAX_NEWS_PER_DIGEST = 10

# =============================================================
# 📡  ИСТОЧНИКИ НОВОСТЕЙ
# =============================================================

def build_rss_feeds(keywords: list) -> list:
    """Генерирует Google News RSS для каждого ключевого слова + фиксированные источники"""

    feeds = []

    # Google News RSS по каждому ключевому слову (самый мощный источник)
    for kw in keywords:
        encoded = urllib.parse.quote(kw)
        feeds.append({
            "name": f"Google News: {kw}",
            "url": f"https://news.google.com/rss/search?q={encoded}&hl=ru&gl=RU&ceid=RU:ru"
        })

    # Фиксированные RSS российских деловых изданий
    fixed_feeds = [
        {"name": "РБК Недвижимость",  "url": "https://realty.rbc.ru/rss/"},
        {"name": "Ведомости",          "url": "https://www.vedomosti.ru/rss/news"},
        {"name": "Коммерсант",         "url": "https://www.kommersant.ru/RSS/news.xml"},
        {"name": "Forbes RU",          "url": "https://www.forbes.ru/rss"},
        {"name": "РБК",                "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"},
    ]
    feeds.extend(fixed_feeds)

    return feeds

# =============================================================
# 🔍  ЛОГИКА ПАРСИНГА
# =============================================================

seen_urls: set = set()  # дедупликация — не показываем одно и то же дважды

def fetch_news(keywords: list) -> list:
    """Парсит все источники, фильтрует по ключевым словам"""
    results = []
    feeds = build_rss_feeds(keywords)

    for feed_info in feeds:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:20]:  # берём последние 20 из каждого источника
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")

                if not link or link in seen_urls:
                    continue

                # Проверяем вхождение ключевых слов (регистронезависимо)
                combined_text = (title + " " + summary).lower()
                matched_kw = [kw for kw in keywords if kw.lower() in combined_text]

                if matched_kw:
                    seen_urls.add(link)
                    results.append({
                        "title": title,
                        "link": link,
                        "source": feed_info["name"],
                        "keywords": matched_kw,
                        "published": entry.get("published", ""),
                    })

        except Exception as e:
            logging.warning(f"Ошибка парсинга {feed_info['name']}: {e}")

    return results[:MAX_NEWS_PER_DIGEST]


def format_news(news_list: list) -> str:
    """Форматирует список новостей в текст для Telegram"""
    if not news_list:
        return "📭 Новых релевантных новостей нет."

    lines = [f"📰 *Дайджест новостей* — {datetime.now().strftime('%d.%m %H:%M')}\n"]

    for i, item in enumerate(news_list, 1):
        kw_str = ", ".join(item["keywords"][:2])  # показываем макс 2 совпавших слова
        lines.append(
            f"{i}\\. [{item['title']}]({item['link']})\n"
            f"   _📍 {item['source']} · 🔑 {kw_str}_\n"
        )

    return "\n".join(lines)

# =============================================================
# 🤖  КОМАНДЫ БОТА
# =============================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Бот для мониторинга новостей запущен*\n\n"
        "Команды:\n"
        "/news — получить новости прямо сейчас\n"
        "/keywords — показать текущие ключевые слова\n"
        "/addkw слово — добавить ключевое слово\n"
        "/delkw слово — удалить ключевое слово\n"
        "/chatid — узнать твой chat\\_id\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Ищу новости...")
    news = fetch_news(KEYWORDS)
    msg = format_news(news)
    await update.message.reply_text(msg, parse_mode="MarkdownV2", disable_web_page_preview=True)


async def cmd_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kw_list = "\n".join([f"• {kw}" for kw in KEYWORDS])
    await update.message.reply_text(f"🔑 *Текущие ключевые слова:*\n{kw_list}", parse_mode="Markdown")


async def cmd_add_keyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /addkw <слово или фраза>")
        return
    kw = " ".join(ctx.args)
    if kw not in KEYWORDS:
        KEYWORDS.append(kw)
        await update.message.reply_text(f"✅ Добавлено: *{kw}*\nВсего: {len(KEYWORDS)} слов", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Слово «{kw}» уже есть в списке.")


async def cmd_del_keyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /delkw <слово или фраза>")
        return
    kw = " ".join(ctx.args)
    if kw in KEYWORDS:
        KEYWORDS.remove(kw)
        await update.message.reply_text(f"🗑 Удалено: *{kw}*\nОсталось: {len(KEYWORDS)} слов", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Слово «{kw}» не найдено в списке.")


async def cmd_chatid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Твой chat_id: `{update.effective_chat.id}`", parse_mode="Markdown")


# =============================================================
# ⏰  АВТОДАЙДЖЕСТ ПО РАСПИСАНИЮ
# =============================================================

async def scheduled_digest(app: Application):
    """Вызывается автоматически каждые CHECK_INTERVAL_HOURS часов"""
    news = fetch_news(KEYWORDS)
    if news:  # отправляем только если есть что-то новое
        msg = format_news(news)
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=msg,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )

# =============================================================
# 🚀  ЗАПУСК
# =============================================================

def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )

    app = Application.builder().token(TOKEN).build()

    # Регистрируем команды
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("news",     cmd_news))
    app.add_handler(CommandHandler("keywords", cmd_keywords))
    app.add_handler(CommandHandler("addkw",    cmd_add_keyword))
    app.add_handler(CommandHandler("delkw",    cmd_del_keyword))
    app.add_handler(CommandHandler("chatid",   cmd_chatid))

    # Автодайджест по расписанию
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scheduled_digest,
        trigger="interval",
        hours=CHECK_INTERVAL_HOURS,
        args=[app],
        id="digest"
    )
    scheduler.start()

    print(f"✅ Бот запущен. Дайджест каждые {CHECK_INTERVAL_HOURS} ч.")
    print(f"📋 Ключевых слов: {len(KEYWORDS)}")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
