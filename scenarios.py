# -*- coding: utf-8 -*-
"""v15: сценарии контракта как набор явных условий.

Идея: сценарий = список условий (dict). Одни и те же условия
  * печатаются игрокам (пронумерованный список, без дублей),
  * проверяются в финале (validate_scenario),
  * порождают текст-подводку, поэтому история ВСЕГДА совпадает с правилами.

Генератор раздаёт карточки и подбирает условия так, чтобы:
  * решение существовало (есть подходящая пара/клуб среди выданных карточек);
  * сценарий не был тривиальным (проходит не больше ~60% вариантов);
  * каждое условие реально что-то отсекало (нет «фальшивых» пунктов).
Модуль не зависит от aiogram/БД и целиком тестируется отдельно.
"""
import copy
import itertools
import random
import re

import cards
import traits
import extras

ROLES = ("goalkeeper", "defender", "midfielder", "attacker")
ROLE_ICON = {"goalkeeper": "🧤", "defender": "🛡", "midfielder": "⚙️", "attacker": "⚔️"}
ROLE_ONE = {"goalkeeper": "вратарь", "defender": "защитник", "midfielder": "полузащитник", "attacker": "нападающий"}
ROLE_MANY = {"goalkeeper": "вратари", "defender": "защитники", "midfielder": "полузащитники", "attacker": "нападающие"}
NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

LEADS = [
    "Летнее окно открыто, каждый трансфер под микроскопом.",
    "Спортивный директор ждёт решения до закрытия окна.",
    "Болельщики следят за каждым шагом клуба.",
    "Совет директоров готов подписать контракт, но только на своих условиях.",
    "Тренер составил список пожеланий и не собирается уступать.",
]


# --------------------------------------------------------------------------
# Разбор карточек
# --------------------------------------------------------------------------
def _num(text):
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(text or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def _fmt(x):
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:.1f}"


def pack_role(pack) -> str:
    return cards._position_role(str((pack or {}).get("position", "")))


def pack_price(pack):
    return _num((pack or {}).get("price"))


def pack_age(pack):
    v = _num((pack or {}).get("age"))
    return int(v) if v is not None else None


def _names(entries, pred):
    return ", ".join(n for n, p in entries if pred(p)) or "—"


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def _round(x, step):
    return int(round(x / step) * step)


# --------------------------------------------------------------------------
# Условия игроков: check(c, entries) -> (ok, сообщение)
# entries = [(имя, pack), ...]
# --------------------------------------------------------------------------
def _c_total_price(c, e):
    vals = [pack_price(p) for _, p in e]
    if any(v is None for v in vals):
        return False, "Не удалось определить цену одного из игроков."
    total = sum(vals)
    ok = total <= c["max"]
    return ok, (f"Сумма цен €{_fmt(total)}M — в пределах лимита €{c['max']}M." if ok
                else f"Сумма цен €{_fmt(total)}M превышает лимит €{c['max']}M.")


def _c_each_price(c, e):
    bad = [n for n, p in e if (pack_price(p) is None or pack_price(p) > c["max"])]
    return (not bad), (f"Цена каждого не выше €{c['max']}M." if not bad
                       else f"Дороже €{c['max']}M: {', '.join(bad)}.")


def _c_roles_all(c, e):
    have = [pack_role(p) for _, p in e]
    need = list(c["roles"])
    pool = list(have)
    missing = []
    for r in need:
        if r in pool:
            pool.remove(r)
        else:
            missing.append(r)
    ok = not missing
    if ok:
        return True, "Нужные роли закрыты: " + " + ".join(f"{ROLE_ICON[r]} {ROLE_ONE[r]}" for r in need) + "."
    return False, "Не хватает: " + ", ".join(f"{ROLE_ICON[r]} {ROLE_ONE[r]}" for r in missing) + "."


def _c_all_role(c, e):
    bad = [n for n, p in e if pack_role(p) != c["role"]]
    r = c["role"]
    return (not bad), (f"Все выбранные — {ROLE_ICON[r]} {ROLE_MANY[r]}." if not bad
                       else f"Не {ROLE_MANY[r]}: {', '.join(bad)}.")


def _c_young_old(c, e):
    ages = [pack_age(p) for _, p in e]
    if any(a is None for a in ages):
        return False, "Не удалось определить возраст."
    young = any(a <= 25 for a in ages)
    old = any(a >= 28 for a in ages)
    ok = young and old and len(ages) >= 2
    shown = "/".join(str(a) for a in ages)
    return ok, (f"Возраст {shown}: есть и молодой, и опытный." if ok
                else f"Возраст {shown}: нужен игрок ≤25 лет и игрок ≥28 лет.")


