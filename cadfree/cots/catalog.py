"""Street-typical FPV / multirotor COTS envelopes.

Prices and stock are **not** live vendor inventory. The agent may search the
web for a shop page; it must not claim a motor is in stock because a snippet
said so. Confirm price and stock on the vendor site before buying.
"""

from __future__ import annotations

from typing import Any

PRICE_NOTE = (
    "Street-typical USD for a hobby quad, not live inventory. "
    "Confirm stock and the cart total on the vendor page."
)

HOBBY_VENDOR_DOMAINS = (
    "getfpv.com",
    "racedayquads.com",
    "shop.iflight.com",
    "store.tmotor.com",
    "hobbyking.com",
    "betafpv.com",
    "newbeedrone.com",
    "rotorriot.com",
    "caddxfpv.com",
    "pyrodrone.com",
    "happymodel.cn.com",
    "radiomasterrc.com",
)


def _item(
    *,
    id: str,
    role: str,
    name: str,
    price_usd: float,
    class_ids: tuple[str, ...],
    mass_g: float,
    envelope_mm: dict[str, float],
    vendor: str = "hobby typical",
    url: str = "",
    mount: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": id,
        "role": role,
        "name": name,
        "price_usd": float(price_usd),
        "price_note": PRICE_NOTE,
        "in_stock_claim": False,
        "class_ids": list(class_ids),
        "mass_g": float(mass_g),
        "envelope_mm": dict(envelope_mm),
        "vendor": vendor,
        "url": url,
        "mount": mount or {},
    }
    if extra:
        row.update(extra)
    return row


# Opposite-motor spacing is arm_mm. Speed bands are what a competent build
# actually flies, not the theoretical prop tip speed.
CLASSES: list[dict[str, Any]] = [
    {
        "id": "whoop_65",
        "label": "65 mm tiny whoop",
        "n_rotors": 4,
        "speed_mph": (12, 25),
        "cost_floor_usd": 40,
        "cost_typical_usd": 70,
        "auw_g": 32,
        "payload_g": 0,
        "range_km": (0.15, 0.6),
        "flight_min": (3, 6),
        "prop_in": 1.2,
        "arm_mm": 46.0,
        "cd_a_m2": 0.008,
        "tw_typical": 3.0,
        "hub_mm": 28.0,
        "arm_w_mm": 8.0,
        "arm_h_mm": 4.0,
    },
    {
        "id": "three_inch",
        "label": "3-inch cinewhoop / toothpick",
        "n_rotors": 4,
        "speed_mph": (20, 45),
        "cost_floor_usd": 120,
        "cost_typical_usd": 160,
        "auw_g": 180,
        "payload_g": 40,
        "range_km": (0.5, 2.0),
        "flight_min": (5, 9),
        "prop_in": 3.0,
        "arm_mm": 140.0,
        "cd_a_m2": 0.016,
        "tw_typical": 4.0,
        "hub_mm": 36.0,
        "arm_w_mm": 12.0,
        "arm_h_mm": 5.0,
    },
    {
        "id": "five_inch",
        "label": "5-inch freestyle / sport quad",
        "n_rotors": 4,
        "speed_mph": (40, 90),
        "cost_floor_usd": 200,
        "cost_typical_usd": 250,
        "auw_g": 550,
        "payload_g": 120,
        "range_km": (1.0, 6.0),
        "flight_min": (5, 12),
        "prop_in": 5.0,
        "arm_mm": 220.0,
        "cd_a_m2": 0.025,
        "tw_typical": 5.0,
        "hub_mm": 42.0,
        "arm_w_mm": 14.0,
        "arm_h_mm": 6.0,
    },
    {
        "id": "seven_inch",
        "label": "7-inch long-range quad",
        "n_rotors": 4,
        "speed_mph": (28, 50),
        "cost_floor_usd": 280,
        "cost_typical_usd": 340,
        "auw_g": 750,
        "payload_g": 200,
        "range_km": (4.0, 18.0),
        "flight_min": (12, 28),
        "prop_in": 7.0,
        "arm_mm": 295.0,
        "cd_a_m2": 0.032,
        "tw_typical": 3.5,
        "hub_mm": 46.0,
        "arm_w_mm": 16.0,
        "arm_h_mm": 7.0,
    },
]

CLASS_BY_ID = {c["id"]: c for c in CLASSES}

KIND_ALIAS = {
    "whoop": "whoop_65",
    "whoop_65": "whoop_65",
    "tiny whoop": "whoop_65",
    "65mm": "whoop_65",
    "3-inch": "three_inch",
    "3 inch": "three_inch",
    "three_inch": "three_inch",
    "cinewhoop": "three_inch",
    "5-inch": "five_inch",
    "5 inch": "five_inch",
    "five_inch": "five_inch",
    "freestyle": "five_inch",
    "quadcopter": "",
    "quad": "",
    "drone": "",
    "not sure": "",
    "long-range": "seven_inch",
    "long range": "seven_inch",
    "seven_inch": "seven_inch",
    "7-inch": "seven_inch",
    "7 inch": "seven_inch",
}

