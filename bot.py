import asyncio
import html
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

import database as db
import cards

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def build_reveal_keyboard(chat_id: int, user_id: int):
    """Генерирует клавиатуру в ЛС только с теми картами, которые ещё не вскрыты."""
    builder = InlineKeyboardBuilder()
    traits = [
        ("position", "📢 Позицию"),
        ("age", "📢 Возраст"),
        ("price", "📢 Цену"),
        ("health", "📢 Здоровье"),
        ("skill", "📢 Навык"),
        ("inventory", "📢 Багаж"),
        ("secret", "📢 Секрет")
    ]
    
    for trait_key, trait_label in traits:
        if not await db.is_trait_revealed(chat_id, user_id, trait_key):
            builder.button(text=trait_label, callback_data=f"reveal_{trait_key}_{chat_id}")
            
    builder.adjust(2)
    return builder.as_markup()

async def start_discussion_and_voting(chat_id: int, current_round: int):
    await db.set_lobby_status(chat_id, "discussion", current_round)
    
    all_players = await db.get_players(chat_id)
    total_count = len(all_players)

    # ДЛЯ 3 ИЛИ 4 ИГРОКОВ: Раунды 1 и 2 без голосования
    if total_count in (3, 4) and current_round < 3:
        await bot.send_message(
            chat_id,
            f"💬 <b>РАУНД {current_round}: ОБСУЖДЕНИЕ (90 СЕКУНД)!</b>\n\n"
            f"Так как в игре {total_count} претендентов, голосование откроется <b>в Раунде 3</b>!\n"
            f"Открывайте по <b>1 карте за раунд</b> в ЛС и доказывайте свою полезность.\n\n"
            f"⏳ <i>Раунд {current_round + 1} начнется через 90 секунд...</i>",
            parse_mode="HTML"
        )
        await asyncio.sleep(90)
        
        check_lobby = await db.get_lobby(chat_id)
        if check_lobby and check_lobby[0] == "discussion":
            next_round = current_round + 1
            await start_discussion_and_voting(chat_id, next_round)
        return

    if total_count in (3, 4) and current_round == 3:
        msg_text = (
            f"💬 <b>РАУНД 3: ВЕШАЮТСЯ КАРТЫ (90 СЕКУНД)!</b>\n\n"
            f"🔓 <b>Вскрывайте карты в ЛС!</b>\n"
            f"Приготовьтесь к первому решающему голосованию!\n\n"
            f"⏳ <i>Голосование начнется через 90 секунд...</i>"
        )
    else:
        msg_text = (
            f"💬 <b>РАУНД {current_round}: ОБСУЖДЕНИЕ (90 СЕКУНД)!</b>\n\n"
            f"Вскрывайте карты в ЛС (максимум 1 за раунд) и убедите команду оставить вас!\n\n"
            f"⏳ <i>Голосование начнется через 90 секунд...</i>"
        )

    await bot.send_message(chat_id, msg_text, parse_mode="HTML")
    await asyncio.sleep(90)
    
    check_lobby = await db.get_lobby(chat_id)
    if check_lobby and check_lobby[0] == "discussion":
        await start_voting_flow(chat_id, current_round)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("⚽️ <b>Футбольный Бункер запущен!</b>\nДобавь бота в групповой чат и напиши <code>/game</code> для старта.", parse_mode="HTML")

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    await db.create_lobby(message.chat.id, message.from_user.id)
    safe_host_name = html.escape(message.from_user.first_name)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 <b>Идет сбор в Футбольный Бункер!</b>\n\n"
        f"Организатор: <a href='tg://user?id={message.from_user.id}'>{safe_host_name}</a>\n"
        f"Участников: 0\n\n"
        f"<i>Минимум для старта: 3 человека. Запустите бота в ЛС!</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "join_game")