def _c_contract_years(c, e):
    bad = []
    for n, p in e:
        y = traits.parse_contract(p.get("contract")).get("years")
        if y is None or y < c["years"]:
            bad.append(n)
    return (not bad), (f"У каждого контракт не короче {c['years']} лет." if not bad
                       else f"Контракт короче {c['years']} лет: {', '.join(bad)}.")


def _c_clause_each(c, e):
    bad = []
    for n, p in e:
        v = traits.parse_contract(p.get("contract")).get("clause")
        if v is None or v > c["max"]:
            bad.append(n)
    return (not bad), (f"Отступные каждого не выше €{c['max']}M." if not bad
                       else f"Слишком большие отступные: {', '.join(bad)}.")


def _c_salary_total(c, e):
    vals = [traits.parse_contract(p.get("contract")).get("salary") for _, p in e]
    if any(v is None for v in vals):
        return False, "Не удалось определить зарплату."
    total = sum(vals)
    ok = total <= c["max"]
    return ok, (f"Суммарная зарплата €{total}M/год — в рамках €{c['max']}M." if ok
                else f"Суммарная зарплата €{total}M/год выше лимита €{c['max']}M.")


def _c_no_bad_health(c, e):
    bad = [n for n, p in e if traits.health_class(p.get("health")) == "bad"]
    return (not bad), ("Серьёзных проблем со здоровьем нет." if not bad
                       else f"Проблемы со здоровьем: {', '.join(bad)}.")


def _c_good_health(c, e):
    good = [n for n, p in e if traits.health_class(p.get("health")) == "good"]
    return bool(good), (f"В отличной форме: {', '.join(good)}." if good
                        else "Никто не показал отличного здоровья.")


def _c_no_bad_skill(c, e):
    bad = [n for n, p in e if traits.skill_class(p.get("skill")) == "bad"]
    return (not bad), ("Явных слабостей в навыках нет." if not bad
                       else f"Слабый навык: {', '.join(bad)}.")


def _c_skill_domain(c, e):
    d = c["domain"]
    good = [n for n, p in e if traits.skill_class(p.get("skill")) == "good" and d in traits.skill_domains(p.get("skill"))]
    return bool(good), (f"Сильный навык ({traits.DOMAIN_TEXT[d]}): {', '.join(good)}." if good
                        else f"Нет сильного навыка: {traits.DOMAIN_TEXT[d]}.")


def _c_no_bad_vibe(c, e):
    bad = [n for n, p in e if traits.vibe_class(p.get("vibe")) == "bad"]
    return (not bad), ("Скандалистов в паре нет." if not bad else f"Проблемный характер: {', '.join(bad)}.")


