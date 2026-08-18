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
    await message.answer("⚽️ **Футбольный Бункер запущен!**\nДобавь бота в групповой чат и напиши `/game` для старта сбора.")

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    await db.create_lobby(message.chat.id, message.from_user.id)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="🚀 Начать игру (Только Хост)", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 **Идет сбор в Футбольный Бункер!**\n\nОрганизатор: @{message.from_user.username}\nИгроков: 0",
        reply_markup=builder.as_markup()
    )

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
    await callback.answer("Ты вступил в игру! Карточки выданы.")

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
