# main.py
# Главный файл FastAPI
import os
import json
import base64
import time
from io import BytesIO
import qrcode
import asyncio
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from label_printer import (
    build_tspl_batch_from_template,
    print_with_variables,
    send_raw_to_printer,
)
from label_template_utils import (
    VARIABLE_BUTTONS,
    get_template,
    molding_variables,
    save_template,
    sheet_variables,
)

app = FastAPI()
from fastapi.responses import FileResponse  # убедитесь, что импортировано
@app.get("/icon.png", include_in_schema=False)
async def get_icon():
    icon_path = os.path.join(BASE_DIR, "icon.png")
    return FileResponse(icon_path, media_type="image/png")

@app.get("/manifest.json", include_in_schema=False)
async def get_manifest():
    return FileResponse("manifest.json", media_type="application/json")

@app.get("/sw.js", include_in_schema=False)
async def get_sw():
    return FileResponse("sw.js", media_type="application/javascript")

# Базовая директория проекта (исправляет NameError)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

DB_FILE = os.path.join(BASE_DIR, "database.json")
DECORS_FILE = os.path.join(BASE_DIR, "decors.json")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://192.168.1.168:8000").rstrip("/")
TRASH_FILE = os.path.join(BASE_DIR, "trash.json")

def load_trash():
    if not os.path.exists(TRASH_FILE):
        with open(TRASH_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)
    try:
        with open(TRASH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_trash(data):
    with open(TRASH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def material_qr_url(item_id: str) -> str:
    return f"{PUBLIC_BASE_URL}/{item_id}"


def material_display_name(item: dict) -> str:
    return f"{item['category']} {item['thickness']}мм {item['name']}"


class PrintLabelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    length: int = Field(..., ge=1)
    width: int = Field(..., ge=0)
    item_id: str = Field(..., min_length=1, max_length=64)


class PrintLabelsBatchRequest(BaseModel):
    item_ids: list[str] = Field(..., min_length=1, max_length=100)


class LabelTemplateSaveRequest(BaseModel):
    context: str = Field(..., pattern="^(sheets|molding)$")
    template: dict

def load_decors():
    if not os.path.exists(DECORS_FILE):
        return []
    with open(DECORS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

# Чтение данных из JSON
def load_db():
    if not os.path.exists(DB_FILE):
        return {"company_name": "Склад материалов мебельщика", "materials": []}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"company_name": "Склад материалов мебельщика", "materials": []}

# Сохранение данных в JSON
def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Новый «всеядный» маршрут для быстрого поиска декоров "на лету" в main.py (ОБНОВЛЕННЫЙ)
@app.get("/api/suggest-decor")
def suggest_decor(q: str = ""):
    query = q.lower().strip()
    if not query:
        return []
    
    # Загружаем вашу базу декоров из файла decors.json через вашу функцию load_decors()
    decors = load_decors()
    matched = []
    
    for d in decors:
        # Ищем вхождение букв пользователя в любую часть названия декора
        if query in d.lower():
            matched.append(d)
            
    # Умная сортировка: декоры, которые НАЧИНАЮТСЯ на буквы пользователя, ставим наверх списка
    matched.sort(key=lambda x: not x.lower().startswith(query))
    
    # СТАЛО: Отдаем весь список совпадений в виде JSON-массива, чтобы работал перебор стрелками
    return matched


# ГЛАВНАЯ СТРАНИЦА (3 Кнопки)
@app.get("/", response_class=HTMLResponse)
def main_menu(request: Request):
    db = load_db()
    return templates.TemplateResponse(
        request=request,
        name="menu.html",
        context={"company_name": db.get("company_name", "Склад материалов мебельщика")}
    )

# СТРАНИЦА: ЛИСТОВЫЕ МАТЕРИАЛЫ
@app.get("/sheets", response_class=HTMLResponse)
def sheets_page(request: Request):
    db = load_db()
    active_materials = [m for m in db["materials"] if m.get("status") in ["В наличии", "В резерве"]]
    
    for item in active_materials:
       
        qr_data = material_qr_url(item["id"])

        qr = qrcode.QRCode(version=1, box_size=10, border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        item["qr_base64"] = base64.b64encode(buffered.getvalue()).decode()

    return templates.TemplateResponse(
        request=request,
        name="sheets.html",
        context={"materials": active_materials}
    )
# ЭНДПОИНТ ДЛЯ ЖИВОГО ОБНОВЛЕНИЯ ДАННЫХ (Вызывается фронтендом каждые 2 секунды)
@app.get("/api/materials")
def get_live_materials():
    db = load_db()
    # Фильтруем только активные материалы, как и для основной страницы
    active_materials = [m for m in db["materials"] if m.get("status") in ["В наличии", "В резерве"]]
    
    # Генерируем QR-коды для новых элементов налету, чтобы фронтенд мог их напечатать
    for item in active_materials:
        qr_data = material_qr_url(item["id"])
        qr = qrcode.QRCode(version=1, box_size=10, border=1)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        item["qr_base64"] = base64.b64encode(buffered.getvalue()).decode()
        
    return {"materials": active_materials}


def _print_label_for_item(body: PrintLabelRequest) -> None:
    qr_url = material_qr_url(body.item_id)
    if body.item_id.startswith("mld_"):
        item = next((i for i in load_molding() if i.get("id") == body.item_id), None)
        if item:
            print_with_variables("molding", molding_variables(item, qr_url))
            return
        print_with_variables(
            "molding",
            molding_variables(
                {
                    "name": body.name.strip(),
                    "length": body.length,
                    "quantity": 1,
                    "comment": "",
                    "sku": "",
                },
                qr_url,
            ),
        )
        return

    db = load_db()
    item = next((m for m in db.get("materials", []) if m.get("id") == body.item_id), None)
    if item:
        print_with_variables(
            "sheets",
            sheet_variables(item, qr_url, material_display_name(item)),
        )
        return
    print_with_variables(
        "sheets",
        sheet_variables(
            {
                "length": body.length,
                "width": body.width,
                "quantity": 1,
                "comment": "",
                "reserve_order": "",
            },
            qr_url,
            body.name.strip(),
        ),
    )


@app.post("/api/print-label")
def api_print_label(body: PrintLabelRequest):
    try:
        _print_label_for_item(body)
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось отправить на принтер: {exc}",
        ) from exc
    return {"ok": True, "message": "Бирка отправлена на печать"}


@app.post("/api/print-labels")
def api_print_labels(body: PrintLabelsBatchRequest):
    db = load_db()
    by_id = {m["id"]: m for m in db.get("materials", [])}
    variables_list: list[dict[str, str]] = []
    missing: list[str] = []
    for item_id in body.item_ids:
        item = by_id.get(item_id)
        if not item:
            missing.append(item_id)
            continue
        variables_list.append(
            sheet_variables(item, material_qr_url(item_id), material_display_name(item))
        )
    if not variables_list:
        raise HTTPException(status_code=404, detail="Материалы не найдены")
    tpl = get_template("sheets")
    pr = tpl.get("printer") or {}
    host = str(pr.get("host") or "")
    port = int(pr.get("port") or 9100)
    try:
        send_raw_to_printer(
            build_tspl_batch_from_template("sheets", variables_list, template=tpl),
            host=host or None,
            port=port or None,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось отправить на принтер: {exc}",
        ) from exc
    result = {"ok": True, "printed": len(variables_list), "message": "Бирки отправлены на печать"}
    if missing:
        result["missing_ids"] = missing
    return result


@app.get("/api/qr-image")
def api_qr_image(data: str = ""):
    qr = qrcode.QRCode(version=1, box_size=6, border=1)
    qr.add_data(data or " ")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return Response(content=buffered.getvalue(), media_type="image/png")


@app.get("/label-designer/{context}", response_class=HTMLResponse)
def label_designer_page(request: Request, context: str):
    if context not in ("sheets", "molding"):
        raise HTTPException(status_code=404, detail="Неизвестный тип страницы")
    template = get_template(context)  # type: ignore[arg-type]
    page_title = "Листовой материал" if context == "sheets" else "Погонаж склада"
    back_url = "/sheets" if context == "sheets" else "/molding"
    return templates.TemplateResponse(
        request=request,
        name="label_designer.html",
        context={
            "context": context,
            "back_url": back_url,
            "page_title": page_title,
            "template": template,
            "var_buttons": VARIABLE_BUTTONS[context],  # type: ignore[index]
        },
    )


@app.get("/api/label-template/{context}")
def api_get_label_template(context: str):
    if context not in ("sheets", "molding"):
        raise HTTPException(status_code=404, detail="Неизвестный тип страницы")
    return {"context": context, "template": get_template(context)}  # type: ignore[arg-type]


@app.post("/api/label-template")
def api_save_label_template(body: LabelTemplateSaveRequest):
    ctx = body.context
    tpl = body.template
    if not isinstance(tpl.get("elements"), list):
        raise HTTPException(status_code=400, detail="Некорректная структура шаблона")
    save_template(ctx, tpl)  # type: ignore[arg-type]
    return {"ok": True, "message": "Шаблон бирки сохранён"}


# ОБРАБОТЧИК: ДОБАВЛЕНИЕ НОВОГО ЛИСТА С АВТОПОПОЛНЕНИЕМ БАЗЫ
@app.post("/add-sheet")
def add_sheet(
    category: str = Form(...),
    name: str = Form(...),
    thickness: int = Form(...),
    length: int = Form(...),
    width: int = Form(...),
    quantity: int = Form(...),
    comment: str = Form("")
):
    db = load_db()
    import random
    new_id = f"mat_{random.randint(1000, 9999)}"
    barcode = f"{category}-{random.randint(100000, 999999)}"
    
    # ------------------------------------------------------------
    # 🧠 АВТОМАТИЧЕСКОЕ ЗАНЕСЕНИЕ НОВОГО ЦВЕТА В decors.json
    # ------------------------------------------------------------
    decor_name_trimmed = name.strip()
    decors_list = load_decors() # Используем вашу штатную функцию загрузки декоров
    
    # Если кладовщик вбил новый декор, которого еще нет в базе — сохраняем его в decors.json
    if decor_name_trimmed and decor_name_trimmed not in decors_list:
        decors_list.append(decor_name_trimmed)
        decors_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decors.json")
        try:
            with open(decors_path, "w", encoding="utf-8") as f:
                json.dump(decors_list, f, ensure_ascii=False, indent=4)
            print(f"📝 База обучена! Новый декор '{decor_name_trimmed}' сохранен.")
        except Exception as e:
            print(f"Ошибка сохранения декора: {e}")
    # ------------------------------------------------------------
    
    new_item = {
        "id": new_id,
        "category": category,
        "name": name,
        "thickness": thickness,
        "length": length,
        "width": width,
        "quantity": quantity,
        "comment": comment,
        "barcode": barcode,
        "status": "В наличии",
        "reserve_order": ""
    }
    db["materials"].append(new_item)
    save_db(db)
    
    # Оставляем ваш рабочий редирект с маркером для оранжевой плашки печати
    return RedirectResponse(url=f"/sheets?just_added_id={new_id}", status_code=303)

# ОБРАБОТЧИК: УСТАНОВКА РЕЗЕРВА
@app.post("/reserve-sheet/{item_id}")
def reserve_sheet(item_id: str, reserve_order: str = Form(...)):
    db = load_db()
    for item in db["materials"]:
        if item.get("id") == item_id:
            item["status"] = "В резерве"
            item["reserve_order"] = reserve_order
            break
    save_db(db)
    return RedirectResponse(url="/sheets", status_code=303)

# ОБРАБОТЧИК: СНЯТИЕ С РЕЗЕРВА
@app.post("/unreserve-sheet/{item_id}")
def unreserve_sheet(item_id: str):
    db = load_db()
    for item in db["materials"]:
        if item.get("id") == item_id:
            item["status"] = "В наличии"
            item["reserve_order"] = ""
            break
    save_db(db)
    return RedirectResponse(url="/sheets", status_code=303)

# ОБНОВЛЕННЫЙ ОБРАБОТЧИК: РЕДАКТИРОВАНИЕ РАЗМЕРОВ, КОЛИЧЕСТВА И КОММЕНТАРИЕВ
@app.post("/edit-sheet/{item_id}")
def edit_sheet(
    item_id: str,
    length: int = Form(...),
    width: int = Form(...),
    quantity: int = Form(1), # 🧠 ДОБАВИЛИ ПРИЕМ КОЛИЧЕСТВА ИЗ ФОРМЫ (По умолчанию 1)
    comment: str = Form("") 
):
    db = load_db()
    for item in db["materials"]:
        if item.get("id") == item_id:
            item["length"] = length
            item["width"] = width
            item["quantity"] = max(1, quantity) # Жестко страхуем базу от значений меньше 1
            item["comment"] = comment.strip() 
            break
    save_db(db)
    return {"status": "success", "message": "Данные заготовки успешно обновлены"}

# ОДИНОЧНОЕ СПИСАНИЕ ЛИСТОВ
@app.post("/delete-sheet/{item_id}")
def delete_sheet(item_id: str):
    db = load_db()
    trash = load_trash()
    
    for item in db["materials"]:
        if item.get("id") == item_id:
            item["type"] = "sheet"  # Маркер типа для восстановления
            item["deleted_at"] = int(time.time())
            trash.insert(0, item)
            break
            
    db["materials"] = [item for item in db["materials"] if item.get("id") != item_id]
    save_db(db)
    save_trash(trash)
    return RedirectResponse(url="/sheets", status_code=303)

# МАССОВОЕ СПИСАНИЕ ЛИСТОВ
@app.post("/mass-delete")
async def mass_delete(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    if selected_ids:
        db = load_db()
        trash = load_trash()
        for item in db["materials"]:
            if item.get("id") in selected_ids:
                item["type"] = "sheet"
                item["deleted_at"] = int(time.time())
                trash.insert(0, item)
        db["materials"] = [item for item in db["materials"] if item.get("id") not in selected_ids]
        save_db(db)
        save_trash(trash)
    return RedirectResponse(url="/sheets", status_code=303)

# ОБРАБОТЧИК: МАССОВАЯ УСТАНОВКА РЕЗЕРВА
@app.post("/mass-reserve")
async def mass_reserve(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    reserve_order = form_data.get("mass_reserve_order", "").strip()
    if selected_ids and reserve_order:
        db = load_db()
        for item in db["materials"]:
            if item.get("id") in selected_ids:
                item["status"] = "В резерве"
                item["reserve_order"] = reserve_order
        save_db(db)
    return RedirectResponse(url="/sheets", status_code=303)

# ОБРАБОТЧИК: МАССОВОЕ СНЯТИЕ С РЕЗЕРВА
@app.post("/mass-unreserve")
async def mass_unreserve(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    if selected_ids:
        db = load_db()
        for item in db["materials"]:
            if item.get("id") in selected_ids:
                item["status"] = "В наличии"
                item["reserve_order"] = ""
        save_db(db)
    return RedirectResponse(url="/sheets", status_code=303)

@app.get("/api/materials-for-sketchup")
def get_materials_for_sketchup():
    db = load_db()
    # Отдаем только те материалы, которые есть в наличии или резерве
    active_materials = [m for m in db["materials"] if m.get("status") in ["В наличии", "В резерве"]]
    return {"materials": active_materials}


# ================================== Погонаж ======================================
# ИНИЦИАЛИЗАЦИЯ ОТДЕЛЬНОЙ БАЗЫ ДАННЫХ ДЛЯ ПОГОНАЖА
MOLDING_DB = os.path.join(BASE_DIR, "molding.json")

def load_molding():
    if not os.path.exists(MOLDING_DB):
        with open(MOLDING_DB, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=4)
    try:
        with open(MOLDING_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_molding(data):
    with open(MOLDING_DB, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# ============================================================
# МАРШРУТ ПОГОНАЖА (АКТИВИРОВАЛИ ЧТЕНИЕ БАЗЫ ДАННЫХ)
# ============================================================
@app.get("/molding", response_class=HTMLResponse)
def get_molding_page(request: Request):
    # 🧠 ТЕПЕРЬ СЕРВЕР ЖЕСТКО ЧИТАЕТ СТОЛЕШНИЦЫ И ПРОФИЛИ ИЗ molding.json
    items = load_molding()
    return templates.TemplateResponse(
        request=request,
        name="molding.html",
        context={"materials": items}  # Передали тестовый массив в Jinja2
    )


# 2. Живое API для 2-секундного фонового обновления
@app.get("/api/molding")
def api_get_molding():
    items = load_molding()
    return {"materials": items}

import os
import json
import base64
import time  # <--- ДОБАВИЛИ ИМПОРТ ДЛЯ РАБОТЫ time.time()
from io import BytesIO
import qrcode
import asyncio
from fastapi import FastAPI, Request, Form, HTTPException
# ... остальной код импортов без изменений ...

# ...

# 3. Приёмка нового погонажного изделия на склад (ИСПРАВЛЕННАЯ)
@app.post("/add-molding")
def add_molding(
    name: str = Form(...),
    sku: str = Form(""),  # <--- СДЕЛАЛИ НЕОБЯЗАТЕЛЬНЫМ (значение по умолчанию "")
    length: int = Form(...),
    quantity: int = Form(...),
    comment: str = Form(None)
):
    items = load_molding()
    
    # Генерируем уникальный ID на основе времени
    new_id = f"mld_{int(time.time() * 1000)}"
    barcode = f"MLD{int(time.time())}"
    
    new_item = {
        "id": new_id,
        "name": name.strip(),          
        "sku": sku.strip() if sku else "", # Чистим пробелы, если артикул передан  
        "length": length,      
        "quantity": quantity,  
        "comment": comment.strip() if comment else "",
        "status": "В наличии",
        "barcode": barcode
    }
    
    items.insert(0, new_item)
    save_molding(items)
    
    # Редирект с маркером только что добавленного ID для вызова плашки печати
    return RedirectResponse(url=f"/molding?just_added_id={new_id}", status_code=303)


# 4. Фоновое асинхронное редактирование погонажа (Без F5)
@app.post("/edit-molding/{item_id}")
def edit_molding_endpoint(
    item_id: str,
    length: int = Form(...),
    quantity: int = Form(...),
    comment: str = Form(None)
):
    items = load_molding()
    for item in items:
        if item["id"] == item_id:
            item["length"] = length
            item["quantity"] = quantity
            item["comment"] = comment or ""
            save_molding(items)
            return {"status": "success"}
            
    raise HTTPException(status_code=404, detail="Позиция не найдена")



# ===============================================================================
# МАССОВЫЕ ОПЕРАЦИИ ДЛЯ ПОГОНАЖА (ИСПРАВЛЕНИЕ ДЛЯ КНОПОК НИЖНЕЙ ПАНЕЛИ)
# ===============================================================================

@app.post("/mass-molding-reserve")
async def mass_molding_reserve(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    reserve_order = form_data.get("mass_reserve_order", "").strip()
    
    if selected_ids and reserve_order:
        items = load_molding()
        for item in items:
            if item.get("id") in selected_ids:
                item["status"] = "В резерве"
                item["reserve_order"] = reserve_order
        save_molding(items)
        
    return RedirectResponse(url="/molding", status_code=303)


@app.post("/mass-molding-unreserve")
async def mass_molding_unreserve(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    
    if selected_ids:
        items = load_molding()
        for item in items:
            if item.get("id") in selected_ids:
                item["status"] = "В наличии"
                item["reserve_order"] = ""
        save_molding(items)
        
    return RedirectResponse(url="/molding", status_code=303)


# МАССОВОЕ СПИСАНИЕ ПОГОНАЖА
@app.post("/mass-molding-delete")
async def mass_molding_delete(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    if selected_ids:
        items = load_molding()
        trash = load_trash()
        for item in items:
            if item.get("id") in selected_ids:
                item["type"] = "molding"
                item["deleted_at"] = int(time.time())
                trash.insert(0, item)
        items = [item for item in items if item.get("id") not in selected_ids]
        save_molding(items)
        save_trash(trash)
    return RedirectResponse(url="/molding", status_code=303)

# ===============================================================================
# НАДЕЖНЫЙ ОБРАБОТЧИК МАССОВОЙ ПЕЧАТИ ПОГОНАЖА ИЗ ФОРМЫ (БЕЗ AJAX/FETCH)
# ===============================================================================
@app.post("/mass-molding-print")
async def mass_molding_print(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    
    if selected_ids:
        molding_items = load_molding()
        by_id = {m["id"]: m for m in molding_items}
        variables_list: list[dict[str, str]] = []
        
        for item_id in selected_ids:
            item = by_id.get(item_id)
            if not item:
                continue
            variables_list.append(molding_variables(item, material_qr_url(item_id)))
            
        if variables_list:
            tpl = get_template("molding")
            pr = tpl.get("printer") or {}
            host = str(pr.get("host") or "")
            port = int(pr.get("port") or 9100)
            try:
                send_raw_to_printer(
                    build_tspl_batch_from_template("molding", variables_list, template=tpl),
                    host=host or None,
                    port=port or None,
                )
                print(f"🖨 Успешно отправлен пакет из {len(variables_list)} бирок погонажа.")
            except OSError as exc:
                print(f"❌ Ошибка отправки пакета на принтер: {exc}")
                
    return RedirectResponse(url="/molding", status_code=303)
# СТРАНИЦА КОРЗИНЫ
@app.get("/trash", response_class=HTMLResponse)
def get_trash_page(request: Request):
    trash_items = load_trash()
    return templates.TemplateResponse(
        request=request,
        name="trash.html",
        context={"trash": trash_items}
    )

# ВОССТАНОВЛЕНИЕ ИЗ КОРЗИНЫ (ОДИНОЧНОЕ)
@app.post("/trash/restore/{item_id}")
def restore_from_trash(item_id: str):
    trash = load_trash()
    item_to_restore = None
    
    for item in trash:
        if item.get("id") == item_id:
            item_to_restore = item
            break
            
    if item_to_restore:
        # Удаляем временные маркеры корзины
        item_type = item_to_restore.pop("type", "sheet")
        item_to_restore.pop("deleted_at", None)
        
        # 🧠 УМНЫЙ ВОЗВРАТ СТАТУСА: Проверяем, в каком статусе деталь уходила в корзину
        current_status = item_to_restore.get("status", "В наличии")
        current_reserve = item_to_restore.get("reserve_order", "").strip()
        
        if current_status == "В резерве" and current_reserve:
            # Если это был резерв — сохраняем статус и имя заказа без изменений!
            item_to_restore["status"] = "В резерве"
            item_to_restore["reserve_order"] = current_reserve
        else:
            # Если деталь была свободной — гарантируем чистый статус "В наличии"
            item_to_restore["status"] = "В наличии"
            item_to_restore["reserve_order"] = ""

        if item_type == "sheet":
            db = load_db()
            db["materials"].append(item_to_restore)
            save_db(db)
        else:
            molding = load_molding()
            molding.insert(0, item_to_restore)
            save_molding(molding)
            
        trash = [item for item in trash if item.get("id") != item_id]
        save_trash(trash)
        
    return RedirectResponse(url="/trash", status_code=303)

# ОКОНЧАТЕЛЬНОЕ УДАЛЕНИЕ ИЗ КОРЗИНЫ
@app.post("/trash/delete/{item_id}")
def hard_delete_from_trash(item_id: str):
    trash = load_trash()
    trash = [item for item in trash if item.get("id") != item_id]
    save_trash(trash)
    return RedirectResponse(url="/trash", status_code=303)

# ПОЛНАЯ ОЧИСТКА КОРЗИНЫ
@app.post("/trash/clear")
def clear_all_trash():
    save_trash([])
    return RedirectResponse(url="/trash", status_code=303)


# МАССОВОЕ ВОССТАНОВЛЕНИЕ ИЗ КОРЗИНЫ (ПАКЕТНОЕ)
@app.post("/trash/mass-restore")
async def mass_trash_restore(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    
    if selected_ids:
        trash = load_trash()
        db = load_db()
        molding = load_molding()
        
        # Фильтруем те элементы, которые нужно восстановить
        to_restore = [item for item in trash if item.get("id") in selected_ids]
        
        for item in to_restore:
            item_type = item.pop("type", "sheet")
            item.pop("deleted_at", None)
            
            # 🧠 УМНЫЙ ВОЗВРАТ СТАТУСА ДЛЯ МАССОВОГО ПАКЕТА ДЕТАЛЕЙ
            current_status = item.get("status", "В наличии")
            current_reserve = item.get("reserve_order", "").strip()
            
            if current_status == "В резерве" and current_reserve:
                item["status"] = "В резерве"
                item["reserve_order"] = current_reserve
            else:
                item["status"] = "В наличии"
                item["reserve_order"] = ""
            
            if item_type == "sheet":
                db["materials"].append(item)
            else:
                molding.insert(0, item)
                
        # Оставляем в корзине только неотмеченные элементы
        new_trash = [item for item in trash if item.get("id") not in selected_ids]
        
        save_db(db)
        save_molding(molding)
        save_trash(new_trash)
        
    return RedirectResponse(url="/trash", status_code=303)


# МАССОВОЕ УДАЛЕНИЕ НАСОВСЕМ ИЗ КОРЗИНЫ
@app.post("/trash/mass-delete-hard")
async def mass_trash_hard_delete(request: Request):
    form_data = await request.form()
    selected_ids = form_data.getlist("selected_items")
    
    if selected_ids:
        trash = load_trash()
        new_trash = [item for item in trash if item.get("id") not in selected_ids]
        save_trash(new_trash)
        
    return RedirectResponse(url="/trash", status_code=303)

# ФОНОВОЕ ЖИВОЕ ОБНОВЛЕНИЕ КОРЗИНЫ ДЛЯ VIRTUAL DOM
@app.get("/api/trash/live-data")
def api_trash_live_data():
    trash_items = load_trash()
    return {"trash": trash_items}


# =============================конец===========================

# ===============================================================================
# УМНЫЙ ОБРАБОТЧИК QR-КОДОВ (ИСПРАВЛЕННЫЙ ОТ ПЕРЕХВАТА КОРЗИНЫ)
# ===============================================================================
@app.get("/{item_id}")
def scan_short_qr(item_id: str):
    # 🧠 ДОБАВИЛИ "trash" В СПИСОК ИСКЛЮЧЕНИЙ БЭКЕНДА
    # 🧠 Проверяем, если ссылка начинается на системные пути или содержит trash, то бэкенд её не трогает
    if item_id in [
        "sheets",
        "molding",
        "favicon.ico",
        "add-sheet",
        "add-molding",
        "trash",
        "label-designer",
        "api",
    ] or "trash" in item_id or item_id.startswith("label-designer"):
        return None

        
    # Разделение по префиксу ID
    if item_id.startswith("mld_"):
        return RedirectResponse(url=f"/molding?search_id={item_id}", status_code=303)
    else:
        return RedirectResponse(url=f"/sheets?search_id={item_id}", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)