PLAYER_CONDS = {
    "total_price_max": dict(
        check=_c_total_price, family="price",
        text=lambda c: f"Сумма цен пары — не более €{c['max']}M",
        hint=lambda c: "Совет директоров жёстко ограничил общий фонд.",
        titles=["ТРАНСФЕРЫ БЕЗ ПЕРЕПЛАТЫ", "ЖЁСТКАЯ ЭКОНОМИЯ", "ФОНД ПОД КОНТРОЛЕМ"]),
    "each_price_max": dict(
        check=_c_each_price, family="price",
        text=lambda c: f"Цена каждого игрока — не более €{c['max']}M",
        hint=lambda c: "Руководство не готово платить за звёздное имя.",
        titles=["БЕЗ ЗВЁЗД", "ПРОСТЫЕ РЕШЕНИЯ"]),
    "roles_all": dict(
        check=_c_roles_all, family="role",
        text=lambda c: "В паре: " + " + ".join(f"{ROLE_ICON[r]} {ROLE_ONE[r]}" for r in c["roles"]),
        hint=lambda c: "Тренеру нужна связка: " + " и ".join(ROLE_ONE[r] for r in c["roles"]) + ".",
        titles=["ДВОЙНОЕ УСИЛЕНИЕ", "НОВЫЙ БАЛАНС", "ТОЧЕЧНАЯ ПЕРЕСТРОЙКА"]),
    "all_role": dict(
        check=_c_all_role, family="role",
        text=lambda c: f"Все выбранные — {ROLE_ICON[c['role']]} {ROLE_MANY[c['role']]}",
        hint=lambda c: f"Тренер хочет усилить одну линию: берём только {ROLE_MANY[c['role']]}.",
        titles=["ОДНА ЛИНИЯ — ОДИН ПЛАН", "ПЕРЕСТРОЙКА ЛИНИИ"]),
    "age_young_old": dict(
        check=_c_young_old, family="age",
        text=lambda c: "Один игрок не старше 25 лет, второй — не моложе 28",
        hint=lambda c: "Нужны и молодой талант, и опытный лидер.",
        titles=["МОЛОДОСТЬ + ОПЫТ", "СМЕНА ПОКОЛЕНИЙ"]),
    "min_contract_years": dict(
        check=_c_contract_years, family="trait",
        text=lambda c: f"У каждого контракт — не менее {c['years']} лет",
        hint=lambda c: "Клуб строит команду на годы вперёд и не берёт игроков «на сезон».",
        titles=["ПРОЕКТ НА ГОДЫ", "ДОЛГАЯ ИГРА"]),
    "max_clause_each": dict(
        check=_c_clause_each, family="trait",
        text=lambda c: f"Отступные каждого — не более €{c['max']}M",
        hint=lambda c: "Руководство боится огромных отступных в контрактах.",
        titles=["СТРАХ ПЕРЕД ОТСТУПНЫМИ", "ЮРИДИЧЕСКАЯ ЧИСТОТА"]),
    "salary_total_max": dict(
        check=_c_salary_total, family="trait",
        text=lambda c: f"Суммарная зарплата — не более €{c['max']}M в год",
        hint=lambda c: "Зарплатная ведомость уже трещит по швам.",
        titles=["ЗАРПЛАТНАЯ ВЕДОМОСТЬ", "БАЛАНС НА ЗАРПЛАТАХ"]),
    "no_bad_health": dict(
        check=_c_no_bad_health, family="trait",
        text=lambda c: "Ни у кого нет серьёзных проблем со здоровьем",
        hint=lambda c: "Медицинский штаб требует игроков без хронических травм.",
        titles=["МЕДИЦИНСКИЙ ШТАБ РЕШАЕТ", "ТОЛЬКО ЗДОРОВЫЕ"]),
    "has_good_health": dict(
        check=_c_good_health, family="trait",
        text=lambda c: "Хотя бы один игрок — в отличной физической форме",
        hint=lambda c: "Календарь плотный, нужен хотя бы один настоящий двигатель.",
        titles=["ПЛОТНЫЙ КАЛЕНДАРЬ", "НУЖЕН ДВИГАТЕЛЬ"]),
    "no_bad_skill": dict(
        check=_c_no_bad_skill, family="trait",
        text=lambda c: "Ни у кого нет явных слабостей в навыках",
        hint=lambda c: "Скауты не прощают очевидных слабостей.",
        titles=["СКАУТЫ НЕ ПРОЩАЮТ", "БЕЗ СЛАБЫХ ЗВЕНЬЕВ"]),
    "skill_domain": dict(
        check=_c_skill_domain, family="trait",
        text=lambda c: f"Хотя бы у одного сильный навык: {traits.DOMAIN_TEXT[c['domain']]}",
        hint=lambda c: f"Команде срочно нужна сильная сторона: {traits.DOMAIN_TEXT[c['domain']]}.",
        titles=["ПОД КОНКРЕТНУЮ ЗАДАЧУ", "ТОЧНЫЙ ПОДБОР"]),
    "no_bad_vibe": dict(
        check=_c_no_bad_vibe, family="trait",
        text=lambda c: "Ни одного скандалиста: характер и окружение без токсичности",
        hint=lambda c: "В раздевалке больше не потерпят скандалистов.",
        titles=["ЧИСТАЯ РАЗДЕВАЛКА", "БЕЗ ТОКСИКОВ"]),
}
ROLE_FAMILY_TYPES = ("roles_all", "all_role")
SUBJECTIVE_TYPES = ("no_bad_health", "has_good_health", "no_bad_skill", "skill_domain", "no_bad_vibe")

# --------------------------------------------------------------------------
# Условия клубов
# --------------------------------------------------------------------------
_FIELD_LABEL = {"squad": "Состав", "infrastructure": "Инфраструктура", "reputation": "Репутация", "finance": "Финансы"}

