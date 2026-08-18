import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

import database as db
import cards

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    args = command.args
    # Обработка глубокой ссылки из группы "Вскрыть карту в ЛС"
    if args and args.startswith("reveal_"):
        try:
            chat_id = int(args.split("_")[1])
            await show_trait_menu_pm(message.from_user.id, chat_id, message)
            return
        except ValueError:
            pass

    await message.answer("⚽️ <b>Футбольный Бункер запущен!</b>\nДобавь бота в групповой чат и напиши <code>/game</code> для старта.", parse_mode="HTML")

async def show_trait_menu_pm(user_id: int, chat_id: int, message_obj: types.Message):
    card = await db.get_player_card(chat_id, user_id)
    if not card or card[10] == 0:
        return await message_obj.answer("⚠️ Ты не участвуешь в текущей игре или выбыл из команды!")

    builder = InlineKeyboardBuilder()
    labels = [
        ("⚽ Позиция", "position", card[5]),
        ("🏥 Здоровье", "health", card[6]),
        ("🎯 Навык", "skill", card[7]),
        ("🎒 Багаж", "inventory", card[8]),
        ("🤫 Секрет", "secret", card[9])
    ]
    
    unrevealed_count = 0
    for name, code, is_rev in labels:
        if is_rev == 0:
            unrevealed_count += 1
            builder.button(text=f"Открыть {name}", callback_data=f"pmrev_{chat_id}_{code}")
            
    builder.adjust(2)
    
    if unrevealed_count == 0:
        return await message_obj.answer("ℹ️ Ты уже вскрыл все свои доступные карты!")

    await message_obj.answer("🎴 <b>Выбери характеристику для публикации в чат группы:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("pmrev_"))
async def process_pm_reveal(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    chat_id = int(parts[1])
    trait_code = parts[2]
    user = callback.from_user

    card = await db.get_player_card(chat_id, user.id)
    if not card or card[10] == 0:
        return await callback.answer("Ошибка: Игра завершена или ты выбыл.", show_alert=True)

    await db.reveal_trait(chat_id, user.id, trait_code)

    val_map = {
        "position": (card[0], "⚽️ Позицию"),
        "health": (card[1], "🏥 Здоровье"),
        "skill": (card[2], "🎯 Навык"),
        "inventory": (card[3], "🎒 Багаж"),
        "secret": (card[4], "🤫 Секрет")
    }
    val, title = val_map[trait_code]

    try:
        await bot.send_message(
            chat_id, 
            f"📢 <b>{user.first_name}</b> показывает всем свою <b>{title}</b>:\n👉 <i>{val}</i>", 
            parse_mode="HTML"
        )
        await callback.message.edit_text(f"✅ <b>{title}</b> успешно отправлена в групповой чат!\n\n<i>Вернись в чат группы для продолжения.</i>", parse_mode="HTML")
    except Exception:
        await callback.answer("Ошибка отправки в группу. Проверь, что бот состоит в чате.", show_alert=True)

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
        f"<i>Минимум для старта: 3 человека.</i>",
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
    await callback.answer("Ты успешно вступил в игру!")

@dp.callback_query(F.data == "start_game")
async def start_game(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    players = await db.get_players(chat_id)
    
    if len(players) < 3:
        return await callback.answer(f"⚠️ Сейчас участников: {len(players)}. Нужно минимум 3 человека!", show_alert=True)

    await callback.answer("Игра начинается!")
    
    scen = cards.generate_scenario(len(players))
    await db.update_lobby_scenario(chat_id, scen["text"], scen["winners_needed"])
    
    # Рассылка полного пака в ЛС
    for p_name, p_id, _ in players:
        try:
            card = await db.get_player_card(chat_id, p_id)
            text_card = (
                f"🎴 <b>ТВОЯ КАРТОЧКА ИГРОКА</b>\n\n"
                f"⚽️ <b>Позиция:</b> {card[0]}\n"
                f"🏥 <b>Физуха/Здоровье:</b> {card[1]}\n"
                f"🎯 <b>Навык:</b> {card[2]}\n"
                f"🎒 <b>Багаж:</b> {card[3]}\n"
                f"🤫 <b>Секрет:</b> {card[4]}\n\n"
                f"<i>Никому не показывай весь пак! Вскрывай характеристики по кнопке в группе.</i>"
            )
            await bot.send_message(p_id, text_card, parse_mode="HTML")
        except Exception:
            pass

    bot_info = await bot.get_me()
    builder = InlineKeyboardBuilder()
    builder.button(text="🎴 Вскрыть карту в ЛС", url=f"https://t.me/{bot_info.username}?start=reveal_{chat_id}")
    builder.button(text="🗳 Перейти к голосованию", callback_data="go_to_voting")
    builder.adjust(1)

    await callback.message.edit_text(
        f"{scen['text']}\n\n"
        f"🔥 <b>РАУНД 1: Вскрытие карт</b>\n"
        f"Нажмите кнопку ниже, чтобы перейти в ЛС с ботом и выбрать карту для вскрытия!",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "go_to_voting")
async def go_to_voting(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    lobby = await db.get_lobby(chat_id)
    
    if not lobby:
        return await callback.answer("Игра не найдена.")

    if callback.from_user.id != lobby[1]:
        return await callback.answer("Только Организатор (Хост) может запускать голосование!", show_alert=True)

    await db.set_lobby_status(chat_id, "voting")
    await db.clear_votes(chat_id)
    
    await update_voting_message(callback.message, chat_id, lobby[4])

async def update_voting_message(message: types.Message, chat_id: int, round_num: int):
    alive_players = await db.get_alive_players(chat_id)
    voters = await db.get_voters(chat_id)
    
    builder = InlineKeyboardBuilder()
    for p_name, p_id in alive_players:
        builder.button(text=f"❌ {p_name}", callback_data=f"vote_{p_id}")
    
    builder.button(text="📊 Подвести итоги голосования", callback_data="finish_voting")
    builder.adjust(2)

    voters_text = ", ".join(voters) if voters else "никто"
    
    text = (
        f"🗳 <b>ГОЛОСОВАНИЕ (Раунд {round_num})!</b>\n\n"
        f"👥 <b>Проголосовали ({len(voters)}/{len(alive_players)}):</b> {voters_text}\n\n"
        f"Нажмите кнопку с именем игрока, которого хотите выгнать из команды:"
    )
    
    try:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    target_id = int(callback.data.split("_")[1])
    voter = callback.from_user
    
    card = await db.get_player_card(chat_id, voter.id)
    if not card or card[10] == 0:
        return await callback.answer("Только живые участники могут голосовать!", show_alert=True)

    async with db.aiosqlite.connect(db.DB_NAME) as conn:
        async with conn.execute("SELECT username FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, target_id)) as cursor:
            row = await cursor.fetchone()
            target_name = row[0] if row else "Игрока"

    await db.add_vote(chat_id, voter.id, target_id)
    await callback.answer(f"✅ Твой голос против {target_name} принят!", show_alert=True)
    
    lobby = await db.get_lobby(chat_id)
    await update_voting_message(callback.message, chat_id, lobby[4])

@dp.callback_query(F.data == "finish_voting")
async def finish_voting(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    lobby = await db.get_lobby(chat_id)
    
    if callback.from_user.id != lobby[1]:
        return await callback.answer("Только Организатор может подвести итоги!", show_alert=True)

    votes_data = await db.get_votes_detailed(chat_id)
    if not votes_data:
        return await callback.answer("⚠️ Никто еще не проголосовал!", show_alert=True)

    results_lines = [f"• <b>{t_name}</b>: {count} гол." for _, t_name, count in votes_data]
    results_text = "\n".join(results_lines)

    kicked_id = votes_data[0][0]
    kicked_name = votes_data[0][1]
    
    await db.eliminate_player(chat_id, kicked_id)
    await db.clear_votes(chat_id)
    
    alive = await db.get_alive_players(chat_id)
    winners_needed = lobby[3]
    bot_info = await bot.get_me()

    if len(alive) <= winners_needed:
        winners_str = "\n".join([f"🏆 <b>{p[0]}</b>" for p in alive])
        await callback.message.edit_text(
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"❌ Из команды изгнан: <b>{kicked_name}</b>\n\n"
            f"🎉 <b>ИГРА ОКОНЧЕНА!</b>\n\n"
            f"Контракт с клубом получают лучшие форварды:\n{winners_str}",
            parse_mode="HTML"
        )
        await db.set_lobby_status(chat_id, "ended")
    else:
        next_round = lobby[4] + 1
        await db.set_lobby_status(chat_id, f"round{next_round}", next_round)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🎴 Вскрыть карту в ЛС", url=f"https://t.me/{bot_info.username}?start=reveal_{chat_id}")
        builder.button(text="🗳 Перейти к голосованию", callback_data="go_to_voting")
        builder.adjust(1)

        await callback.message.edit_text(
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"❌ Из команды изгнан: <b>{kicked_name}</b>\n\n"
            f"🔥 <b>РАУНД {next_round}!</b>\n"
            f"Осталось претендентов: {len(alive)}. Нужно оставить: {winners_needed}.\n"
            f"Перейдите в ЛС бота для выбора следующей карты!",
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