async def join_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        user = callback.from_user
        
        empty_pack = {
            "position": "-", "age": "-", "price": "-", "health": "-",
            "skill": "-", "inventory": "-", "secret": "-"
        }
        await db.add_player(chat_id, user.id, user.first_name, empty_pack)
        players = await db.get_players(chat_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
        builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
        builder.adjust(1)

        player_list = "\n".join([f"• {html.escape(p[0])}" for p in players])
        await callback.message.edit_text(
            f"🎮 <b>Идет набор игроков в Футбольный Бункер!</b>\n\nУчастники ({len(players)}):\n{player_list}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer("Ты успешно вступил в игру!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка входа: {e}", show_alert=True)

@dp.callback_query(F.data == "start_game")
async def start_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        players = await db.get_players(chat_id)
        num_players = len(players)
        
        if num_players < 3:
            return await callback.answer(f"⚠️ Сейчас участников: {num_players}. Нужно минимум 3 человека!", show_alert=True)

        await callback.answer("Игра начинается!")
        
        packs = cards.generate_game_packs(num_players)
        scen = cards.generate_scenario(num_players)
        await db.update_lobby_scenario(chat_id, scen["text"], 1)

        bot_username = (await bot.get_me()).username
        failed_pm_players = []

        for idx, (p_name, p_id, _) in enumerate(players):
            pack = packs[idx]
            await db.add_player(chat_id, p_id, p_name, pack)
            
            pm_card_text = (
                f"📋 <b>ТВОЯ КАРТОЧКА ИГРОКА:</b>\n\n"
                f"• Позиция: <b>{html.escape(pack['position'])}</b>\n"
                f"• Возраст: <b>{pack['age']} лет</b>\n"
                f"• Трансферная цена: <b>{pack['price']}</b>\n"
                f"• Здоровье: {html.escape(pack['health'])}\n"
                f"• Навык: {html.escape(pack['skill'])}\n"
                f"• Багаж: {html.escape(pack['inventory'])}\n"
                f"• Секрет: <i>{html.escape(pack['secret'])}</i>\n\n"
                f"👇 <i>Нажимай кнопки ниже, чтобы открыть 1 карту за раунд в общий чат:</i>"
            )
            
            markup = await build_reveal_keyboard(chat_id, p_id)
            
            try:
                await bot.send_message(p_id, pm_card_text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                failed_pm_players.append(f"<a href='tg://user?id={p_id}'>{html.escape(p_name)}</a>")

        players_list_str = "\n".join([f"• {html.escape(p[0])}" for p in players])
        
        pm_warning = ""
        if failed_pm_players:
            pm_warning = f"\n\n⚠️ <b>ВНИМАНИЕ!</b> Следующие игроки не запустили бота в ЛС:\n" + ", ".join(failed_pm_players) + f"\nНажмите 👉 @{bot_username} и запустите бота!"

        await callback.message.edit_text(
            f"{scen['text']}\n\n"
            f"👥 <b>ПРЕТЕНДЕНТЫ НА КОНТРАКТ:</b>\n{players_list_str}\n\n"
            f"📩 <b>Карточки и кнопки отправлены каждому игроку в ЛС!</b>{pm_warning}\n\n"
            f"🔥 Через 10 секунд начнется Раунд 1!",
            parse_mode="HTML"
        )

        await asyncio.sleep(10)
        await start_discussion_and_voting(chat_id, 1)

    except Exception as e:
        await callback.answer(f"❌ Ошибка старта: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("reveal_"))
async def process_reveal(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        trait = parts[1]
        target_chat_id = int(parts[2])
        user = callback.from_user

        lobby = await db.get_lobby(target_chat_id)
        if not lobby:
            return await callback.answer("❌ Игра не найдена!", show_alert=True)
            
        current_round = lobby[4]

        # 1. Проверка: Открывал ли уже эту карту?
        if await db.is_trait_revealed(target_chat_id, user.id, trait):
            return await callback.answer("⚠️ Ты уже вскрыл эту характеристику!", show_alert=True)

        # 2. Проверка КД: Не больше 1 карты за раунд!
        if await db.has_revealed_in_round(target_chat_id, user.id, current_round):
            return await callback.answer(f"⚠️ В Раунде {current_round} ты уже открыл 1 карту! Дождись следующего раунда.", show_alert=True)

        pack = await db.get_player_pack(target_chat_id, user.id)
        if not pack:
            return await callback.answer("❌ Ошибка: ты не найден в этой игре!", show_alert=True)

        trait_titles = {
            "position": ("Позицию", pack.get("position", "-")),
            "age": ("Возраст", f"{pack.get('age', '-')} лет"),
            "price": ("Трансферную цену", pack.get("price", "-")),
            "health": ("Здоровье", pack.get("health", "-")),
            "skill": ("Навык", pack.get("skill", "-")),
            "inventory": ("Багаж", pack.get("inventory", "-")),
            "secret": ("Секрет", pack.get("secret", "-"))
        }

        if trait not in trait_titles:
            return await callback.answer("❌ Неизвестная характеристика!")

        title, value = trait_titles[trait]
        
        # Записываем открытие
        await db.record_reveal(target_chat_id, user.id, trait, current_round)

        # Отправляем сообщение в общий чат
        safe_name = html.escape(user.first_name)
        safe_val = html.escape(str(value))
        await bot.send_message(
            target_chat_id,
            f"🔓 <b>{safe_name}</b> открывает {title}:\n👉 <b>{safe_val}</b>",
            parse_mode="HTML"
        )
        
        # Обновляем кнопки в ЛС (скрываем нажатую)
        new_markup = await build_reveal_keyboard(target_chat_id, user.id)
        await callback.message.edit_reply_markup(reply_markup=new_markup)
        
        await callback.answer(f"Ты открыл {title} в общем чате!")

    except Exception as e:
        await callback.answer(f"❌ Ошибка отправки: {e}", show_alert=True)

async def start_voting_flow(chat_id: int, round_num: int):
    await db.set_lobby_status(chat_id, "voting", round_num)
    await db.clear_votes(chat_id)
    
    alive_players = await db.get_alive_players(chat_id)
    builder = InlineKeyboardBuilder()
    for p_name, p_id in alive_players:
        builder.button(text=f"❌ {p_name}", callback_data=f"vote_{p_id}")
    builder.adjust(2)

    await bot.send_message(
        chat_id,
        f"🗳 <b>ОБСУЖДЕНИЕ ОКОНЧЕНО! НАЧИНАЕТСЯ ГОЛОСОВАНИЕ (Раунд {round_num})!</b>\n\n"
        f"Голосуйте кнопками ниже, кого изгнать из команды:\n"
        f"<i>Проголосовали: 0/{len(alive_players)}</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        target_id = int(callback.data.split("_")[1])
        voter = callback.from_user
        
        card = await db.get_player_card(chat_id, voter.id)
        if not card or card[10] == 0:
            return await callback.answer("❌ Ты изгнан и не можешь голосовать!", show_alert=True)

        if voter.id == target_id:
            return await callback.answer("⚠️ За себя голосовать нельзя!", show_alert=True)

        if await db.has_user_voted(chat_id, voter.id):
            return await callback.answer("⚠️ Ты уже отдал свой голос!", show_alert=True)

        target_name = await db.get_username(chat_id, target_id)
        await db.add_vote(chat_id, voter.id, target_id)
        await callback.answer("Твой голос принят!")

        safe_voter = html.escape(voter.first_name)
        safe_target = html.escape(target_name)

        await bot.send_message(
            chat_id, 
            f"🗳 <b>{safe_voter}</b> проголосовал(а) против <b>{safe_target}</b>!", 
            parse_mode="HTML"
        )

        voters_count = await db.get_voters_count(chat_id)
        alive_players = await db.get_alive_players(chat_id)

        if voters_count >= len(alive_players):
            await bot.send_message(chat_id, "🎉 <b>Все игроки проголосовали! Подводим итоги...</b>", parse_mode="HTML")
            await finish_voting_flow(chat_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка голосования: {e}", show_alert=True)

async def finish_voting_flow(chat_id: int):
    lobby = await db.get_lobby(chat_id)
    votes_data = await db.get_votes_detailed(chat_id)
    
    if not votes_data:
        return

    results_lines = [f"• <b>{html.escape(t_name)}</b>: {count} гол." for _, t_name, count in votes_data]
    results_text = "\n".join(results_lines)

    max_votes = votes_data[0][2]
    top_candidates = [v for v in votes_data if v[2] == max_votes]

    alive = await db.get_alive_players(chat_id)
    winners_needed = 2

    if len(top_candidates) > 1:
        await db.clear_votes(chat_id)
        next_round = lobby[4] + 1

        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"🤝 <b>НИЧЬЯ!</b> Голоса разделились при равном счете.\n"
            f"В этом раунде никто не выбывает!\n\n"
            f"Переходим к Раунду {next_round}. В игре остаются все {len(alive)} претендентов.",
            parse_mode="HTML"
        )
        await start_discussion_and_voting(chat_id, next_round)
        return

    kicked_id, kicked_name = votes_data[0][0], votes_data[0][1]

    await db.eliminate_player(chat_id, kicked_id)
    await db.clear_votes(chat_id)
    
    alive = await db.get_alive_players(chat_id)
    safe_kicked_name = html.escape(kicked_name)

    if len(alive) <= winners_needed:
        winners_str = "\n".join([f"🏆 <b>{html.escape(p[0])}</b>" for p in alive])
        
        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>\n\n"
            f"🎉 <b>ИГРА ОКОНЧЕНА!</b>\n\n"
            f"Контракт с клубом получают 2 лучших игрока:\n{winners_str}",
            parse_mode="HTML"
        )
        await db.set_lobby_status(chat_id, "ended")
    else:
        next_round = lobby[4] + 1

        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>\n\n"
            f"Переходим к Раунду {next_round}. Осталось игроков: {len(alive)}.",
            parse_mode="HTML"
        )
        await start_discussion_and_voting(chat_id, next_round)

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
