import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

import database as db
import cards

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def mention_user(user_id: int, name: str, username: str = None) -> str:
    if username:
        return f"@{username}"
    return f"<a href='tg://user?id={user_id}'>{name}</a>"

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("⚽️ <b>Футбольный Бункер запущен!</b>\nДобавь бота в чат и напиши <code>/game</code> для старта.", parse_mode="HTML")

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    await db.create_lobby(message.chat.id, message.from_user.id)
    bot_info = await bot.get_me()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="📩 Открыть ЛС с ботом", url=f"https://t.me/{bot_info.username}?start=game")
    builder.button(text="🚀 Начать игру", callback_data="start_game")
    builder.adjust(1)

    user_mention = mention_user(message.from_user.id, message.from_user.first_name, message.from_user.username)

    await message.answer(
        f"🎮 <b>Идет сбор в Футбольный Бункер!</b>\n\n"
        f"Организатор: {user_mention}\n"
        f"Участников: 0\n\n"
        f"🤖 <i>Для соло-теста напиши <code>/test_fill</code></i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.message(Command("test_fill"))
async def cmd_test_fill(message: types.Message):
    chat_id = message.chat.id
    user = message.from_user
    
    # Добавляем реального юзера
    user_pack = cards.generate_player_pack()
    await db.add_player(chat_id, user.id, user.first_name, user_pack)
    
    # Добавляем фейковых ботов
    fake_bots = [
        (101, "Bot_Messi"),
        (102, "Bot_Ronaldo"),
        (103, "Bot_Pedri")
    ]
    for b_id, b_name in fake_bots:
        pack = cards.generate_player_pack()
        await db.add_player(chat_id, b_id, b_name, pack)
    
    players = await db.get_players(chat_id)
    player_list = "\n".join([f"• {p[0]}" for p in players])
    
    bot_info = await bot.get_me()
    builder = InlineKeyboardBuilder()
    builder.button(text="📩 Открыть ЛС с ботом", url=f"https://t.me/{bot_info.username}?start=game")
    builder.button(text="🚀 Начать игру", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🤖 <b>Тестовый режим включен!</b>\n\nУчастники ({len(players)}):\n{player_list}\n\nЖми «Начать игру»!",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "join_game")
async def join_game(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    
    pack = cards.generate_player_pack()
    await db.add_player(chat_id, user.id, user.first_name, pack)
    
    players = await db.get_players(chat_id)
    bot_info = await bot.get_me()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="📩 Открыть ЛС с ботом", url=f"https://t.me/{bot_info.username}?start=game")
    builder.button(text="🚀 Начать игру", callback_data="start_game")
    builder.adjust(1)

    player_list = "\n".join([f"• {p[0]}" for p in players])
    await callback.message.edit_text(
        f"🎮 <b>Идет сбор в Футбольный Бункер!</b>\n\nУчастники ({len(players)}):\n{player_list}",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer("Ты вступил в игру!")

@dp.callback_query(F.data == "start_game")
async def start_game(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    players = await db.get_players(chat_id)
    
    if len(players) < 1:
        await callback.answer("Недостаточно игроков!", show_alert=True)
        return

    await callback.answer("Игра начинается!")
    bot_info = await bot.get_me()
    
    for p_name, p_id in players:
        # Пропускаем фейковых ботов
        if p_id < 1000:
            continue
            
        try:
            card = await db.get_player_card(chat_id, p_id)
            if not card:
                continue

            text_card = (
                f"🎴 <b>ТВОЯ КАРТОЧКА ИГРОКА</b>\n\n"
                f"⚽️ <b>Позиция:</b> {card[0]}\n"
                f"🏥 <b>Физуха/Здоровье:</b> {card[1]}\n"
                f"🎯 <b>Навык:</b> {card[2]}\n"
                f"🎒 <b>Багаж:</b> {card[3]}\n"
                f"🤫 <b>Секрет:</b> {card[4]}\n\n"
                f"<i>Никому не показывай весь пак! Вскрывай характеристики по команде бота в чате.</i>"
            )
            await bot.send_message(p_id, text_card, parse_mode="HTML")
        except Exception as e:
            print(f"Ошибка отправки игроку {p_id}: {e}")
            user_link = f"<a href='tg://user?id={p_id}'>{p_name}</a>"
            builder = InlineKeyboardBuilder()
            builder.button(text="📩 Перейти в ЛС", url=f"https://t.me/{bot_info.username}?start=game")
            await callback.message.answer(
                f"⚠️ {user_link}, не удалось прислать карту. Проверь ЛС с ботом!",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )

    await callback.message.edit_text(
        f"🏁 <b>ИГРА НАЧАЛАСЬ!</b>\n\n"
        f"Всего игроков на поле: {len(players)}.\n"
        f"Карты отправлены в ЛС.",
        parse_mode="HTML"
    )

async def handle_ping(request):
    return web.Response(text="Bot Alive")

async def main():
    await db.init_db()
    
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
