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
    builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 <b>Идет сбор в Футбольный Бункер!</b>\n\n"
        f"Организатор: <a href='tg://user?id={message.from_user.id}'>{message.from_user.first_name}</a>\n"
        f"Участников: 0\n\n"
        f"🤖 <i>Для соло-теста напиши <code>/test_fill</code></i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.message(Command("test_fill"))
async def cmd_test_fill(message: types.Message):
    chat_id = message.chat.id
    user = message.from_user
    
    # Добавляем юзера + 2 ботов = 3 игрока
    await db.add_player(chat_id, user.id, user.first_name, cards.generate_player_pack())
    
    fake_bots = [(101, "Bot_Messi"), (102, "Bot_Ronaldo")]
    for b_id, b_name in fake_bots:
        await db.add_player(chat_id, b_id, b_name, cards.generate_player_pack())
    
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
    
    await db.add_player(chat_id, user.id, user.first_name, cards.generate_player_pack())
    players = await db.get_players(chat_id)
    bot_info = await bot.get_me()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="📩 Открыть ЛС с ботом", url=f"https://t.me/{bot_info.username}?start=game")
    builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
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
    
    # ПРОВЕРКА НА МИНИМУМ 3 ИГРОКА
    if len(players) < 3:
        return await callback.answer("⚠️ Для старта нужно минимум 3 игрока! Использовать /test_fill", show_alert=True)

    await callback.answer("Игра начинается!")
    
    # Генерируем сценарий
    scen = cards.generate_scenario(len(players))
    await db.update_lobby_scenario(chat_id, scen["text"], scen["winners_needed"])
    
    # Отправляем карты реальным игрокам в ЛС
    for p_name, p_id, _ in players:
        if p_id >= 1000:
            try:
                card = await db.get_player_card(chat_id, p_id)
                text_card = (
                    f"🎴 <b>ТВОЯ КАРТОЧКА ИГРОКА</b>\n\n"
                    f"⚽️ <b>Позиция:</b> {card[0]}\n"
                    f"🏥 <b>Физуха/Здоровье:</b> {card[1]}\n"
                    f"🎯 <b>Навык:</b> {card[2]}\n"
                    f"🎒 <b>Багаж:</b> {card[3]}\n"
                    f"🤫 <b>Секрет:</b> {card[4]}\n\n"
                    f"<i>Никому не показывай весь пак! Вскрывай характеристики в чате.</i>"
                )
                await bot.send_message(p_id, text_card, parse_mode="HTML")
            except Exception:
                pass

    builder = InlineKeyboardBuilder()
    builder.button(text="🎴 Вскрыть карту", callback_data="open_trait_menu")
    builder.button(text="🗳 Перейти к голосованию", callback_data="go_to_voting")
    builder.adjust(1)

    await callback.message.edit_text(
        f"{scen['text']}\n\n"
        f"🔥 <b>РАУНД 1: Вскрытие карт</b>\n"
        f"Каждый игрок должен вскрыть 1 характеристику и доказать свою пользу!",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "open_trait_menu")
