"""
=============================================================
  КРТ-МОНИТОР — Telegram Bot
  Источники: torgi.gov.ru · zakupki.gov.ru · Google News
=============================================================

БЫСТРЫЙ СТАРТ:
1. pip install python-telegram-bot feedparser apscheduler aiohttp
2. Вставь TOKEN, CHANNEL_ID
3. python news_bot.py

КАК ДОБАВИТЬ БОТА В КАНАЛ:
- Зайди в настройки канала → Администраторы
- Добавь своего бота как администратора
- Дай права "Публикация сообщений"

=============================================================
"""

import asyncio
import logging
import urllib.parse
import json
from datetime import datetime, timezone

import aiohttp
import feedparser
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# =============================================================
# ⚙️  НАСТРОЙКИ
# =============================================================

import os
TOKEN      = os.environ.get("TOKEN", "ВСТАВЬ_ТОКЕН_БОТА")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "ВСТАВЬ_ID_КАНАЛА")  # например: @my_channel или -1001234567890

# Как часто проверять (в минутах)
CHECK_INTERVAL_MINUTES = 60

# =============================================================
# 🔑  КЛЮЧЕВЫЕ СЛОВА
# =============================================================

KEYWORDS = [
    "КРТ",
    "Проект КРТ",
    "Комплексное развитие территорий",
    "Комплексного развития территорий",
    "Комплексного развития территории",
    "Масштабный инвестиционный проект",
    "МИП",
    "Сделка по продаже актива",
    "Девелопер купил",
    "Девелопер продал",
    "редевелопмент промзоны",
    "промышленная зона продажа",
    "земельный участок торги",
    "аукцион земля девелопмент",
]

# =============================================================
# 💾  ДЕДУПЛИКАЦИЯ (хранит ID уже отправленных записей)
# =============================================================

seen_ids: set = set()

# =============================================================
# 🏛️  ИСТОЧНИК 1: torgi.gov.ru API
# =============================================================

TORGI_API = "https://torgi.gov.ru/new/api/lotcards/search"

async def fetch_torgi(session: aiohttp.ClientSession, keyword: str) -> list:
    """Ищет лоты на torgi.gov.ru по ключевому слову"""
    results = []
    try:
        params = {
            "text": keyword,
            "page": 0,
            "size": 20,
            "sortField": "firstVersionPublicationDate",
            "sortAsc": "false",
        }
        async with session.get(TORGI_API, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            lots = data.get("content", [])
            for lot in lots:
                lot_id = str(lot.get("id", ""))
                if not lot_id or lot_id in seen_ids:
                    continue

                title    = lot.get("lotName", "") or lot.get("biddingObjectInfo", "")
                notice   = lot.get("noticeNumber", "")
                region   = lot.get("subjectRFCode", {})
                if isinstance(region, dict):
                    region = region.get("name", "")
                pub_date = lot.get("firstVersionPublicationDate", "")[:10] if lot.get("firstVersionPublicationDate") else ""
                price    = lot.get("priceMin", "")
                link     = f"https://torgi.gov.ru/new/public/lots/lot/{lot_id}"

                # Проверяем совпадение ключевых слов
                search_text = (title or "").lower()
                if not any(kw.lower() in search_text for kw in KEYWORDS):
                    # Если не совпало по названию — пропускаем (уже отфильтровано API)
                    # но добавляем если искали конкретным словом
                    pass

                seen_ids.add(lot_id)
                price_str = f"\n💰 Начальная цена: *{int(float(price)):,} ₽*".replace(",", " ") if price else ""
                results.append({
                    "uid":    lot_id,
                    "source": "🏛 torgi.gov.ru",
                    "title":  title or f"Лот №{notice}",
                    "region": region,
                    "date":   pub_date,
                    "price":  price_str,
                    "link":   link,
                    "keyword": keyword,
                })
    except Exception as e:
        logging.warning(f"torgi.gov.ru ошибка [{keyword}]: {e}")
    return results


async def fetch_all_torgi() -> list:
    """Запрашивает torgi.gov.ru по всем ключевым словам параллельно"""
    results = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_torgi(session, kw) for kw in KEYWORDS]
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        for batch in batches:
            if isinstance(batch, list):
                results.extend(batch)

    # Убираем дубли по uid
    seen = set()
    unique = []
    for item in results:
        if item["uid"] not in seen:
            seen.add(item["uid"])
            unique.append(item)
    return unique

# =============================================================
# 📋  ИСТОЧНИК 2: zakupki.gov.ru RSS
# =============================================================

def fetch_zakupki() -> list:
    """Парсит RSS закупок по ключевым словам"""
    results = []
    for kw in KEYWORDS[:6]:  # берём первые 6 самых важных
        try:
            encoded = urllib.parse.quote(kw)
            url = f"https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString={encoded}&morphology=on&fz44=on&fz223=on"
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                uid  = entry.get("link", "") or entry.get("id", "")
                if not uid or uid in seen_ids:
                    continue
                title = entry.get("title", "")
                date  = entry.get("published", "")[:10] if entry.get("published") else ""
                seen_ids.add(uid)
                results.append({
                    "uid":    uid,
                    "source": "📋 zakupki.gov.ru",
                    "title":  title,
                    "region": "",
                    "date":   date,
                    "price":  "",
                    "link":   uid,
                    "keyword": kw,
                })
        except Exception as e:
            logging.warning(f"zakupki.gov.ru ошибка [{kw}]: {e}")
    return results

