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

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("⚽️ **Футбольный Бункер запущен!**\nДобавь бота в чат и напиши `/game` для старта.")

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    await db.create_lobby(message.chat.id, message.from_user.id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="🚀 Начать игру", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 **Идет сбор в Футбольный Бункер!**\n\nОрганизатор: @{message.from_user.username}\nИгроков: 0\n\n*Для соло-теста напиши `/test_fill`*",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

# Команда для соло-тестирования
@dp.message(Command("test_fill"))
async def cmd_test_fill(message: types.Message):
    chat_id = message.chat.id
    fake_bots = [
        (101, "Bot_Messi"),
        (102, "Bot_Ronaldo"),
        (103, "Bot_Pedri")
    ]
    for b_id, b_name in fake_bots:
        pack = cards.generate_player_pack()
        await db.add_player(chat_id, b_id, b_name, pack)
    
    await message.answer("🤖 Добавлено 3 тестовых бота! Жми «Начать игру».")

@dp.callback_query(F.data == "join_game")
async def join_game(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    
    pack = cards.generate_player_pack()
    await db.add_player(chat_id, user.id, user.username or user.first_name, pack)
    
    players = await db.get_players(chat_id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="🚀 Начать игру", callback_data="start_game")
    builder.adjust(1)

    player_list = "\n".join([f"• @{p[0]}" for p in players])
    await callback.message.edit_text(
        f"🎮 **Идет сбор в Футбольный Бункер!**\n\nУчастники ({len(players)}):\n{player_list}",
        reply_markup=builder.as_markup()
    )
    # Гасим индикатор загрузки в Telegram
    await callback.answer("Ты успешно вступил в игру!")

@dp.callback_query(F.data == "start_game")
async def start_game(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    players = await db.get_players(chat_id)
    
    if len(players) < 1:
        await callback.answer("Недостаточно игроков!", show_alert=True)
        return

    # Отвечаем на callback, чтобы кнопка перестала крутиться
    await callback.answer("Игра начинается!")
    
    # Рассылаем карточки реальным людям в ЛС
    for p_name, p_id in players:
        # Пропускаем фейковых тестовых ботов
        if p_id < 1000:
            continue
            
        try:
            # Получаем нагенерированный пак карт из БД
            async with db.aiosqlite.connect(db.DB_NAME) as conn:
                async with conn.execute("SELECT position, health, skill, inventory, secret FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, p_id)) as cursor:
                    card = await cursor.fetchone()
                    
            text_card = (
                f"🎴 **ТВОЯ КАРТОЧКА ИГРОКА**\n\n"
                f"⚽️ Позиция:\n"
                f"🏥 Физуха/Здоровье:\n"
                f"🎯 Навык:\n"
                f"🎒 Багаж:\n"
                f"🤫 Секрет:\n\n"
                f"Никому не показывай весь пак! Вскрывай характеристики по команде бота в чате."
            )
            await bot.send_message(p_id, text_card, parse_mode="Markdown")
        except Exception:
            # Если юзер не нажал /start в ЛС у бота
            await callback.message.answer(f"⚠️ @{p_name}, напиши боту в личку /start, чтобы получить карты!")

    await callback.message.edit_text(f"🏁 **ИГРА НАЧАЛАСЬ!**\n\nВсего игроков на поле: {len(players)}.\nПроверьте личные сообщения от бота.")

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