CLUB_TAG_DEFS = {
    "academy": ("infrastructure", "academy", False, "Нужна сильная академия или работа с молодёжью",
                "Игрок хочет развиваться там, где умеют растить молодёжь.", ["АКАДЕМИЯ ПРОТИВ ЗВЁЗД", "ШКОЛА РАЗВИТИЯ"]),
    "medical": ("infrastructure", "medical", False, "Нужна сильная медицинская база",
                "После тяжёлых травм игроку важна современная медицина.", ["ВТОРОЕ ДЫХАНИЕ", "ЗАБОТА О ЗДОРОВЬЕ"]),
    "analytics": ("infrastructure", "analytics", False, "Нужна сильная аналитическая команда",
                  "Игрок верит в цифры и ищет клуб с аналитикой.", ["ФУТБОЛ ПО ДАННЫМ", "УМНЫЙ КЛУБ"]),
    "modern": ("infrastructure", "modern", False, "Нужен современный тренировочный центр",
               "Для игрока важны условия на тренировках.", ["ТРЕНИРОВОЧНАЯ БАЗА МЕЧТЫ", "УСЛОВИЯ ДЛЯ РОСТА"]),
    "commercial": ("infrastructure", "commercial", False, "Нужна сильная коммерческая база (стадион или фан-база)",
                   "Игрок хочет играть там, где полные трибуны.", ["ПОЛНЫЕ ТРИБУНЫ", "ГОЛОС БОЛЕЛЬЩИКОВ"]),
    "leaders": ("squad", "leaders", False, "В составе должны быть лидеры, вокруг которых строится проект",
                "Игрок ищет сильных лидеров рядом.", ["ПОД КРЫЛО ЛИДЕРОВ", "ЛИДЕРЫ РАЗДЕВАЛКИ"]),
    "balanced": ("squad", "balanced", False, "Нужен сбалансированный состав без явной слабой зоны",
                 "Игрок не хочет попасть в команду с дырами.", ["СБАЛАНСИРОВАННЫЙ ПРОЕКТ", "РОВНЫЙ СОСТАВ"]),
    "young": ("squad", "young", False, "Нужен молодой состав с потенциалом",
              "Игрок хочет расти вместе с молодой командой.", ["ПРОЕКТ НА ТРИ ГОДА", "РАСТИ С КОМАНДОЙ"]),
    "not_thin": ("squad", "thin", True, "Состав не должен страдать от нехватки глубины",
                 "Игрок не готов играть без замен.", ["ШИРОКАЯ СКАМЕЙКА", "ГЛУБИНА СОСТАВА"]),
    "contender": ("reputation", "contender", False, "Клуб должен реально бороться за титулы",
                  "Игрок хочет играть за трофеи.", ["ВОЗВРАЩЕНИЕ НА ВЕРШИНУ", "ГОНКА ЗА ТИТУЛАМИ"]),
    "youth_rep": ("reputation", "youth", False, "Клуб должен славиться развитием молодых",
                  "Игрок выбирает клуб с репутацией «фабрики талантов».", ["ФАБРИКА ТАЛАНТОВ", "ПУТЬ ВОСПИТАННИКОВ"]),
    "stable_rep": ("reputation", "stable", False, "Нужен клуб с репутацией стабильности",
                   "Игрок устал от хаоса и хочет предсказуемости.", ["ТИХАЯ ГАВАНЬ", "СТАБИЛЬНОСТЬ ПРЕЖДЕ ВСЕГО"]),
    "fin_stable": ("finance", "stable", False, "Финансы клуба должны быть стабильными",
                   "Игрок не хочет задержек по зарплате.", ["НАДЁЖНЫЙ КОШЕЛЁК", "ФИНАНСОВЫЙ КОМФОРТ"]),
    "fin_calm": ("finance", "tense", True, "Финансовая ситуация не должна быть напряжённой",
                 "Игрок боится финансовых проблем.", ["СПОКОЙНЫЕ ФИНАНСЫ", "БЕЗ ДОЛГОВ"]),
}


def _cc_min_budget(c, e):
    n, p = e[0]
    v = _num(p.get("budget"))
    ok = v is not None and v >= c["min"]
    return ok, (f"Бюджет €{_fmt(v)}M — не меньше €{c['min']}M." if ok
                else f"Бюджет €{_fmt(v) if v is not None else '?'}M ниже требуемых €{c['min']}M.")


def _cc_tag(c, e):
    field, tag, neg, _text, _hint, _titles = CLUB_TAG_DEFS[c["key"]]
    n, p = e[0]
    tags = traits.club_tags(field, p.get(field))
    ok = (tag in tags) != neg
    label = _FIELD_LABEL[field]
    return ok, (f"{label} подходит: «{p.get(field, '—')}»." if ok
                else f"{label} не подходит: «{p.get(field, '—')}».")


# --------------------------------------------------------------------------
# Реестр + общие функции
# --------------------------------------------------------------------------
def _cond_def(c):
    t = c["type"]
    if t in PLAYER_CONDS:
        return PLAYER_CONDS[t]
    if t == "club_min_budget":
        return dict(check=_cc_min_budget, family="budget",
                    text=lambda c: f"Бюджет клуба — не менее €{c['min']}M",
                    hint=lambda c: "Игроку нужен клуб, который потянет его запросы.",
                    titles=["ПЕРЕЕЗД ЗА ДЕНЬГАМИ", "ЦЕНА ВОПРОСА"])
    if t == "club_tag":
        key = c["key"]
        _f, _t, _n, text, hint, titles = CLUB_TAG_DEFS[key]
        return dict(check=_cc_tag, family="tag", text=lambda c: text, hint=lambda c: hint, titles=titles)
    raise KeyError(t)