# =============================================================
# 📰  ИСТОЧНИК 3: Google News RSS
# =============================================================

def fetch_google_news() -> list:
    """Парсит Google News по ключевым словам"""
    results = []
    for kw in KEYWORDS:
        try:
            encoded = urllib.parse.quote(kw)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=ru&gl=RU&ceid=RU:ru"
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                uid  = entry.get("link", "")
                if not uid or uid in seen_ids:
                    continue
                title = entry.get("title", "")
                date  = entry.get("published", "")[:16] if entry.get("published") else ""
                seen_ids.add(uid)
                results.append({
                    "uid":    uid,
                    "source": "📰 Google News",
                    "title":  title,
                    "region": "",
                    "date":   date,
                    "price":  "",
                    "link":   uid,
                    "keyword": kw,
                })
        except Exception as e:
            logging.warning(f"Google News ошибка [{kw}]: {e}")
    return results

# =============================================================
# 📤  ФОРМАТИРОВАНИЕ И ОТПРАВКА
# =============================================================

def format_item(item: dict) -> str:
    region_str = f"\n📍 {item['region']}" if item["region"] else ""
    date_str   = f"\n📅 {item['date']}"   if item["date"]   else ""
    kw_str     = f"\n🔑 _{item['keyword']}_"

    # Экранируем спецсимволы MarkdownV2
    title = (item["title"]
             .replace("&", "&amp;")
             .replace("<", "")
             .replace(">", ""))

    return (
        f"{item['source']}{region_str}{date_str}\n"
        f"*{title}*"
        f"{item.get('price', '')}"
        f"{kw_str}\n"
        f"[Открыть →]({item['link']})"
    )


async def send_items(app: Application, items: list):
    """Отправляет каждый найденный элемент отдельным сообщением"""
    for item in items[:20]:  # максимум 20 за один прогон
        try:
            text = format_item(item)
            await app.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            await asyncio.sleep(1)  # пауза между сообщениями
        except Exception as e:
            logging.error(f"Ошибка отправки: {e}\nТекст: {item['title']}")

# =============================================================
# ⏰  ОСНОВНОЙ ЦИКЛ МОНИТОРИНГА
# =============================================================

async def run_monitor(app: Application):
    """Запускается каждые CHECK_INTERVAL_MINUTES минут"""
    logging.info("🔍 Запуск мониторинга...")

    # Собираем данные из всех источников
    torgi_items   = await fetch_all_torgi()
    zakupki_items = fetch_zakupki()
    news_items    = fetch_google_news()

    all_items = torgi_items + zakupki_items + news_items

    if all_items:
        logging.info(f"✅ Найдено новых записей: {len(all_items)}")
        await send_items(app, all_items)
    else:
        logging.info("ℹ️ Новых записей нет")

# =============================================================
# 🤖  КОМАНДЫ БОТА
# =============================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *КРТ-Монитор запущен*\n\n"
        "Слежу за:\n"
        "• 🏛 torgi.gov.ru\n"
        "• 📋 zakupki.gov.ru\n"
        "• 📰 Google News\n\n"
        f"Проверка каждые *{CHECK_INTERVAL_MINUTES} мин*\n\n"
        "Команды:\n"
        "/check — проверить прямо сейчас\n"
        "/keywords — список ключевых слов\n"
        "/addkw слово — добавить\n"
        "/delkw слово — удалить\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Запускаю проверку...")
    await run_monitor(ctx.application)
    await update.message.reply_text("✅ Готово. Новые записи отправлены в канал.")


async def cmd_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kw_list = "\n".join([f"{i+1}. {kw}" for i, kw in enumerate(KEYWORDS)])
    await update.message.reply_text(
        f"🔑 *Ключевые слова ({len(KEYWORDS)}):\n*{kw_list}",
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_add_keyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /addkw <слово или фраза>")
        return
    kw = " ".join(ctx.args)
    if kw not in KEYWORDS:
        KEYWORDS.append(kw)
        await update.message.reply_text(f"✅ Добавлено: *{kw}*", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"Уже есть: {kw}")


async def cmd_del_keyword(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /delkw <слово или фраза>")
        return
    kw = " ".join(ctx.args)
    if kw in KEYWORDS:
        KEYWORDS.remove(kw)
        await update.message.reply_text(f"🗑 Удалено: *{kw}*", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"Не найдено: {kw}")

# =============================================================
# 🚀  ЗАПУСК
# =============================================================

async def post_init(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_monitor,
        trigger="interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[app],
        id="monitor",
    )
    scheduler.start()
    logging.info(f"✅ Мониторинг запущен. Интервал: {CHECK_INTERVAL_MINUTES} мин.")
    logging.info(f"📋 Ключевых слов: {len(KEYWORDS)}")


def main():
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(message)s",
        level=logging.INFO
    )

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("check",    cmd_check))
    app.add_handler(CommandHandler("keywords", cmd_keywords))
    app.add_handler(CommandHandler("addkw",    cmd_add_keyword))
    app.add_handler(CommandHandler("delkw",    cmd_del_keyword))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
