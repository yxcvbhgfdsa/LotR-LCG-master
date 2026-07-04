"""RingsDB API 与本地 魔戒玩家牌.csv 字段映射与转换。"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# 关联键（优先级从高到低）
# ---------------------------------------------------------------------------
# 1. 图片链接 (CSV) == octgnid (RingsDB)  — 最可靠，OCTGN UUID
# 2. pack_code + position == 系列(映射后) + 编号
# 3. code (RingsDB 五位编号，如 01001) 可由 pack 首张卡编号 + position 推导

CSV_PRIMARY_KEY = "图片链接"
RINGSD_PRIMARY_KEY = "octgnid"

CSV_SECONDARY_KEYS = ("系列", "编号")
RINGSD_SECONDARY_KEYS = ("pack_code", "position")

# ---------------------------------------------------------------------------
# 列名映射：魔戒玩家牌.csv → RingsDB JSON
# ---------------------------------------------------------------------------
CSV_TO_RINGSD: dict[str, str | tuple[str, ...]] = {
    "系列": ("pack_name", "pack_code"),  # 系列为中文扩展名，需 SERIES_TO_PACK_CODE 转换
    "编号": "position",
    "派系": ("sphere_code", "sphere_name"),
    "卡牌名称": "name",  # 语言不同，仅作语义对应
    "图片链接": "octgnid",
    "类型": ("type_code", "type_name"),
    "独有": "is_unique",
    "卡牌费用": "cost",
    "初始威胁": "threat",
    "意志力": "willpower",
    "攻击力": "attack",
    "防御力": "defense",
    "生命值": "health",
    "任务点": "quest",
    "属性": "traits",  # 语言不同
    "规则文字": "text",  # 语言不同
    # 关键词列 → 嵌入 text 英文关键词，见 KEYWORD_COLUMNS
    "警戒": "Sentinel",
    "远攻": "Ranged",
    "限制": "Restricted",
    "隐匿": "Stealth",
    "厄运": "Doomed",
    "遭遇": "Surge",
    "守护": "Guarded",
    "协同": "Ambush",
    # 本地独有列（RingsDB 无直接字段）
    "种族": "_derived_from_traits",
    "功能": None,
    "人数": None,
}

# RingsDB 独有字段（CSV 无对应列）
RINGSD_ONLY_FIELDS: tuple[str, ...] = (
    "code",  # 五位产品编号，如 01001
    "deck_limit",
    "quantity",
    "flavor",
    "illustrator",
    "has_errata",
    "url",
    "imagesrc",
    "victory",
)

# ---------------------------------------------------------------------------
# 枚举映射
# ---------------------------------------------------------------------------
TYPE_CSV_TO_RINGSD: dict[str, str] = {
    "英雄": "hero",
    "盟友": "ally",
    "附属": "attachment",
    "事件": "event",
    "任务": "player-side-quest",
    "约定": "contract",
}

TYPE_RINGSD_TO_CSV: dict[str, str] = {v: k for k, v in TYPE_CSV_TO_RINGSD.items()}

SPHERE_CSV_TO_RINGSD: dict[str, str] = {
    "领导": "leadership",
    "战术": "tactics",
    "精神": "spirit",
    "学识": "lore",
    "中立": "neutral",
    "远征": "fellowship",
    "巴金斯": "baggins",
}

SPHERE_RINGSD_TO_CSV: dict[str, str] = {v: k for k, v in SPHERE_CSV_TO_RINGSD.items()}

# CSV √ 列 → RingsDB text 内英文关键词
KEYWORD_COLUMNS: dict[str, str] = {
    "警戒": "Sentinel",
    "远攻": "Ranged",
    "限制": "Restricted",
    "隐匿": "Stealth",
    "厄运": "Doomed",
    "遭遇": "Surge",
    "守护": "Guarded",
    "协同": "Ambush",
}

# 系列中文名 → RingsDB pack_code
# 英文扩展包名参考 RingsDB 官方顺序；部分 CSV 系列顺序与官方不同，以卡牌内容校验为准
SERIES_TO_PACK_CODE: dict[str, str] = {
    "基础": "Core",
    "追捕咕噜": "HfG",
    "激战卡洛克": "CatC",
    "罗斯加堡之旅": "JtR",
    "艾明穆尔山丘": "HoEM",
    "死亡沼泽": "TDM",
    "重返幽暗密林": "RtM",
    "凯萨督姆": "KD",
    "红角隘口": "TRG",
    "瑞文戴尔之路": "RtR",
    "水中监视者": "WitW",
    "漫长的黑暗": "TLD",
    "大地的根基": "FoS",
    "暗影与烈焰": "SaF",
    "努曼诺尔的后裔": "HoN",
    "摄政王的恐惧": "TSF",
    "督伊顿森林": "TDF",
    "阿蒙丁山的遭遇": "EaAD",
    "强袭奥斯吉力亚斯": "AoO",
    "刚铎之血": "BoG",
    "魔窟谷": "TMV",
    "艾辛格之声": "VoI",
    "登兰德的陷阱": "TDT",
    "三场试炼": "TTT",
    "塔巴德的麻烦": "TiT",
    "天鹅沼泽": "NiE",
    "凯勒布理鹏的秘密": "CS",
    "鹿角王冠": "TAC",
    "失落的王国": "TLR",
    "伊利雅德荒原": "WoE",
    "逃离格兰山": "EfMG",
    "横穿伊顿荒原": "AtE",
    "鲁道尔的阴谋": "ToR",
    "卡恩督之战": "BoCD",
    "亡者国度": "TDR",
    "雾山奇缘": "OHaUH",
    "孤山秘境": "OtD",
    "黑骑士": "TBR",
    "前路黑暗": "TRD",
    "萨鲁曼的背叛": "ToS",
    "魔境之影": "LoS",
    "灰港岸": "TGH",
    "追击风暴召唤者": "FotS",
    "深海之物": "TitD",
    "欺诈者神庙": "TotD",
    "沉没的废墟": "DR",
    "科巴斯港的风暴": "SoCH",
    "海盗之城": "CoC",
    "西方之炎": "FotW",
    "哈拉德之沙": "TSoH",
    "猛犸巨象": "M",
    "哈拉德竞逐": "RAH",
    "沙土之下": "BtS",
    "黑蛇": "TBS",
    "西力斯古拉特地牢": "DoCG",
    "波罗斯渡口": "CoP",
    "末日火山": "MoF",
    "罗马尼安的荒野": "TWoR",
    "凋谢荒地": "TWH",
    "漫游罗马尼安": "RAR",
    "夜火": "FitN",
    "佛兰斯堡的鬼魂": "TGoF",
    "刚达巴山": "MG",
    "荒原的命运": "TFoW",
    "东方魔影": "ASitE",
    "愤怒和毁灭": "WaR",
    "乌法斯特之城": "TCoU",
    "战车民的挑战": "CotW",
    "灰烬山下": "UtAM",
    "悲伤之地": "TLoS",
    "诺恩要塞": "TFoN",
    "追捕无畏号": "THftD",
    "洛希尔人的誓言": "ALeP-MULTI",
}

# 系列中文名 → RingsDB 英文扩展包名（对照参考）
SERIES_TO_PACK_ENGLISH: dict[str, str] = {
    "基础": "Core Set",
    "追捕咕噜": "The Hunt for Gollum",
    "激战卡洛克": "Conflict at the Carrock",
    "罗斯加堡之旅": "A Journey to Rhosgobel",
    "艾明穆尔山丘": "The Hills of Emyn Muil",
    "死亡沼泽": "The Dead Marshes",
    "重返幽暗密林": "Return to Mirkwood",
    "凯萨督姆": "Khazad-dûm",
    "红角隘口": "The Redhorn Gate",
    "瑞文戴尔之路": "Road to Rivendell",
    "水中监视者": "The Watcher in the Water",
    "漫长的黑暗": "The Long Dark",
    "大地的根基": "Foundations of Stone",
    "暗影与烈焰": "Shadow and Flame",
    "努曼诺尔的后裔": "Heirs of Númenor",
    "摄政王的恐惧": "The Steward's Fear",
    "督伊顿森林": "The Drúadan Forest",
    "阿蒙丁山的遭遇": "Encounter at Amon Dîn",
    "强袭奥斯吉力亚斯": "Assault on Osgiliath",
    "刚铎之血": "The Blood of Gondor",
    "魔窟谷": "The Morgul Vale",
    "艾辛格之声": "The Voice of Isengard",
    "登兰德的陷阱": "The Dunland Trap",
    "三场试炼": "The Three Trials",
    "塔巴德的麻烦": "Trouble in Tharbad",
    "天鹅沼泽": "The Nîn-in-Eilph",
    "凯勒布理鹏的秘密": "Celebrimbor's Secret",
    "鹿角王冠": "The Antlered Crown",
    "失落的王国": "The Lost Realm",
    "伊利雅德荒原": "The Wastes of Eriador",
    "逃离格兰山": "Escape from Mount Gram",
    "横穿伊顿荒原": "Across the Ettenmoors",
    "鲁道尔的阴谋": "The Treachery of Rhudaur",
    "卡恩督之战": "The Battle of Carn Dûm",
    "亡者国度": "The Dread Realm",
    "雾山奇缘": "Over Hill and Under Hill",
    "孤山秘境": "On the Doorstep",
    "黑骑士": "The Black Riders",
    "前路黑暗": "The Road Darkens",
    "萨鲁曼的背叛": "The Treason of Saruman",
    "魔境之影": "The Land of Shadow",
    "灰港岸": "The Grey Havens",
    "追击风暴召唤者": "Flight of the Stormcaller",
    "深海之物": "The Thing in the Depths",
    "欺诈者神庙": "Temple of the Deceived",
    "沉没的废墟": "The Drowned Ruins",
    "科巴斯港的风暴": "A Storm on Cobas Haven",
    "海盗之城": "The City of Corsairs",
    "西方之炎": "The Flame of the West",
    "哈拉德之沙": "The Sands of Harad",
    "猛犸巨象": "The Mûmakil",
    "哈拉德竞逐": "Race Across Harad",
    "沙土之下": "Beneath the Sands",
    "黑蛇": "The Black Serpent",
    "西力斯古拉特地牢": "The Dungeons of Cirith Gurat",
    "波罗斯渡口": "The Crossings of Poros",
    "末日火山": "The Mountain of Fire",
    "罗马尼安的荒野": "The Wilds of Rhovanion",
    "凋谢荒地": "The Withered Heath",
    "漫游罗马尼安": "Roam Across Rhovanion",
    "夜火": "Fire in the Night",
    "佛兰斯堡的鬼魂": "The Ghost of Framsburg",
    "刚达巴山": "Mount Gundabad",
    "荒原的命运": "The Fate of Wilderland",
    "东方魔影": "A Shadow in the East",
    "愤怒和毁灭": "Wrath and Ruin",
    "乌法斯特之城": "The City of Ulfast",
    "战车民的挑战": "Challenge of the Wainriders",
    "灰烬山下": "Under the Ash Mountains",
    "悲伤之地": "The Land of Sorrow",
    "诺恩要塞": "The Fortress of Nurn",
    "追捕无畏号": "The Hunt for the Dreadnaught",
    "洛希尔人的誓言": "ALeP (fan aggregate)",
}

# 卡组列表常用简称 / 英文变体 → CSV「系列」列（须为魔戒玩家牌.csv 中存在的系列）
SERIES_EXTRA_ALIASES: dict[str, str] = {
  # 基础
  "核心": "基础",
  "核心系列": "基础",
  "Core": "基础",
  "Lord of the Rings: The Card Game": "基础",
  # 追捕咕噜
  "猎捕咕噜": "追捕咕噜",
  "狩猎咕噜": "追捕咕噜",
  # 激战卡洛克
  "激战卡尔岩": "激战卡洛克",
  "激战卡尔者": "激战卡洛克",
  "卡尔洛克": "激战卡洛克",
  # 罗斯加堡之旅
  "罗斯加堡": "罗斯加堡之旅",
  "罗斯戈贝尔之旅": "罗斯加堡之旅",
  "罗斯戈贝尔": "罗斯加堡之旅",
  "罗丝加堡之旅": "罗斯加堡之旅",
  "罗丝加堡": "罗斯加堡之旅",
  # 艾明穆尔山丘
  "艾明穆尔": "艾明穆尔山丘",
  "埃敏穆尔山丘": "艾明穆尔山丘",
  "埃敏穆伊丘陵": "艾明穆尔山丘",
  "埃敏穆伊": "艾明穆尔山丘",
  "埃明穆尔山丘": "艾明穆尔山丘",
  # 死亡沼泽
  "死沼泽": "死亡沼泽",
  # 重返幽暗密林
  "重返黑森林": "重返幽暗密林",
  "重返密林": "重返幽暗密林",
  # 凯萨督姆
  "卡萨督姆": "凯萨督姆",
  "卡扎督姆": "凯萨督姆",
  "Khazad-Dûm": "凯萨督姆",
  "Khazad-Dum": "凯萨督姆",
  # 努曼诺尔的后裔
  "努曼诺尔后裔": "努曼诺尔的后裔",
  "努曼诺尔之子": "努曼诺尔的后裔",
  # 摄政王的恐惧
  "摄政王恐惧": "摄政王的恐惧",
  # 督伊顿森林
  "德鲁阿丹森林": "督伊顿森林",
  # 阿蒙丁山的遭遇
  "阿蒙丁遭遇": "阿蒙丁山的遭遇",
  "阿姆迪恩遭遇": "阿蒙丁山的遭遇",
  # 强袭奥斯吉力亚斯
  "强袭奥斯吉利亚斯": "强袭奥斯吉力亚斯",
  # 艾辛格之声
  "艾辛格": "艾辛格之声",
  # 登兰德的陷阱
  "登兰德": "登兰德的陷阱",
  # 天鹅沼泽
  "宁因埃利夫": "天鹅沼泽",
  "尼宁伊勒夫": "天鹅沼泽",
  "The Nin-in-Eilph": "天鹅沼泽",
  # 凯勒布理鹏的秘密
  "凯勒布理鹏": "凯勒布理鹏的秘密",
  # 鹿角王冠 / 失落的王国 / 前路黑暗
  "鹿角": "鹿角王冠",
  "失落王国": "失落的王国",
  # 伊利雅德荒原
  "埃里阿多荒原": "伊利雅德荒原",
  # 雾山奇缘 / 孤山秘境
  "雾山": "雾山奇缘",
  "孤山": "孤山秘境",
  # 灰港岸
  "灰色港湾": "灰港岸",
  # 欺诈者神庙
  "受欺者神庙": "欺诈者神庙",
  # 沉没的废墟 / 科巴斯港的风暴 / 海盗之城
  "沉没废墟": "沉没的废墟",
  "科巴斯港": "科巴斯港的风暴",
  "海盗城": "海盗之城",
  # 西力斯古拉特地牢
  "西里斯古拉特": "西力斯古拉特地牢",
  "西力斯古拉特": "西力斯古拉特地牢",
  # 英文无变音 / 撇号变体
  "Encounter at Amon Din": "阿蒙丁山的遭遇",
  "The Battle of Carn Dum": "卡恩督之战",
  "Heirs of Numenor": "努曼诺尔的后裔",
  "The Drudan Forest": "督伊顿森林",
}

_SERIES_NIGHTMARE_RE = re.compile(r"\s+Nightmare Decks?\s*$", re.IGNORECASE)
_DIACRITIC_FOLD = str.maketrans(
    {
        "û": "u",
        "Û": "U",
        "ú": "u",
        "Ú": "U",
        "î": "i",
        "Î": "I",
        "é": "e",
        "É": "E",
        "ô": "o",
        "Ô": "O",
        "ñ": "n",
        "Ñ": "N",
    }
)


def normalize_series_alias_key(key: str) -> str:
    """统一撇号、连字符与空白，便于扩展包别名查找。"""
    s = (key or "").strip()
    s = (
        s.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return re.sub(r"\s+", " ", s).strip()


def fold_pack_english(name: str) -> str:
    """去掉 Nightmare 后缀并折叠英文变音字母。"""
    s = _SERIES_NIGHTMARE_RE.sub("", normalize_series_alias_key(name))
    return s.translate(_DIACRITIC_FOLD)


def _series_alias_variants(key: str) -> set[str]:
    variants: set[str] = set()
    if not key:
        return variants
    norm = normalize_series_alias_key(key)
    stripped = _SERIES_NIGHTMARE_RE.sub("", norm).strip()
    folded = fold_pack_english(key)
    for item in (key, norm, stripped, folded):
        if item:
            variants.add(item)
    if folded.isascii():
        variants.add(folded.lower())
    return variants


def build_series_aliases() -> dict[str, str]:
    """合并手动别名、RingsDB 英文名 / pack code 及常见英文变体。"""
    aliases: dict[str, str] = {}
    for alias, cn in SERIES_EXTRA_ALIASES.items():
        for variant in _series_alias_variants(alias):
            aliases.setdefault(variant, cn)
    for cn, en in SERIES_TO_PACK_ENGLISH.items():
        for variant in _series_alias_variants(en):
            aliases.setdefault(variant, cn)
        code = SERIES_TO_PACK_CODE.get(cn)
        if code:
            for variant in _series_alias_variants(code):
                aliases.setdefault(variant, cn)
    return aliases


# Card.from_csv_row 内部字段 ↔ CSV 列（玩家卡抽取.py）
CARD_DATACLASS_TO_CSV: dict[str, str] = {
    "id": "图片链接",
    "name": "卡牌名称",
    "Category": "类型",
    "Sphere": "派系",
    "Cost": "卡牌费用",
    "Threat": "初始威胁",
    "Willpower": "意志力",
    "Attack": "攻击力",
    "Defense": "防御力",
    "Health": "生命值",
    "type": "类型",
    "Text_Effect": "规则文字",
    "series": "系列",
    "unique": "独有",
    "restricted": "限制",
    "Ranged": "远攻",
    "Vigilant": "警戒",
}

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(KEYWORD_COLUMNS.values()) + r")\b", re.IGNORECASE
)


def has_keyword(text: str, keyword: str) -> bool:
    """检测 RingsDB text 是否含某关键词（忽略大小写）。"""
    return bool(re.search(rf"\b{re.escape(keyword)}\b", text or "", re.IGNORECASE))


def keywords_from_text(text: str) -> dict[str, bool]:
    """从 RingsDB text 提取关键词布尔值。"""
    found = {kw.lower() for kw in _KEYWORD_RE.findall(text or "")}
    return {col: en.lower() in found for col, en in KEYWORD_COLUMNS.items()}


def keywords_to_csv_flags(keywords: dict[str, bool]) -> dict[str, str]:
    """关键词布尔值 → CSV √ 列。"""
    return {col: "√" if keywords.get(col) else "" for col in KEYWORD_COLUMNS}


def unique_csv_to_ringsdb(value: str) -> bool:
    return (value or "").strip() == "*"


def unique_ringsdb_to_csv(is_unique: bool) -> str:
    return "*" if is_unique else ""


def infer_series_to_pack_code(
    csv_rows: list[dict[str, str]],
    ringsdb_cards: list[dict[str, Any]],
) -> dict[str, str]:
    """从 octgnid 匹配结果推断 系列→pack_code 映射，合并内置表。"""
    by_uuid = {c["octgnid"]: c for c in ringsdb_cards if c.get("octgnid")}
    mapping = dict(SERIES_TO_PACK_CODE)
    for row in csv_rows:
        uid = (row.get("图片链接") or "").strip()
        series = (row.get("系列") or "").strip()
        if uid and series and uid in by_uuid:
            mapping[series] = by_uuid[uid]["pack_code"]
    return mapping


def ringsdb_card_to_csv_row(
    card: dict[str, Any],
    series: str = "",
) -> dict[str, str]:
    """将 RingsDB 卡牌 JSON 转为 CSV 行字典（中文系列名需外部传入）。"""
    text = card.get("text") or ""
    kw = keywords_from_text(text)

    row: dict[str, str] = {
        "系列": series or card.get("pack_name") or "",
        "编号": str(card.get("position") or ""),
        "派系": SPHERE_RINGSD_TO_CSV.get(card.get("sphere_code") or "", card.get("sphere_name") or ""),
        "卡牌名称": card.get("name") or "",
        "图片链接": card.get("octgnid") or "",
        "类型": TYPE_RINGSD_TO_CSV.get(card.get("type_code") or "", card.get("type_name") or ""),
        "独有": unique_ringsdb_to_csv(bool(card.get("is_unique"))),
        "卡牌费用": str(card.get("cost") or ""),
        "初始威胁": str(card.get("threat") or ""),
        "意志力": str(card.get("willpower") or ""),
        "攻击力": str(card.get("attack") or ""),
        "防御力": str(card.get("defense") or ""),
        "生命值": str(card.get("health") or ""),
        "任务点": str(card.get("quest") or ""),
        "属性": (card.get("traits") or "").replace(". ", "."),
        "规则文字": text,
        "种族": "",
        "功能": "",
        "人数": "",
    }
    row.update(keywords_to_csv_flags(kw))
    return row


def csv_row_to_ringsdb_fields(row: dict[str, str]) -> dict[str, Any]:
    """将 CSV 行转为 RingsDB 字段子集（不含 pack_code，需查 SERIES_TO_PACK_CODE）。"""
    text = (row.get("规则文字") or "").strip()
    type_csv = (row.get("类型") or "").strip()
    sphere_csv = (row.get("派系") or "").strip()

    return {
        "pack_code": SERIES_TO_PACK_CODE.get((row.get("系列") or "").strip(), ""),
        "position": int(row["编号"]) if (row.get("编号") or "").isdigit() else None,
        "sphere_code": SPHERE_CSV_TO_RINGSD.get(sphere_csv, sphere_csv),
        "sphere_name": sphere_csv,
        "name": (row.get("卡牌名称") or "").strip(),
        "octgnid": (row.get("图片链接") or "").strip(),
        "type_code": TYPE_CSV_TO_RINGSD.get(type_csv, type_csv),
        "type_name": type_csv,
        "is_unique": unique_csv_to_ringsdb(row.get("独有") or ""),
        "cost": row.get("卡牌费用") or None,
        "threat": _int_or_none(row.get("初始威胁")),
        "willpower": _int_or_none(row.get("意志力")),
        "attack": _int_or_none(row.get("攻击力")),
        "defense": _int_or_none(row.get("防御力")),
        "health": _int_or_none(row.get("生命值")),
        "quest": _int_or_none(row.get("任务点")),
        "traits": (row.get("属性") or "").strip(),
        "text": text,
        "keywords": {col: row.get(col) == "√" for col in KEYWORD_COLUMNS},
    }


def match_ringsdb_card(
    csv_row: dict[str, str],
    ringsdb_cards: list[dict[str, Any]],
    series_to_pack: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """在 RingsDB 卡牌列表中查找与 CSV 行对应的卡牌。"""
    series_to_pack = series_to_pack or SERIES_TO_PACK_CODE
    by_uuid = {c["octgnid"]: c for c in ringsdb_cards if c.get("octgnid")}
    by_pack_pos: dict[tuple[str, int], dict[str, Any]] = {}
    for c in ringsdb_cards:
        pack = c.get("pack_code")
        pos = c.get("position")
        if pack and isinstance(pos, int):
            by_pack_pos[(pack, pos)] = c

    uid = (csv_row.get("图片链接") or "").strip()
    if uid and uid in by_uuid:
        return by_uuid[uid]

    series = (csv_row.get("系列") or "").strip()
    pack = series_to_pack.get(series)
    if pack == "ALeP-MULTI":
        return None
    num = (csv_row.get("编号") or "").strip()
    if pack and num.isdigit():
        return by_pack_pos.get((pack, int(num)))

    return None


def field_mapping_reference() -> dict[str, Any]:
    """返回完整字段映射参考（供 MCP / 文档使用）。"""
    return {
        "join_keys": {
            "primary": {CSV_PRIMARY_KEY: RINGSD_PRIMARY_KEY},
            "secondary": dict(zip(CSV_SECONDARY_KEYS, RINGSD_SECONDARY_KEYS)),
            "note": "系列需先映射为 pack_code；基础→Core 已内置，其余系列可通过 octgnid 推断",
        },
        "csv_to_ringsdb": CSV_TO_RINGSD,
        "ringsdb_only": list(RINGSD_ONLY_FIELDS),
        "csv_only": ["种族", "功能", "人数"],
        "type_map": TYPE_CSV_TO_RINGSD,
        "sphere_map": SPHERE_CSV_TO_RINGSD,
        "keyword_columns": KEYWORD_COLUMNS,
        "card_dataclass_to_csv": CARD_DATACLASS_TO_CSV,
        "series_to_pack_code": SERIES_TO_PACK_CODE,
        "series_to_pack_english": SERIES_TO_PACK_ENGLISH,
        "notes": [
            "卡牌名称、属性、规则文字：CSV 为中文，RingsDB 为英文，无法自动互译",
            "关键词：CSV 用 √ 列，RingsDB 嵌入 text 首行（Sentinel. / Ranged. 等）",
            "独有：CSV 用 *，RingsDB 用 is_unique: true",
            "图片链接/octgnid：与 Card.id 及 OCTGN 资源 UUID 一致",
            "RingsDB 含 treasure/player-objective 类型，本地 CSV 暂无对应",
            "洛希尔人的誓言：本地 91 张 ALeP 合集，RingsDB 无单一 pack，代码 ALeP-MULTI",
            "CSV 系列顺序与官方英文顺序在 Hobbit/LOTR 段及哈拉德段有错位，映射以卡牌校验为准",
        ],
    }


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None