def cond_text(c) -> str:
    return _cond_def(c)["text"](c)


def check_conditions(conds, entries):
    results = []
    for c in conds:
        ok, msg = _cond_def(c)["check"](c, entries)
        results.append((ok, msg))
    return results


def _analyze(conds, packs, n):
    entries = [(f"#{i + 1}", p) for i, p in enumerate(packs)]
    combos = list(itertools.combinations(range(len(entries)), n))
    rows = []
    for combo in combos:
        ents = [entries[i] for i in combo]
        rows.append([ok for ok, _ in check_conditions(conds, ents)])
    valid = sum(1 for r in rows if all(r))
    binding = []
    for i in range(len(conds)):
        binding.append(any(all(r[j] for j in range(len(conds)) if j != i) and not r[i] for r in rows))
    return valid, len(combos), binding


def count_valid(scenario, entries):
    """Сколько наборов из entries=[(имя, pack)] проходят все условия сценария."""
    n = scenario.get("winners_needed", 2)
    packs = [p for _, p in entries]
    valid, total, _ = _analyze(scenario["conditions"], packs, n)
    return valid, total


# --------------------------------------------------------------------------
# Раздача карточек
# --------------------------------------------------------------------------
_ROLE_WEIGHTS = [("attacker", 28), ("midfielder", 28), ("defender", 30), ("goalkeeper", 14)]


def _take(pool, used, rng):
    """Берёт уникальный элемент пула; если пул исчерпан — допускает повтор."""
    free = [x for x in pool if x not in used]
    choice = rng.choice(free or pool)
    used.add(choice)
    return choice


def make_player_packs(num_players, rng):
    roles = rng.choices([r for r, _ in _ROLE_WEIGHTS], weights=[w for _, w in _ROLE_WEIGHTS], k=num_players)
    used_pos, used_skill = {r: set() for r in ROLES}, {r: set() for r in ROLES}
    used_h, used_i, used_s, used_v = set(), set(), set(), set()
    vibe_pool = [t for t, _ in traits.VIBES]
    packs = []
    for role in roles:
        packs.append({
            "position": _take(cards.POSITIONS[role], used_pos[role], rng),
            "age": rng.randint(17, 35),
            "price": f"€{rng.randint(20, 200)}M",
            "health": _take(cards.HEALTH_TRAITS, used_h, rng),
            "skill": _take(cards.SKILLS[role], used_skill[role], rng),
            "contract": traits.make_contract(rng),
            "vibe": _take(vibe_pool, used_v, rng),
            "inventory": _take(cards.INVENTORIES, used_i, rng),
            "secret": _take(cards.SECRETS, used_s, rng),
        })
    return packs


def make_club_packs(num_players, rng):
    names = rng.sample(cards.CLUB_NAMES, k=min(num_players, len(cards.CLUB_NAMES)))
    while len(names) < num_players:
        names.append(rng.choice(cards.CLUB_NAMES))
    return [{
        "club": names[i],
        "budget": f"€{rng.choice(cards.CLUB_BUDGETS)}M",
        "squad": rng.choice(cards.CLUB_SQUADS),
        "finance": rng.choice(cards.CLUB_FINANCE),
        "infrastructure": rng.choice(cards.CLUB_INFRA),
        "reputation": rng.choice(cards.CLUB_REPUTATION),
        "problem": rng.choice(cards.CLUB_PROBLEMS),
        "secret": rng.choice(cards.CLUB_SECRETS),
    } for i in range(num_players)]