ITEMS: list[dict[str, Any]] = [
    _item(
        id="motor_0802_19000",
        role="motor",
        name="0802 19000KV class (whoop)",
        price_usd=7.5,
        class_ids=("whoop_65",),
        mass_g=2.6,
        envelope_mm={"l": 10, "w": 10, "h": 12},
        vendor="BetaFPV / Happymodel class",
        url="https://betafpv.com/",
        mount={"pcd_mm": 6.6, "hole_mm": 1.4, "count": 4},
        extra={"kv": 19000, "thrust_g": 45, "cells": "1S"},
    ),
    _item(
        id="motor_1404_4000",
        role="motor",
        name="1404 4000KV class (3-inch)",
        price_usd=13.0,
        class_ids=("three_inch",),
        mass_g=12.0,
        envelope_mm={"l": 18, "w": 18, "h": 16},
        vendor="T-Motor / iFlight class",
        url="https://store.tmotor.com/",
        mount={"pcd_mm": 9.0, "hole_mm": 2.0, "count": 4},
        extra={"kv": 4000, "thrust_g": 380, "cells": "4S"},
    ),
    _item(
        id="motor_2207_1750",
        role="motor",
        name="2207 1750KV class (5-inch)",
        price_usd=18.0,
        class_ids=("five_inch",),
        mass_g=32.0,
        envelope_mm={"l": 28, "w": 28, "h": 18},
        vendor="T-Motor / iFlight class",
        url="https://store.tmotor.com/",
        mount={"pcd_mm": 16.0, "hole_mm": 3.0, "count": 4},
        extra={"kv": 1750, "thrust_g": 1200, "cells": "6S"},
    ),
    _item(
        id="motor_2806_1300",
        role="motor",
        name="2806 1300KV class (7-inch)",
        price_usd=22.0,
        class_ids=("seven_inch",),
        mass_g=48.0,
        envelope_mm={"l": 32, "w": 32, "h": 22},
        vendor="T-Motor class",
        url="https://store.tmotor.com/",
        mount={"pcd_mm": 19.0, "hole_mm": 3.0, "count": 4},
        extra={"kv": 1300, "thrust_g": 1600, "cells": "6S"},
    ),
    _item(
        id="prop_31mm",
        role="prop",
        name="31 mm whoop prop (pair typical)",
        price_usd=1.0,
        class_ids=("whoop_65",),
        mass_g=0.4,
        envelope_mm={"l": 31, "w": 31, "h": 6},
        vendor="Gemfan class",
        url="https://www.getfpv.com/",
        extra={"diameter_in": 1.22},
    ),
    _item(
        id="prop_3in",
        role="prop",
        name="3-inch tri-blade prop",
        price_usd=2.0,
        class_ids=("three_inch",),
        mass_g=2.2,
        envelope_mm={"l": 76, "w": 76, "h": 8},
        vendor="Gemfan class",
        url="https://www.getfpv.com/",
        extra={"diameter_in": 3.0},
    ),
    _item(
        id="prop_5in",
        role="prop",
        name="5×4.3 tri-blade prop",
        price_usd=2.5,
        class_ids=("five_inch",),
        mass_g=4.5,
        envelope_mm={"l": 127, "w": 127, "h": 10},
        vendor="HQProp / Gemfan class",
        url="https://www.getfpv.com/",
        extra={"diameter_in": 5.0},
    ),
    _item(
        id="prop_7in",
        role="prop",
        name="7-inch long-range prop",
        price_usd=3.5,
        class_ids=("seven_inch",),
        mass_g=7.0,
        envelope_mm={"l": 178, "w": 178, "h": 12},
        vendor="HQProp class",
        url="https://www.getfpv.com/",
        extra={"diameter_in": 7.0},
    ),
    _item(
        id="batt_1s300",
        role="battery",
        name="1S 300 mAh 30C (whoop)",
        price_usd=6.0,
        class_ids=("whoop_65",),
        mass_g=8.5,
        envelope_mm={"l": 45, "w": 12, "h": 8},
        vendor="Tattu / BetaFPV class",
        url="https://www.getfpv.com/",
        extra={"cells": 1, "mah": 300},
    ),
    _item(
        id="batt_4s650",
        role="battery",
        name="4S 650 mAh 75C (3-inch)",
        price_usd=18.0,
        class_ids=("three_inch",),
        mass_g=72.0,
        envelope_mm={"l": 60, "w": 30, "h": 22},
        vendor="Tattu class",
        url="https://www.getfpv.com/",
        extra={"cells": 4, "mah": 650},
    ),
    _item(
        id="batt_4s1500",
        role="battery",
        name="4S 1500 mAh 75C (5-inch)",
        price_usd=28.0,
        class_ids=("five_inch",),
        mass_g=185.0,
        envelope_mm={"l": 72, "w": 36, "h": 28},
        vendor="Tattu / CNHL class",
        url="https://www.getfpv.com/",
        extra={"cells": 4, "mah": 1500},
    ),
    _item(
        id="batt_6s1050",
        role="battery",
        name="6S 1050 mAh 120C (7-inch / long-range)",
        price_usd=36.0,
        class_ids=("seven_inch", "five_inch"),
        mass_g=175.0,
        envelope_mm={"l": 85, "w": 35, "h": 32},
        vendor="Tattu class",
        url="https://www.getfpv.com/",
        extra={"cells": 6, "mah": 1050},
    ),
    _item(
        id="fc_aio_whoop",
        role="fc",
        name="1S AIO FC+ESC (25.5 mm whoop)",
        price_usd=28.0,
        class_ids=("whoop_65",),
        mass_g=4.5,
        envelope_mm={"l": 29, "w": 29, "h": 8},
        vendor="BetaFPV / Happymodel class",
        url="https://betafpv.com/",
        mount={"pcd_mm": 25.5, "hole_mm": 2.0, "count": 4},
        extra={"stack": "aio"},
    ),
    _item(
        id="fc_20mm",
        role="fc",
        name="F4 FC 20 mm (3-inch)",
        price_usd=32.0,
        class_ids=("three_inch",),
        mass_g=5.5,
        envelope_mm={"l": 27, "w": 27, "h": 8},
        vendor="SpeedyBee / iFlight class",
        url="https://www.getfpv.com/",
        mount={"pcd_mm": 20.0, "hole_mm": 2.0, "count": 4},
    ),
    _item(
        id="fc_305_f7",
        role="fc",
        name="F7 FC 30.5 mm",
        price_usd=45.0,
        class_ids=("five_inch", "seven_inch"),
        mass_g=8.0,
        envelope_mm={"l": 36, "w": 36, "h": 8},
        vendor="SpeedyBee / Matek class",
        url="https://www.getfpv.com/",
        mount={"pcd_mm": 30.5, "hole_mm": 3.0, "count": 4},
    ),
    _item(
        id="esc_20mm_35a",
        role="esc",
        name="4-in-1 ESC 35A 20 mm",
        price_usd=30.0,
        class_ids=("three_inch",),
        mass_g=8.0,
        envelope_mm={"l": 27, "w": 27, "h": 6},
        vendor="SpeedyBee class",
        url="https://www.getfpv.com/",
        mount={"pcd_mm": 20.0, "hole_mm": 2.0, "count": 4},
    ),
    _item(
        id="esc_305_60a",
        role="esc",
        name="4-in-1 ESC 60A 30.5 mm",
        price_usd=38.0,
        class_ids=("five_inch",),
        mass_g=14.0,
        envelope_mm={"l": 36, "w": 36, "h": 7},
        vendor="SpeedyBee / T-Motor class",
        url="https://www.getfpv.com/",
        mount={"pcd_mm": 30.5, "hole_mm": 3.0, "count": 4},
    ),
    _item(
        id="esc_305_80a",
        role="esc",
        name="4-in-1 ESC 80A 30.5 mm",
        price_usd=45.0,
        class_ids=("seven_inch", "five_inch"),
        mass_g=16.0,
        envelope_mm={"l": 36, "w": 36, "h": 8},
        vendor="T-Motor class",
        url="https://store.tmotor.com/",
        mount={"pcd_mm": 30.5, "hole_mm": 3.0, "count": 4},
    ),
]