async def open_trait_menu(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    
    card = await db.get_player_card(chat_id, user_id)
    if not card or card[10] == 0: # Не игрок или мертв
        return await callback.answer("Ты не участвуешь в игре или был изгнан!", show_alert=True)

    builder = InlineKeyboardBuilder()
    labels = [
        ("Позиция", "position", card[5]),
        ("Здоровье", "health", card[6]),
        ("Навык", "skill", card[7]),
        ("Багаж", "inventory", card[8]),
        ("Секрет", "secret", card[9])
    ]
    
    for name, code, is_rev in labels:
        if is_rev == 0:
            builder.button(text=f"Открыть {name}", callback_data=f"reveal_{code}")
            
    builder.adjust(2)
    
    if len(builder.as_markup().inline_keyboard) == 0:
        return await callback.answer("Ты уже вскрыл доступные карты!", show_alert=True)

    await callback.answer()
    await bot.send_message(user_id, "Выбери характеристику для вскрытия в общий чат:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("reveal_"))
async def reveal_chosen_trait(callback: types.CallbackQuery):
    trait_code = callback.data.split("_")[1]
    user = callback.from_user
    
    # Находим chat_id, где идет игра
    async with db.aiosqlite.connect(db.DB_NAME) as conn:
        async with conn.execute("SELECT chat_id FROM players WHERE user_id = ? AND is_alive = 1", (user.id,)) as cursor:
            row = await cursor.fetchone()
            
    if not row:
        return await callback.answer("Активная игра не найдена!")
        
    chat_id = row[0]
    await db.reveal_trait(chat_id, user.id, trait_code)
    card = await db.get_player_card(chat_id, user.id)
    
    val_map = {"position": (card[0], "⚽️ Позицию"), "health": (card[1], "🏥 Здоровье"), "skill": (card[2], "🎯 Навык"), "inventory": (card[3], "🎒 Багаж"), "secret": (card[4], "🤫 Секрет")}
    val, title = val_map[trait_code]

    await bot.send_message(chat_id, f"📢 <b>{user.first_name}</b> вскрывает <b>{title}</b>:\n👉 <i>{val}</i>", parse_mode="HTML")
    await callback.message.edit_text("Карта успешно вскрыта в общем чате!")

@dp.callback_query(F.data == "go_to_voting")
async def go_to_voting(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    lobby = await db.get_lobby(chat_id)
    
    if callback.from_user.id != lobby[1]:
        return await callback.answer("Только Организатор (Хост) может запускать голосование!", show_alert=True)

    await db.set_lobby_status(chat_id, "voting")
    await db.clear_votes(chat_id)
    
    alive_players = await db.get_alive_players(chat_id)
    builder = InlineKeyboardBuilder()
    
    for p_name, p_id in alive_players:
        builder.button(text=f"❌ Выгнать {p_name}", callback_data=f"vote_{p_id}")
    builder.button(text="📊 Подвести итоги", callback_data="finish_voting")
    builder.adjust(1)

    await callback.message.edit_text(
        f"🗳 <b>ГОЛОСОВАНИЕ (Раунд {lobby[4]})!</b>\n\n"
        f"Обсудите в чате, кто меньше всего подходит команде, и проголосуйте кнопками ниже:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    target_id = int(callback.data.split("_")[1])
    voter_id = callback.from_user.id
    
    await db.add_vote(chat_id, voter_id, target_id)
    await callback.answer("Твой голос принят!")

@dp.callback_query(F.data == "finish_voting")
async def finish_voting(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    lobby = await db.get_lobby(chat_id)
    
    if callback.from_user.id != lobby[1]:
        return await callback.answer("Только Организатор может подвести итоги!", show_alert=True)

    votes = await db.get_votes_count(chat_id)
    if not votes:
        return await callback.answer("Никто еще не проголосовал!", show_alert=True)

    kicked_id = votes[0][0]
    await db.eliminate_player(chat_id, kicked_id)
    
    async with db.aiosqlite.connect(db.DB_NAME) as conn:
        async with conn.execute("SELECT username FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, kicked_id)) as cursor:
            kicked_name = (await cursor.fetchone())[0]

    alive = await db.get_alive_players(chat_id)
    winners_needed = lobby[3]
    
    # ПРОВЕРКА НА ПОБЕДУ
    if len(alive) <= winners_needed:
        winners_str = "\n".join([f"🏆 <b>{p[0]}</b>" for p in alive])
        await callback.message.edit_text(
            f"❌ Большинством голосов из команды изгнан: <b>{kicked_name}</b>\n\n"
            f"🎉 <b>ИГРА ОКОНЧЕНА! ПЕРЕГОВОРЫ ЗАВЕРШЕНЫ!</b>\n\n"
            f"Контракт с клубом получают лучшие форварды:\n{winners_str}",
            parse_mode="HTML"
        )
        await db.set_lobby_status(chat_id, "ended")
    else:
        next_round = lobby[4] + 1
        await db.set_lobby_status(chat_id, f"round{next_round}", next_round)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🎴 Вскрыть карту", callback_data="open_trait_menu")
        builder.button(text="🗳 Перейти к голосованию", callback_data="go_to_voting")
        builder.adjust(1)

        await callback.message.edit_text(
            f"❌ Большинством голосов из команды изгнан: <b>{kicked_name}</b>\n\n"
            f"🔥 <b>РАУНД {next_round}!</b>\n"
            f"Осталось претендентов: {len(alive)}. Нужно оставить: {winners_needed}.\n"
            f"Вскрывайте следующие характеристики и готовьтесь к финальному голосованию!",
            reply_markup=builder.as_markup(),
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
