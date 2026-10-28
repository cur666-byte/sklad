# label_template_utils.py — хранение шаблонов бирок и подстановка переменных
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from typing import Any, Literal, Optional

ContextType = Literal["sheets", "molding"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_FILE = os.path.join(BASE_DIR, "label_templates.json")

DOTS_PER_MM = 8.0
PRINTER_NAME = "BSmart BS350 (TSPL)"

TAG_ALIASES: dict[str, list[str]] = {
    "material": ["material", "материал"],
    "length": ["length", "длина"],
    "width": ["width", "ширина"],
    "reserve": ["reserve", "резерв"],
    "comment": ["comment", "комментарий"],
    "quantity": ["quantity", "количество"],
    "area": ["area", "площадь"],
    "date": ["date", "дата"],
    "time": ["time", "время"],
    "sku": ["sku", "article", "артикул"],
    "qr_url": ["qr_url", "url"],
}


def _default_template(context: ContextType) -> dict[str, Any]:
    from label_printer import LABEL_GAP_MM, LABEL_HEIGHT_MM, LABEL_PRINTER_HOST, LABEL_PRINTER_PORT, LABEL_WIDTH_MM

    base = {
        "printer": {
            "name": PRINTER_NAME,
            "host": LABEL_PRINTER_HOST,
            "port": LABEL_PRINTER_PORT,
        },
        "media": {
            "width_mm": LABEL_WIDTH_MM,
            "height_mm": LABEL_HEIGHT_MM,
            "gap_mm": LABEL_GAP_MM,
        },
        "elements": [],
    }
    if context == "sheets":
        base["elements"] = [
            {
                "id": "default-name",
                "type": "text",
                "x_mm": 1.5,
                "y_mm": 12,
                "w_mm": 35,
                "h_mm": 14,
                "template": "{material}",
                "align_h": "left",
                "align_v": "top",
                "font_scale": 2,
            },
            {
                "id": "default-size",
                "type": "text",
                "x_mm": 1.5,
                "y_mm": 28,
                "w_mm": 35,
                "h_mm": 10,
                "template": "L: {length} mm\nW: {width} mm",
                "align_h": "left",
                "align_v": "top",
                "font_scale": 3,
            },
            {
                "id": "default-qr",
                "type": "qr",
                "x_mm": 41,
                "y_mm": 12,
                "size_mm": 14,
                "template": "{qr_url}",
            },
        ]
    else:
        base["elements"] = [
            {
                "id": "default-name",
                "type": "text",
                "x_mm": 1.5,
                "y_mm": 12,
                "w_mm": 35,
                "h_mm": 14,
                "template": "{material}",
                "align_h": "left",
                "align_v": "top",
                "font_scale": 2,
            },
            {
                "id": "default-size",
                "type": "text",
                "x_mm": 1.5,
                "y_mm": 28,
                "w_mm": 35,
                "h_mm": 8,
                "template": "L: {length} mm",
                "align_h": "left",
                "align_v": "top",
                "font_scale": 3,
            },
            {
                "id": "default-qr",
                "type": "qr",
                "x_mm": 41,
                "y_mm": 12,
                "size_mm": 14,
                "template": "{qr_url}",
            },
        ]
    return base


def load_all_templates() -> dict[str, Any]:
    if not os.path.exists(TEMPLATES_FILE):
        return {"sheets": _default_template("sheets"), "molding": _default_template("molding")}
    try:
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}
    for ctx in ("sheets", "molding"):
        if ctx not in data or not isinstance(data.get(ctx), dict):
            data[ctx] = _default_template(ctx)  # type: ignore[arg-type]
    return data


def save_all_templates(data: dict[str, Any]) -> None:
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def get_template(context: ContextType) -> dict[str, Any]:
    all_data = load_all_templates()
    return deepcopy(all_data.get(context, _default_template(context)))


def save_template(context: ContextType, template: dict[str, Any]) -> None:
    all_data = load_all_templates()
    all_data[context] = template
    save_all_templates(all_data)


def _normalize_tag_key(raw: str) -> Optional[str]:
    key = raw.strip().lower()
    for canonical, aliases in TAG_ALIASES.items():
        if key in aliases:
            return canonical
    return None


def render_template_string(template: str, variables: dict[str, str]) -> str:
    if not template:
        return ""

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        canonical = _normalize_tag_key(inner)
        if canonical and canonical in variables:
            return variables[canonical]
        # поддержка {Material} и произвольного регистра латиницы
        low = inner.lower()
        for k, v in variables.items():
            if k.lower() == low:
                return v
        return match.group(0)

    return re.sub(r"\{([^}]+)\}", repl, template)


def sheet_variables(item: dict[str, Any], qr_url: str, material_name: str) -> dict[str, str]:
    length = int(item.get("length") or 0)
    width = int(item.get("width") or 0)
    now = datetime.now()
    return {
        "material": material_name,
        "length": str(length),
        "width": str(width),
        "reserve": str(item.get("reserve_order") or "").strip(),
        "comment": str(item.get("comment") or "").strip(),
        "quantity": str(item.get("quantity") or 1),
        "area": str(length * width),
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M"),
        "qr_url": qr_url,
    }


def molding_variables(item: dict[str, Any], qr_url: str) -> dict[str, str]:
    now = datetime.now()
    name = str(item.get("name") or "").strip()
    sku = str(item.get("sku") or "").strip()
    material = name
    if sku:
        material = f"{name}\n Арт:{sku}"
    return {
        "material": material,
        "sku": sku,
        "length": str(item.get("length") or 0),
        "reserve": str(item.get("reserve_order") or "").strip(),
        "comment": str(item.get("comment") or "").strip(),
        "quantity": str(item.get("quantity") or 1),
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M"),
        "qr_url": qr_url,
    }


def preview_sample_variables(context: ContextType) -> dict[str, str]:
    if context == "sheets":
        return {
            "material": "ЛДСП 16мм Дуб Галифакс",
            "length": "1200",
            "width": "800",
            "reserve": "50",
            "comment": "остаток кухни",
            "quantity": "2",
            "area": "960000",
            "date": "22.10.2026",
            "time": "14:30",
            "qr_url": "http://192.168.1.168:8000/mat_1234",
        }
    return {
        "material": "Профиль цокольный\n Арт:AGT 601",
        "sku": "AGT 601",
        "length": "3000",
        "reserve": "12",
        "comment": "полка №2",
        "quantity": "4",
        "date": "22.10.2026",
        "time": "14:30",
        "qr_url": "http://192.168.1.168:8000/mld_1234",
    }


VARIABLE_BUTTONS: dict[ContextType, list[tuple[str, str]]] = {
    "sheets": [
        ("Материал", "{material}"),
        ("Длина", "{length}"),
        ("Ширина", "{width}"),
        ("Резерв", "{reserve}"),
        ("Комментарий", "{comment}"),
        ("Количество", "{quantity}"),
        ("Площадь", "{area}"),
        ("Дата", "{date}"),
        ("Время", "{time}"),
    ],
    "molding": [
        ("Материал", "{material}"),
        ("Артикул", "{sku}"),
        ("Длина", "{length}"),
        ("Резерв", "{reserve}"),
        ("Комментарий", "{comment}"),
        ("Количество", "{quantity}"),
        ("Дата", "{date}"),
        ("Время", "{time}"),
    ],
}