# --------------------------------------------------------------------------
# Построение условий (по реальным данным выданных карточек)
# --------------------------------------------------------------------------
def _make_player_cond(t, packs, n, rng):
    combos = list(itertools.combinations(range(len(packs)), n))
    prices = sorted(v for v in (pack_price(p) for p in packs) if v is not None)
    if t == "total_price_max":
        sums = sorted(sum(pack_price(packs[i]) for i in c) for c in combos)
        v = _pct(sums, rng.uniform(0.35, 0.75))
        return {"type": t, "max": max(40, _round(v, 10))} if v else None
    if t == "each_price_max":
        v = _pct(prices, rng.uniform(0.5, 0.8))
        return {"type": t, "max": max(30, _round(v, 10))} if v else None
    if t == "roles_all":
        present = sorted({pack_role(p) for p in packs} & set(ROLES))
        if len(present) < 2:
            return None
        return {"type": t, "roles": rng.sample(present, 2)}
    if t == "all_role":
        counts = {r: sum(1 for p in packs if pack_role(p) == r) for r in ROLES}
        ok = [r for r, c in counts.items() if c >= n]
        return {"type": t, "role": rng.choice(ok)} if ok else None
    if t == "age_young_old":
        return {"type": t}
    if t == "min_contract_years":
        return {"type": t, "years": rng.choice([2, 3, 3, 4])}
    if t == "max_clause_each":
        vals = sorted({traits.parse_contract(p["contract"])["clause"] for p in packs})
        v = _pct(vals, rng.uniform(0.4, 0.8))
        return {"type": t, "max": v} if v else None
    if t == "salary_total_max":
        sums = sorted(sum(traits.parse_contract(packs[i]["contract"])["salary"] for i in c) for c in combos)
        v = _pct(sums, rng.uniform(0.35, 0.75))
        return {"type": t, "max": max(8, _round(v, 2))} if v else None
    if t == "skill_domain":
        have = {}
        for p in packs:
            if traits.skill_class(p["skill"]) == "good":
                for d in traits.skill_domains(p["skill"]):
                    have[d] = have.get(d, 0) + 1
        if not have:
            return None
        doms = list(have)
        return {"type": t, "domain": rng.choices(doms, weights=[have[d] for d in doms], k=1)[0]}
    if t in ("no_bad_health", "has_good_health", "no_bad_skill", "no_bad_vibe"):
        return {"type": t}
    return None


_TRAIT_WEIGHTS = {"no_bad_health": 14, "has_good_health": 8, "no_bad_skill": 12, "skill_domain": 14,
                  "no_bad_vibe": 10, "min_contract_years": 10, "max_clause_each": 6, "salary_total_max": 6}


def _pick_trait_type(rng, taken):
    types = [t for t in _TRAIT_WEIGHTS if t not in taken]
    if not types:
        return None
    return rng.choices(types, weights=[_TRAIT_WEIGHTS[t] for t in types], k=1)[0]


def _compose_player(packs, n, rng):
    conds, taken = [], set()

    def add(t):
        c = _make_player_cond(t, packs, n, rng)
        if c and c["type"] not in taken:
            conds.append(c)
            taken.add(c["type"])
            return True
        return False

    r = rng.random()
    if r < 0.55:
        add(rng.choice(ROLE_FAMILY_TYPES))
    elif r < 0.70:
        add("age_young_old")
    # цена
    pr = rng.random()
    if pr < 0.50:
        add("total_price_max")
    elif pr < 0.62:
        add("each_price_max")
    # хотя бы 2 условия и «часто» условия по здоровью/навыкам/характеру
    want_trait = len(conds) < 2 or rng.random() < 0.65
    if want_trait:
        t = _pick_trait_type(rng, taken)
        if t:
            add(t)
    if len(conds) < 2:
        t = _pick_trait_type(rng, taken)
        if t:
            add(t)
    if len(conds) == 2 and rng.random() < 0.22:
        t = _pick_trait_type(rng, taken)
        if t:
            add(t)
    return conds if len(conds) >= 2 else None


def _market_event(conds, rng):
    """Редкое событие рынка меняет лимит цены. Возвращает текст или ''."""
    price = next((c for c in conds if c["type"] == "total_price_max"), None)
    if not price or rng.random() >= 0.20:
        return ""
    delta = rng.choice([-40, -20, 30, 50])
    new_max = max(40, price["max"] + delta)
    if new_max == price["max"]:
        return ""
    old = price["max"]
    price["max"] = new_max
    if delta < 0:
        return f"📉 <b>Рынок просел:</b> совет директоров урезал лимит с €{old}M до €{new_max}M."
    return f"📈 <b>Рынок перегрет:</b> клуб выделил дополнительные средства — лимит вырос с €{old}M до €{new_max}M."


def _acceptable(conds, packs, n, stage):
    valid, total, binding = _analyze(conds, packs, n)
    if valid < 1:
        return False
    cap = max(1, int(0.6 * total))
    if stage <= 1:
        if valid > cap:
            return False
        if total >= 6 and not all(binding):
            return False
        return True
    if stage == 2:
        return valid <= cap
    return valid < total or total == 1


