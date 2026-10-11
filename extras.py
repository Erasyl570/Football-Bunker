# -*- coding: utf-8 -*-
"""v15: тайные цели, события между раундами и заголовки итогов.

Чистая логика без aiogram/БД: бот только исполняет описанные здесь эффекты
(показать сообщение, записать вскрытие, начислить монеты, отправить ЛС).
"""
import html
import random

import cards
import traits

GOAL_REWARD = 150
EVENT_CHANCE = 0.45
SPONSOR_GIFT = 100

_ROLE_ICON = {"goalkeeper": "🧤", "defender": "🛡", "midfielder": "⚙️", "attacker": "⚔️"}
_ROLE_ONE = {"goalkeeper": "вратарь", "defender": "защитник", "midfielder": "полузащитник", "attacker": "нападающий"}


def _role(pack):
    return cards._position_role(str((pack or {}).get("position", "")))


def _num(text):
    import re
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(text or ""))
    return float(m.group(1).replace(",", ".")) if m else None


# ==========================================================================
# 1. ТАЙНЫЕ ЦЕЛИ
# ==========================================================================
def _goal_candidates(packs, game_type):
    """Список (code, param, text) целей, которые в принципе достижимы на этом раскладе."""
    out = [("survive", None, "Дойди до финальной пары.")]
    if game_type == "club":
        budgets = [v for v in (_num(p.get("budget")) for p in packs) if v is not None]
        if len(set(budgets)) > 1:
            out.append(("rich_final", None, "В финал должен пройти клуб с бюджетом выше среднего."))
            out.append(("block_richest", None, "Не дай самому богатому клубу пройти в финал."))
        if any("academy" in traits.club_tags("infrastructure", p.get("infrastructure")) for p in packs):
            out.append(("academy_final", None, "Проведи в финал клуб с сильной академией."))
        return out
    roles = sorted({_role(p) for p in packs} & set(_ROLE_ICON))
    for r in roles:
        out.append(("role_in_final", r, f"Проведи в финальную пару хотя бы одного: {_ROLE_ICON[r]} {_ROLE_ONE[r]}."))
    ages = [a for a in ((_num(p.get("age"))) for p in packs) if a is not None]
    if any(a <= 22 for a in ages):
        out.append(("young_in_final", None, "В финале должен оказаться игрок моложе 23 лет."))
    if any(a >= 30 for a in ages):
        out.append(("veteran_in_final", None, "В финале должен оказаться игрок от 30 лет и старше."))
    if len(packs) >= 3:
        out.append(("block_expensive", None, "Не дай самому дорогому игроку пройти в финал."))
    if any(traits.health_class(p.get("health")) == "bad" for p in packs):
        out.append(("healthy_final", None, "Не пропусти в финал игроков с серьёзными проблемами здоровья."))
    return out


def assign_goals(packs, game_type, rng=None):
    """Кладёт в каждый pack ключ 'goal'. Цели разнообразны: пока есть варианты, не повторяются."""
    rng = rng or random
    cands = _goal_candidates(packs, game_type)
    pool = []
    for pack in packs:
        if not pool:
            pool = cands[:]
            rng.shuffle(pool)
        code, param, text = pool.pop()
        pack["goal"] = {"code": code, "param": param, "text": text}
    return packs


def evaluate_goal(goal, my_id, final_ids, players):
    """players = {user_id: pack} для ВСЕХ участников. final_ids — множество финалистов."""
    if not goal:
        return False
    code, param = goal.get("code"), goal.get("param")
    final_ids = set(final_ids)
    final_packs = [players[i] for i in final_ids if i in players]
    if code == "survive":
        return my_id in final_ids
    if code == "role_in_final":
        return any(_role(p) == param for p in final_packs)
    if code == "young_in_final":
        return any((_num(p.get("age")) or 99) <= 22 for p in final_packs)
    if code == "veteran_in_final":
        return any((_num(p.get("age")) or 0) >= 30 for p in final_packs)
    if code == "block_expensive":
        priced = {i: (_num(p.get("price")) or 0) for i, p in players.items()}
        if not priced:
            return False
        top = max(priced.values())
        return not any(priced[i] == top for i in final_ids if i in priced)
    if code == "healthy_final":
        return not any(traits.health_class(p.get("health")) == "bad" for p in final_packs)
    if code == "rich_final":
        budgets = [(_num(p.get("budget")) or 0) for p in players.values()]
        if not budgets or not final_packs:
            return False
        avg = sum(budgets) / len(budgets)
        return any((_num(p.get("budget")) or 0) > avg for p in final_packs)
    if code == "block_richest":
        budgets = {i: (_num(p.get("budget")) or 0) for i, p in players.items()}
        if not budgets:
            return False
        top = max(budgets.values())
        return not any(budgets[i] == top for i in final_ids if i in budgets)
    if code == "academy_final":
        return any("academy" in traits.club_tags("infrastructure", p.get("infrastructure")) for p in final_packs)
    return False


