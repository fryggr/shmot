"""Генератор ДЕМОНСТРАЦИОННЫХ YML-фидов. Все бренды, магазины и домены вымышлены
(домены зоны .example зарезервированы и не принадлежат реальным магазинам).

Запуск: python tests/fixtures/generate_demo_feeds.py  — перезаписывает tests/fixtures/feeds/*.yml.
Генерация детерминирована, результат хранится в репозитории.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).parent / "feeds"

CATEGORIES = [
    ("1", None, "Женщинам"),
    ("2", "1", "Верхняя одежда"),
    ("3", "2", "Куртки"),
    ("4", "2", "Пальто"),
    ("5", "2", "Тренчи"),
    ("6", "2", "Бомберы"),
    ("7", "1", "Платья"),
    ("8", "1", "Юбки"),
    ("9", "1", "Обувь"),
    ("10", "9", "Лоферы"),
    ("11", "9", "Кроссовки"),
    ("12", "9", "Ботинки"),
    ("13", "1", "Сумки"),
    ("14", "1", "Джинсы"),
    ("15", "1", "Трикотаж"),
    ("16", "1", "Футболки"),
    ("17", "1", "Рубашки"),
    ("18", "1", "Брюки"),
    ("19", "1", "Топы"),
    ("99", "1", "Разное"),
]

# sizes: list of (label, price, available) — available: True / False / None (не передано)
INT = "INT"


def m(gid, name, vendor, code, colorway, color, cat, material, composition, sizes, unit=INT,
      gender="женский", desc="", oldprice=None, fit=None):
    return dict(gid=gid, name=name, vendor=vendor, code=code, colorway=colorway, color=color, cat=cat,
                material=material, composition=composition, sizes=sizes, unit=unit, gender=gender,
                desc=desc, oldprice=oldprice, fit=fit)


SHOP_A = [
    m("A-JKT-01", "Куртка из натуральной замши оверсайз", "Demo Atelier", "DA-J100", "BRN", "коричневый", "3",
      "натуральная замша", "100% замша", [("S", 27990, True), ("M", 27990, True), ("L", 27990, False)],
      desc="Свободная куртка из мягкой замши.", oldprice=34990, fit="оверсайз"),
    m("A-JKT-02", "Куртка под замшу укороченная", "Urban Demo", "UD-J210", "BRN", "коричневый", "3",
      "искусственная замша", "100% полиэстер", [("S", 8990, True), ("M", 8990, True), ("L", 8990, True)]),
    m("A-JKT-03", "Куртка-рубашка из экокожи", "Urban Demo", "UD-J220", "BLK", "черный", "3",
      "экокожа", "100% полиуретан", [("XS", 11990, True), ("S", 11990, True), ("M", 11990, False)]),
    # Цена и размер в разных вариантах: дешёвый S, дорогой M
    m("A-JKT-04", "Кожаная куртка косуха", "Demo Classic", "DC-J300", "BLK", "черный", "3",
      "натуральная кожа", "100% кожа", [("S", 25000, True), ("M", 35000, True), ("L", 24000, False)]),
    m("A-COAT-01", "Пальто из шерсти миди", "Demo Classic", "DC-C100", "GRY", "серый", "4",
      "шерсть", "80% шерсть, 20% полиамид", [("S", 32990, True), ("M", 32990, True), ("L", 32990, True)]),
    m("A-COAT-02", "Пальто прямого кроя без шерсти", "Urban Demo", "UD-C110", "BEI", "бежевый", "4",
      "полиэстер", "70% полиэстер, 30% вискоза", [("S", 14990, True), ("M", 14990, None), ("L", 14990, None)]),
    m("A-TR-01", "Тренч классический", "Demo Atelier", "DA-T100", "BEI", "бежевый", "5",
      "хлопок", "100% хлопок", [("S", 19990, True), ("M", 19990, True)]),
    m("A-BMB-01", "Бомбер оверсайз как мужской", "Urban Demo", "UD-B100", "GRN", "хаки", "6",
      "нейлон", "100% нейлон", [("M", 9990, True), ("L", 9990, True)], fit="оверсайз"),
    m("A-DR-01", "Платье белое миди из льна", "Demo Atelier", "DA-D100", "WHT", "белый", "7",
      "лен", "100% лен", [("XS", 12990, True), ("S", 12990, True), ("M", 12990, True)]),
    m("A-DR-02", "Свадебное платье белое в пол", "Demo Atelier", "DA-D900", "WHT", "белый", "7",
      "шелк", "100% шелк", [("S", 59990, True), ("M", 59990, True)], desc="Свадебная коллекция."),
    m("A-DR-03", "Платье миди для торжества", "Demo Classic", "DC-D200", "BLU", "темно-синий", "7",
      "вискоза", "100% вискоза", [("S", 15990, True), ("M", 15990, True), ("L", 15990, True)],
      desc="Подойдёт для свадьбы подруги или вечернего мероприятия."),
    m("A-SK-01", "Юбка миди из шерсти", "Demo Classic", "DC-S100", "GRY", "серый меланж", "8",
      "шерсть", "60% шерсть, 40% полиэстер", [("S", 12490, True), ("M", 12490, True), ("L", 12490, None)]),
    m("A-LOF-01", "Лоферы кожаные", "Nord Demo", "ND-L100", "BLK", "черный", "10",
      "натуральная кожа", "100% кожа", [("37", 18990, True), ("38", 25000, True), ("39", 18990, True)], unit="EU"),
    m("A-SNK-01", "Кроссовки белые базовые", "Nord Demo", "ND-S200", "WHT", "белый", "11",
      "натуральная кожа", "кожа", [("37", 10990, True), ("38", 10990, True), ("39", 10990, False)], unit="EU"),
    m("A-BAG-01", "Сумка бордовая через плечо", "Demo Atelier", "DA-B100", "BRD", "бордовый", "13",
      "натуральная кожа", "100% кожа", [(None, 17990, True)]),
    m("A-JN-01", "Джинсы широкие удлинённые", "Urban Demo", "UD-JN100", "BLU", "голубой", "14",
      "хлопок", "99% хлопок, 1% эластан", [("S", 6990, True), ("M", 6990, True), ("L", 6990, True)],
      desc="Удлинённая модель, длина по внутреннему шву 84 см."),
    m("A-KN-01", "Свитер из кашемира", "Demo Classic", "DC-K100", "BEI", "кэмел", "15",
      "кашемир", "100% кашемир", [("S", 21990, True), ("M", 21990, True)]),
    # Пропадает во втором снимке магазина A
    m("A-SH-01", "Рубашка оверсайз в полоску", "Urban Demo", "UD-SH100", "BLU", "синий", "17",
      "хлопок", "100% хлопок", [("S", 4990, True), ("M", 4990, True), ("L", 4990, True)]),
]

# Одна group_id с двумя цветами — разбивается на две карточки «модель в цвете»
SHOP_A_MULTICOLOR = [
    m("A-TS-01", "Футболка базовая", "Urban Demo", "UD-TS100", "BLK", "черный", "16",
      "хлопок", "100% хлопок", [("S", 1990, True), ("M", 1990, True)]),
    m("A-TS-01", "Футболка базовая", "Urban Demo", "UD-TS100", "WHT", "белый", "16",
      "хлопок", "100% хлопок", [("S", 1990, True), ("M", 1990, None)]),
]

SHOP_B = [
    # Совпадает с A-LOF-01 по бренд + код модели + колорвей → общая карточка, два продавца
    m("B-77001", "Лоферы из кожи Nord Demo", "Nord Demo", "ND-L100", "BLK", "чёрный", "10",
      "кожа", "100% натуральная кожа", [("38", 15000, False), ("39", 15000, True), ("40", 15000, True)], unit="EU"),
    # Та же модель, другой цвет — не объединять с чёрными
    m("B-77002", "Лоферы из кожи Nord Demo", "Nord Demo", "ND-L100", "BRN", "коричневый", "10",
      "кожа", "100% натуральная кожа", [("38", 15500, True), ("39", 15500, True)], unit="EU"),
    # То же название, что у A-JKT-01, но другая модель — не объединять
    m("B-77003", "Куртка из натуральной замши оверсайз", "Demo Atelier", "DA-J777", "BRN", "коричневый", "3",
      "замша", "100% замша", [("44", 31990, True), ("46", 31990, True), ("48", 31990, None)], unit="RU"),
    m("B-77004", "Куртка пуховая", "Nord Demo", "ND-P100", "BLK", "черный", "3",
      "полиэстер", "100% полиэстер", [("46", 19990, True), ("48", 19990, True), ("50", 19990, True)], unit="RU",
      gender="мужской"),
    m("B-77005", "Платье-рубашка белое", "Demo Classic", "DC-D300", "WHT", "белый", "7",
      "хлопок", "100% хлопок", [("42", 9990, True), ("44", 9990, True), ("46", 9990, None)], unit="RU"),
    m("B-77006", "Юбка плиссе миди", "Demo Classic", "DC-S200", "GRY", "серый", "8",
      "полиэстер", "100% полиэстер", [("42", 5990, True), ("44", 5990, True)], unit="RU"),
    m("B-77007", "Ботинки челси замшевые", "Nord Demo", "ND-B300", "BRN", "коричневый", "12",
      "замша", "100% замша", [("37", 16990, True), ("38", 16990, True), ("39", 16990, True)], unit="RU"),
    m("B-77008", "Сумка-тоут бордо", "Urban Demo", "UD-BG200", "BRD", "бордо", "13",
      "экокожа", "100% полиуретан", [(None, 5990, None)]),
    m("B-77009", "Джинсы прямые", "Urban Demo", "UD-JN200", "BLU", "синий", "14",
      "хлопок", "100% хлопок", [("44", 5490, True), ("46", 5490, True), ("48", 5490, True)], unit="RU"),
    m("B-77010", "Брюки широкие из шерсти", "Demo Classic", "DC-TR100", "BLK", "черный", "18",
      "шерсть", "50% шерсть, 50% полиэстер", [("42", 13990, True), ("44", 13990, True)], unit="RU"),
    # Размер без указанной системы → UNKNOWN; наличие не передано
    m("B-77011", "Кардиган крупной вязки", "Nord Demo", "ND-K200", "PNK", "розовый", "15",
      None, None, [("44", 7990, None), ("46", 7990, None)], unit=None),
    m("B-77013", "Топ на тонких бретелях", "Demo Classic", "DC-TP100", "BLK", "черный", "19",
      "вискоза", "95% вискоза, 5% эластан", [("42", 2990, True), ("44", 2990, True)], unit="RU"),
    m("B-77012", "Пальто-кокон", "Demo Atelier", "DA-C300", "BEI", "песочный", "4",
      "шерсть", "100% шерсть", [("42", 39990, True), ("44", 39990, True)], unit="RU", oldprice=39990),
]


def offer_xml(shop_host, model, size, price, available, idx, *, oid=None, extra=None, price_text=None,
              url=None, currency="RUR", picture=True):
    sku = oid or f"{model['gid']}-{model['colorway']}-{size or 'OS'}"
    attrs = [f'id="{escape(sku)}"', f'group_id="{escape(model["gid"])}"']
    if available is not None:
        attrs.append(f'available="{"true" if available else "false"}"')
    lines = [f"      <offer {' '.join(attrs)}>"]
    gid = model["gid"].lower()
    lines.append(f"        <url>{escape(url or f'https://{shop_host}/p/{gid}?sku={sku}')}</url>")
    lines.append(f"        <price>{price_text if price_text is not None else f'{price}.00'}</price>")
    if model.get("oldprice"):
        lines.append(f"        <oldprice>{model['oldprice']}.00</oldprice>")
    lines.append(f"        <currencyId>{currency}</currencyId>")
    lines.append(f"        <categoryId>{model['cat']}</categoryId>")
    if picture:
        cw = model["colorway"].lower()
        for n in (1, 2):
            lines.append(f"        <picture>https://{shop_host}/img/{gid}-{cw}-{n}.jpg</picture>")
    lines.append(f"        <name>{escape(model['name'])}</name>")
    lines.append(f"        <vendor>{escape(model['vendor'])}</vendor>")
    lines.append(f"        <vendorCode>{escape(model['code'])}</vendorCode>")
    if model.get("desc"):
        lines.append(f"        <description>{escape(model['desc'])}</description>")
    if size is not None:
        unit = f' unit="{model["unit"]}"' if model.get("unit") else ""
        lines.append(f'        <param name="Размер"{unit}>{escape(size)}</param>')
    lines.append(f'        <param name="Цвет">{escape(model["color"])}</param>')
    lines.append(f'        <param name="Код цвета">{escape(model["colorway"])}</param>')
    if model.get("material"):
        lines.append(f'        <param name="Материал">{escape(model["material"])}</param>')
    if model.get("composition"):
        lines.append(f'        <param name="Состав">{escape(model["composition"])}</param>')
    if model.get("fit"):
        lines.append(f'        <param name="Посадка">{escape(model["fit"])}</param>')
    lines.append(f'        <param name="Пол">{escape(model["gender"])}</param>')
    for line in extra or []:
        lines.append(f"        {line}")
    lines.append("      </offer>")
    return "\n".join(lines)


def feed(shop_name, shop_host, models, broken, date, *, override=None):
    override = override or {}
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ: вымышленный магазин, бренды и товары. -->",
        f'<yml_catalog date="{date}">',
        "  <shop>",
        f"    <name>{escape(shop_name)}</name>",
        f"    <company>{escape(shop_name)} (demo)</company>",
        f"    <url>https://{shop_host}/</url>",
        '    <currencies><currency id="RUR" rate="1"/></currencies>',
        "    <categories>",
    ]
    for cid, parent, title in CATEGORIES:
        p = f' parentId="{parent}"' if parent else ""
        out.append(f'      <category id="{cid}"{p}>{escape(title)}</category>')
    out += ["    </categories>", "    <offers>"]
    idx = 0
    for model in models:
        for size, price, available in model["sizes"]:
            key = (model["gid"], model["colorway"], size)
            p, a = override.get(key, (price, available))
            out.append(offer_xml(shop_host, model, size, p, a, idx))
            idx += 1
    out.extend(broken)
    out += ["    </offers>", "  </shop>", "</yml_catalog>", ""]
    return "\n".join(out)


INT_ORDER = ("XS", "S", "M", "L", "XL")
# У этой модели специально разные цены у размеров — не расширяем её размерную сетку
KEEP_SIZES = {"A-JKT-04"}


def expand_int_sizes(models):
    """Дополняет размерную сетку INT-моделей магазина A до XS..XL (та же цена, в наличии)."""
    out = []
    for model in models:
        if model["unit"] != INT or model["gid"] in KEEP_SIZES or model["sizes"][0][0] is None:
            out.append(model)
            continue
        have = {s[0]: s for s in model["sizes"]}
        price = model["sizes"][0][1]
        sizes = [have.get(label, (label, price, True)) for label in INT_ORDER]
        out.append(model | {"sizes": sizes})
    return out


def broken_a(host):
    junk = SHOP_A[0] | {"gid": "A-BROKEN", "colorway": "X", "desc": None, "oldprice": None}
    return [
        # нет изображения
        offer_xml(host, junk, "S", 1000, True, 0, oid="A-BROKEN-PIC", picture=False),
        # отрицательная цена
        offer_xml(host, junk, "M", 0, True, 0, oid="A-BROKEN-PRICE", price_text="-100"),
        # ссылка на чужой домен
        offer_xml(host, junk, "M", 1000, True, 0, oid="A-BROKEN-URL", url="https://evil.example/phish"),
        # дубль SKU существующего варианта
        offer_xml(host, SHOP_A[1], "S", 8990, True, 0, oid="A-JKT-02-BRN-S"),
    ]


def broken_b(host):
    junk = SHOP_B[3] | {"gid": "B-BROKEN", "colorway": "X", "oldprice": None}
    return [
        # валюта вне allowlist
        offer_xml(host, junk, "44", 1000, True, 0, oid="B-BROKEN-CUR", currency="USD"),
    ]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    a_host, b_host = "demo-shop-a.example", "demo-shop-b.example"
    a_models = expand_int_sizes(SHOP_A + SHOP_A_MULTICOLOR)
    (OUT / "demo_shop_a_v1.yml").write_text(
        feed("Demo Shop A", a_host, a_models, broken_a(a_host), "2026-09-29 10:00"), encoding="utf-8")
    # v2: рубашка A-SH-01 пропала; тренч подешевел; у кроссовок закончился 38
    v2_models = [x for x in a_models if x["gid"] != "A-SH-01"]
    (OUT / "demo_shop_a_v2.yml").write_text(
        feed("Demo Shop A", a_host, v2_models, broken_a(a_host), "2026-09-30 10:00",
             override={("A-TR-01", "BEI", "S"): (17990, True), ("A-TR-01", "BEI", "M"): (17990, True),
                       ("A-SNK-01", "WHT", "38"): (10990, False)}),
        encoding="utf-8")
    (OUT / "demo_shop_b_v1.yml").write_text(
        feed("Demo Shop B", b_host, SHOP_B, broken_b(b_host), "2026-09-29 12:00"), encoding="utf-8")

    (OUT / "broken_xxe.yml").write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE yml_catalog [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<yml_catalog><shop><offers><offer id="1"><name>&xxe;</name></offer></offers></shop></yml_catalog>\n',
        encoding="utf-8")
    (OUT / "broken_billion_laughs.yml").write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>\n'
        "<yml_catalog><shop><offers/></shop></yml_catalog>\n", encoding="utf-8")
    full = (OUT / "demo_shop_a_v1.yml").read_text(encoding="utf-8")
    (OUT / "broken_truncated.yml").write_text(full[: len(full) // 2], encoding="utf-8")
    (OUT / "empty_offers.yml").write_text(
        feed("Demo Shop A", a_host, [], [], "2026-09-30 11:00"), encoding="utf-8")
    colorways = {(x["vendor"], x["code"], x["colorway"]) for x in a_models + SHOP_B}
    codes = {(x["vendor"], x["code"]) for x in a_models + SHOP_B}
    print(f"models: {len(codes)}, model-in-color: {len(colorways)}")


if __name__ == "__main__":
    main()