ITEM_BY_ID = {i["id"]: i for i in ITEMS}


def class_by_id(class_id: str | None) -> dict[str, Any] | None:
    if not class_id:
        return None
    key = KIND_ALIAS.get(str(class_id).strip().lower(), str(class_id).strip())
    if not key:
        return None
    return CLASS_BY_ID.get(key)


def resolve_class_id(raw: Any) -> str:
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    # "whoop_65: keep $80" from a confirm survey
    token = text.split(":", 1)[0].strip().lower()
    alias = KIND_ALIAS.get(token, token)
    if alias in CLASS_BY_ID:
        return alias
    if token in CLASS_BY_ID:
        return token
    return KIND_ALIAS.get(text.lower(), "") or ""


def get_item(item_id: str | None) -> dict[str, Any] | None:
    if not item_id:
        return None
    return ITEM_BY_ID.get(str(item_id).strip())


def search_catalog(
    query: str = "",
    role: str | None = None,
    class_id: str | None = None,
    budget_usd: float | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    tokens = [t for t in (query or "").lower().replace(",", " ").split() if t]
    want_class = resolve_class_id(class_id) if class_id else ""
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in ITEMS:
        if role and item["role"] != str(role).strip().lower():
            continue
        if want_class and want_class not in item["class_ids"]:
            if not tokens:
                continue
        if budget_usd is not None:
            try:
                if float(item["price_usd"]) > float(budget_usd):
                    continue
            except (TypeError, ValueError):
                pass
        hay = " ".join(
            [
                item["id"],
                item["name"],
                item["role"],
                item.get("vendor") or "",
                str(item.get("kv") or ""),
                " ".join(item["class_ids"]),
            ]
        ).lower()
        score = sum(3 if tok == item["role"] else 1 for tok in tokens if tok in hay)
        if not tokens:
            score = 1
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["price_usd"]))
    return [item for _, item in scored[: max(1, int(limit or 8))]]