def goal_results(all_players, final_ids):
    """all_players = [(name, user_id, pack)]. Возвращает [(name, user_id, text, achieved)]."""
    mapping = {uid: pack for _name, uid, pack in all_players}
    out = []
    for name, uid, pack in all_players:
        goal = (pack or {}).get("goal")
        if not goal:
            continue
        out.append((name, uid, goal.get("text", ""), evaluate_goal(goal, uid, final_ids, mapping)))
    return out


def format_goal_results(results):
    if not results:
        return ""
    lines = ["🎯 <b>ТАЙНЫЕ ЦЕЛИ</b>", "───────────────────"]
    for name, _uid, text, ok in results:
        mark = f"✅ +{GOAL_REWARD} 🪙" if ok else "❌"
        lines.append(f"{mark} <b>{html.escape(name)}</b> — <i>{html.escape(text)}</i>")
    return "\n".join(lines)


# ==========================================================================
# 2. СОБЫТИЯ МЕЖДУ РАУНДАМИ
# ==========================================================================
# alive — список dict: {"id", "name", "pack", "revealed": set(trait)}
def _flags(p):
    return traits.item_flags(p["pack"].get("inventory"))


def _e_press_leak(alive, labels, rng):
    cands = [p for p in alive if "secret" not in p["revealed"] and p["pack"].get("secret")
             and "pr" not in _flags(p)]
    if not cands:
        return None
    t = rng.choice(cands)
    return {"code": "press_leak", "reveals": [(t["id"], "secret")],
            "text": f"📰 <b>УТЕЧКА В ПРЕССУ!</b>\nЖурналисты раздобыли секрет игрока <b>{html.escape(t['name'])}</b>:\n"
                    f"🔍 <i>{html.escape(str(t['pack']['secret']))}</i>"}


def _e_doping(alive, labels, rng):
    found = []  # [((uid, trait), строка)]
    for p in alive:
        pack = p["pack"]
        if "doping" in traits.secret_flags(pack.get("secret")) and "secret" not in p["revealed"]:
            found.append(((p["id"], "secret"),
                          f"<b>{html.escape(p['name'])}</b> — 🔍 <i>{html.escape(str(pack['secret']))}</i>"))
        elif "doping" in _flags(p) and "inventory" not in p["revealed"]:
            found.append(((p["id"], "inventory"),
                          f"<b>{html.escape(p['name'])}</b> — 🎒 <i>{html.escape(str(pack['inventory']))}</i>"))
    if not found:
        return None
    rng.shuffle(found)
    found = found[:2]
    return {"code": "doping", "reveals": [r for r, _ in found],
            "text": "🧪 <b>ДОПИНГ-КОНТРОЛЬ!</b>\nПробы показали подозрительные результаты:\n" + "\n".join(t for _, t in found)}


def _e_medical(alive, labels, rng):
    cands = [p for p in alive if "health" not in p["revealed"] and p["pack"].get("health")
             and "medical" not in _flags(p)]
    if not cands:
        return None
    t = rng.choice(cands)
    return {"code": "medical", "reveals": [(t["id"], "health")],
            "text": f"🏥 <b>ВНЕЗАПНЫЙ МЕДОСМОТР!</b>\nВрачи проверили игрока <b>{html.escape(t['name'])}</b>:\n"
                    f"❤️ <i>{html.escape(str(t['pack']['health']))}</i>"}


def _e_rumor(alive, labels, rng):
    cands = [p for p in alive if "secret" not in p["revealed"] and "leaving" in traits.secret_flags(p["pack"].get("secret"))]
    if not cands:
        return None
    t = rng.choice(cands)
    return {"code": "rumor", "reveals": [(t["id"], "secret")],
            "text": f"🗞 <b>ТРАНСФЕРНЫЙ СЛУХ!</b>\nИнсайдеры сообщают о тайных планах игрока <b>{html.escape(t['name'])}</b>:\n"
                    f"🔍 <i>{html.escape(str(t['pack']['secret']))}</i>"}


def _e_scout(alive, labels, rng):
    holders = [p for p in alive if "analytics" in _flags(p)]
    if not holders or len(alive) < 2:
        return None
    dms = []
    for h in holders:
        others = [o for o in alive if o["id"] != h["id"]]
        rng.shuffle(others)
        for o in others:
            hidden = [k for k, v in o["pack"].items()
                      if k in labels and k not in o["revealed"] and v not in (None, "")]
            if hidden:
                key = rng.choice(hidden)
                val = o["pack"][key]
                if key == "age":
                    val = f"{val} лет"
                dms.append((h["id"], f"📊 <b>Скаутский отчёт:</b> твой аналитик выяснил, что у игрока "
                                     f"<b>{html.escape(o['name'])}</b> карта <b>[{labels[key]}]</b>: "
                                     f"<code>{html.escape(str(val))}</code>"))
                break
    if not dms:
        return None
    return {"code": "scout", "dm": dms,
            "text": "📊 <b>СКАУТСКИЙ ОТЧЁТ!</b>\nИгроки с аналитическим оборудованием получили закрытые данные в личные сообщения."}