def _constructive_player(packs, n, rng):
    """Гарантированно решаемый сценарий: условия строятся ПО реальной паре игроков."""
    combo = rng.sample(range(len(packs)), n)
    chosen = [packs[i] for i in combo]
    total = sum(pack_price(p) or 0 for p in chosen)
    roles = [pack_role(p) for p in chosen]
    conds = []
    if len(set(roles)) == len(roles) and all(r in ROLES for r in roles):
        conds.append({"type": "roles_all", "roles": roles})
    elif roles and all(r == roles[0] for r in roles) and roles[0] in ROLES:
        conds.append({"type": "all_role", "role": roles[0]})
    conds.append({"type": "total_price_max", "max": max(40, int(-(-total // 10) * 10))})
    return conds


def _title_and_story(conds, club, rng, game_type):
    first = _cond_def(conds[0])
    title = rng.choice(first["titles"])
    hints = [_cond_def(c)["hint"](c) for c in conds]
    lead = rng.choice(LEADS)
    return title, lead + " " + " ".join(hints)


def _build_player_scenario(packs, rng):
    n = 2
    for stage in (1, 2, 3):
        for _ in range(110 if stage == 1 else 70):
            conds = _compose_player(packs, n, rng)
            if not conds:
                continue
            conds = copy.deepcopy(conds)
            event = _market_event(conds, rng)
            if _acceptable(conds, packs, n, stage):
                return conds, event
    return None


def _make_club_tag_cond(key):
    return {"type": "club_tag", "key": key}


def _compose_club(packs, rng):
    budgets = sorted(v for v in (_num(p["budget"]) for p in packs) if v is not None)
    conds = []
    # порог бюджета берём по реальным значениям, чтобы он что-то отсекал
    t = _pct(budgets, rng.uniform(0.2, 0.7))
    conds.append({"type": "club_min_budget", "min": int(max(60, _round(t, 10)))})
    keys = list(CLUB_TAG_DEFS)
    rng.shuffle(keys)
    k = 1 if rng.random() < 0.7 else 2
    seen_fields = set()
    for key in keys:
        field = CLUB_TAG_DEFS[key][0]
        if field in seen_fields:
            continue
        conds.append(_make_club_tag_cond(key))
        seen_fields.add(field)
        if len(conds) - 1 >= k:
            break
    return conds


def _constructive_club(packs, rng):
    p = rng.choice(packs)
    b = _num(p["budget"]) or 60
    conds = [{"type": "club_min_budget", "min": int(max(60, (int(b) // 10) * 10))}]
    options = []
    for key, (field, tag, neg, *_rest) in CLUB_TAG_DEFS.items():
        if (tag in traits.club_tags(field, p.get(field))) != neg:
            options.append(key)
    rng.shuffle(options)
    seen = set()
    for key in options:
        f = CLUB_TAG_DEFS[key][0]
        if f in seen:
            continue
        conds.append(_make_club_tag_cond(key))
        seen.add(f)
        if len(conds) >= 3:
            break
    return conds


def _build_club_scenario(packs, rng):
    for stage in (1, 2, 3):
        for _ in range(110 if stage == 1 else 70):
            conds = _compose_club(packs, rng)
            if _acceptable(conds, packs, 1, stage):
                return conds, ""
    return None


# --------------------------------------------------------------------------
# Текст сценария
# --------------------------------------------------------------------------
def render_scenario(scen) -> str:
    conds = scen["conditions"]
    n = scen.get("winners_needed", 2)
    club_mode = scen.get("game_type") == "club"
    head = f"🎬 <b>{scen['title']}</b>" if club_mode else f"🎬 <b>{scen['title']} — {scen['club']}</b>"
    word = {1: "одного", 2: "двух", 3: "трёх"}.get(n, str(n))
    task = "выбрать <b>один</b> клуб" if club_mode else f"выбрать <b>{word}</b> игроков"
    lines = [head, f"<i>{scen['intro']}</i>", "", f"📋 <b>ЗАДАЧА:</b> {task}"]
    for i, c in enumerate(conds):
        lines.append(f"{NUM_EMOJI[i]} {cond_text(c)}")
    lines.append("⚠️ <i>Нарушение любого пункта = провал.</i>")
    if any(c["type"] in SUBJECTIVE_TYPES for c in conds):
        lines.append("🔎 <i>Здоровье, навыки и характер оцениваются по смыслу карточки: травмы, ошибки и конфликты — минус.</i>")
    if scen.get("market_event"):
        lines += ["", scen["market_event"]]
    for tw in scen.get("twists", []):
        lines += ["", f"🌪 <b>ПОВОРОТ СЮЖЕТА:</b> {tw}"]
    return "\n".join(lines)


def _finalize(scen):
    scen["text"] = render_scenario(scen)
    return scen


# --------------------------------------------------------------------------
# Главная точка входа
# --------------------------------------------------------------------------
def generate_game(num_players, game_type="player", rng=None):
    """Возвращает (scenario_dict, packs). Решение гарантированно существует."""
    rng = rng or random
    club_mode = game_type == "club"
    n = 1 if club_mode else 2
    result = None
    packs = None
    for _ in range(8):
        packs = make_club_packs(num_players, rng) if club_mode else make_player_packs(num_players, rng)
        result = _build_club_scenario(packs, rng) if club_mode else _build_player_scenario(packs, rng)
        if result:
            break
    if not result:
        # Конструктивный запасной вариант: условия строятся по реальным карточкам.
        if club_mode:
            result = (_constructive_club(packs, rng), "")
        else:
            result = (_constructive_player(packs, n, rng), "")
    conds, event = result
    club = rng.choice(cards.CLUBS)
    title, intro = _title_and_story(conds, club, rng, game_type)
    scen = {
        "version": 15,
        "game_type": "club" if club_mode else "player",
        "title": title,
        "club": club,
        "intro": intro,
        "winners_needed": n,
        "conditions": conds,
        "market_event": event,
        "twists": [],
        "twist_round": 3 if num_players <= 4 else 2,
        "twist_done": False,
    }
    _finalize(scen)
    extras.assign_goals(packs, scen["game_type"], rng)
    return scen, packs


# --------------------------------------------------------------------------
# Финальная проверка
# --------------------------------------------------------------------------
def validate_scenario(scenario, winners_data):
    """winners_data = [(имя, pack), ...]. Возвращает (успех, [строки])."""
    conds = scenario.get("conditions") if isinstance(scenario, dict) else None
    if not (isinstance(conds, list) and conds and isinstance(conds[0], dict) and "type" in conds[0]):
        return cards.validate_scenario(scenario, winners_data)  # старый формат
    need = scenario.get("winners_needed", 2)
    if len(winners_data) != need:
        word = "клуб" if scenario.get("game_type") == "club" else "игроков"
        return False, [f"❌ Нужно ровно {need} {word}, а в финале осталось {len(winners_data)}."]
    results = check_conditions(conds, list(winners_data))
    lines = [("✅ " if ok else "❌ ") + msg for ok, msg in results]
    return all(ok for ok, _ in results), lines


# --------------------------------------------------------------------------
# Поворот сюжета посреди игры
# --------------------------------------------------------------------------
def make_twist(scenario, alive_entries, rng=None):
    """Пытается усложнить сценарий так, чтобы среди ЖИВЫХ игроков решение осталось.
    Возвращает (новый_сценарий, текст_анонса) или None."""
    rng = rng or random
    if not scenario or scenario.get("twist_done") or not scenario.get("conditions"):
        return None
    n = scenario.get("winners_needed", 2)
    packs = [p for _, p in alive_entries]
    if len(packs) < n + 1:
        return None
    club_mode = scenario.get("game_type") == "club"
    conds = scenario["conditions"]
    types = {c["type"] for c in conds}
    options = []  # (новые условия, текст)
    if club_mode:
        for i, c in enumerate(conds):
            if c["type"] == "club_min_budget":
                for add in (20, 40):
                    new = copy.deepcopy(conds)
                    new[i]["min"] = c["min"] + add
                    options.append((new, f"совет директоров поднял планку — бюджет теперь не менее €{c['min'] + add}M."))
        used_fields = {CLUB_TAG_DEFS[c["key"]][0] for c in conds if c["type"] == "club_tag"}
        for key, d in CLUB_TAG_DEFS.items():
            if d[0] in used_fields or len(conds) >= 4:
                continue
            new = copy.deepcopy(conds) + [_make_club_tag_cond(key)]
            options.append((new, f"игрок добавил личное требование: {d[3]}."))
    else:
        for i, c in enumerate(conds):
            if c["type"] == "total_price_max":
                nm = max(40, _round(c["max"] * 0.85, 10))
                if nm < c["max"]:
                    new = copy.deepcopy(conds)
                    new[i]["max"] = nm
                    options.append((new, f"совет директоров урезал фонд, теперь сумма цен не более €{nm}M."))
        if len(conds) < 4:
            for t in ("no_bad_health", "no_bad_skill", "no_bad_vibe", "has_good_health",
                      "min_contract_years", "skill_domain", "age_young_old"):
                if t in types or (t == "age_young_old" and "age_young_old" in types):
                    continue
                c = _make_player_cond(t, packs, n, rng)
                if c:
                    new = copy.deepcopy(conds) + [c]
                    options.append((new, f"тренер добавил требование: {cond_text(c)}."))
    rng.shuffle(options)
    good, fallback = [], []
    for new, text in options:
        valid, total, _ = _analyze(new, packs, n)
        if valid >= 1 and valid < total:
            good.append((new, text))
        elif valid >= 1:
            fallback.append((new, text))
    pick = (good or [])[:1] or []
    if not pick:
        return None
    new_conds, text = pick[0]
    out = copy.deepcopy(scenario)
    out["conditions"] = new_conds
    out["twists"] = list(out.get("twists", [])) + [text]
    out["twist_done"] = True
    _finalize(out)
    return out, text