def _e_crunch(alive, labels, rng):
    return {"code": "crunch", "discussion_factor": 0.5,
            "text": "⏱ <b>ТРАНСФЕРНАЯ ЛИХОРАДКА!</b>\nОкно закрывается: на обсуждение в этом раунде вдвое меньше времени."}


def _e_sponsor(alive, labels, rng):
    t = rng.choice(alive)
    return {"code": "sponsor", "coins": [(t["id"], SPONSOR_GIFT)],
            "text": f"💸 <b>СПОНСОРСКИЙ ПОДАРОК!</b>\n<b>{html.escape(t['name'])}</b> получает <b>{SPONSOR_GIFT} 🪙</b> от щедрого спонсора."}


def _e_audit(alive, labels, rng):
    cands = [p for p in alive if "finance" not in p["revealed"] and p["pack"].get("finance")]
    if not cands:
        return None
    t = rng.choice(cands)
    club = t["pack"].get("club", "клуба")
    return {"code": "audit", "reveals": [(t["id"], "finance")],
            "text": f"📑 <b>ФИНАНСОВЫЙ АУДИТ!</b>\nПроверка вскрыла финансы клуба <b>{html.escape(str(club))}</b> ({html.escape(t['name'])}):\n"
                    f"📊 <i>{html.escape(str(t['pack']['finance']))}</i>"}


_PLAYER_EVENTS = [(_e_press_leak, 3), (_e_doping, 2), (_e_medical, 3), (_e_rumor, 2),
                  (_e_scout, 2), (_e_crunch, 2), (_e_sponsor, 2)]
_CLUB_EVENTS = [(_e_press_leak, 3), (_e_audit, 3), (_e_crunch, 2), (_e_sponsor, 2)]


def plan_event(round_no, alive, game_type, labels, rng=None, force=False):
    """Возвращает описание события или None. Событие бывает со 2-го раунда."""
    rng = rng or random
    if round_no < 2 or len(alive) < 2:
        return None
    if not force and rng.random() >= EVENT_CHANCE:
        return None
    table = _CLUB_EVENTS if game_type == "club" else _PLAYER_EVENTS
    options = []
    for fn, weight in table:
        ev = fn(alive, labels, rng)
        if ev:
            options.append((ev, weight))
    if not options:
        return None
    return rng.choices([o for o, _ in options], weights=[w for _, w in options], k=1)[0]


# ==========================================================================
# 3. ЗАГОЛОВКИ ИТОГОВ
# ==========================================================================
_OK = [
    "«Сезон спасён! В главных ролях финалисты: {who}»",
    "«Идеальное попадание: контракт выполнен, финалисты — {who}»",
    "«Спортдиректор в восторге от выбора: {who}»",
    "«Фанаты ликуют: финалисты {who} — именно то, что заказывал тренер»",
]
_FAIL = [
    "«Позор трансферного окна: финалисты {who} не прошли проверку»",
    "«Совет директоров в ярости: контракт провален, главные герои — {who}»",
    "«Фанаты требуют отставки: финалисты {who} не оправдали ожиданий»",
    "«Тренер в шоке: финалисты {who} — совсем не то, что просили»",
]


def fallback_headlines(success, names, rng=None, count=3):
    rng = rng or random
    if not names:
        return ["«Трансферное окно закрылось без подписаний: клуб остался ни с чем»"]
    who = " и ".join(names)
    pool = list(_OK if success else _FAIL)
    rng.shuffle(pool)
    return [t.format(who=who) for t in pool[:count]]


def build_headline_prompt(title, success, names, condition_lines):
    return (
        "Ты — редактор остроумной футбольной газеты. Придумай ровно 3 коротких заголовка (до 90 символов каждый) "
        "на русском языке про итог игры «Футбольный Бункер». Стиль: жёлтая пресса, шутливо, без грубости и без мата.\n"
        f"Сценарий: {title}\n"
        f"Результат: {'контракт выполнен' if success else 'контракт провален'}\n"
        f"Финалисты: {', '.join(names) if names else 'нет'}\n"
        "Итоги проверки условий:\n" + "\n".join(f"- {x}" for x in condition_lines) + "\n\n"
        "Формат: по одному заголовку на строку, без нумерации, без кавычек-ёлочек, без пояснений."
    )


def parse_headlines(raw, limit=3):
    out = []
    for line in str(raw or "").splitlines():
        line = line.strip().lstrip("-•*0123456789.) ").strip().strip("«»\"")
        if 8 <= len(line) <= 140:
            out.append(f"«{line}»")
        if len(out) >= limit:
            break
    return out
