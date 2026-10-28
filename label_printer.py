# label_printer.py — печать бирок 58×40 мм на BSmart BS350 (TSPL, Ethernet)
import os
import socket
from dataclasses import dataclass
from typing import Any, Literal, Optional

from label_template_utils import (
    DOTS_PER_MM,
    get_template,
    render_template_string,
)

ContextType = Literal["sheets", "molding"]

LABEL_WIDTH_MM = float(os.environ.get("LABEL_WIDTH_MM", "58"))
LABEL_HEIGHT_MM = float(os.environ.get("LABEL_HEIGHT_MM", "40"))
LABEL_GAP_MM = float(os.environ.get("LABEL_GAP_MM", "3"))
LABEL_PRINTER_HOST = os.environ.get("LABEL_PRINTER_HOST", "192.168.1.200")
LABEL_PRINTER_PORT = int(os.environ.get("LABEL_PRINTER_PORT", "9100"))
LABEL_PRINT_TIMEOUT = float(os.environ.get("LABEL_PRINT_TIMEOUT", "8"))


@dataclass
class LabelPayload:
    name: str
    length: int
    width: int
    qr_url: str


def _escape_tspl(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _mm_to_dots(mm: float) -> int:
    return max(0, int(round(mm * DOTS_PER_MM)))


def _wrap_lines(text: str, max_len: int = 22) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_len:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:8]


def _text_lines_resolved(resolved: str, box_w_mm: float) -> list[str]:
    max_len = max(8, int(box_w_mm * 1.8))
    out: list[str] = []
    for paragraph in resolved.replace("\r\n", "\n").split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        out.extend(_wrap_lines(paragraph, max_len=max_len))
    return out[:10] or [""]


def _line_x(base_x: int, line: str, box_w_dots: int, align_h: str, char_w: int) -> int:
    text_w = len(line) * char_w
    if align_h == "center":
        return base_x + max(0, (box_w_dots - text_w) // 2)
    if align_h == "right":
        return base_x + max(0, box_w_dots - text_w)
    return base_x


def _body_from_template(template: dict[str, Any], variables: dict[str, str]) -> str:
    media = template.get("media") or {}
    width_mm = float(media.get("width_mm") or LABEL_WIDTH_MM)
    height_mm = float(media.get("height_mm") or LABEL_HEIGHT_MM)

    parts = ["CLS"]
    for el in template.get("elements") or []:
        el_type = el.get("type")
        if el_type == "text":
            tmpl = str(el.get("template") or "")
            resolved = render_template_string(tmpl, variables)
            x = _mm_to_dots(float(el.get("x_mm") or 0))
            y = _mm_to_dots(float(el.get("y_mm") or 0))
            w_mm = float(el.get("w_mm") or width_mm)
            h_mm = float(el.get("h_mm") or 8)
            w_dots = _mm_to_dots(w_mm)
            h_dots = _mm_to_dots(h_mm)
            align_h = str(el.get("align_h") or "left").lower()
            align_v = str(el.get("align_v") or "top").lower()
            font = str(int(el.get("font_scale") or 2))
            line_h = 28 if font == "2" else 36
            char_w = 12 if font == "2" else 14
            lines = _text_lines_resolved(resolved, w_mm)
            block_h = len(lines) * line_h
            if align_v == "center":
                start_y = y + max(0, (h_dots - block_h) // 2)
            elif align_v == "bottom":
                start_y = y + max(0, h_dots - block_h)
            else:
                start_y = y
            for i, line in enumerate(lines):
                lx = _line_x(x, line, w_dots, align_h, char_w)
                ly = start_y + i * line_h
                if ly > _mm_to_dots(height_mm):
                    break
                parts.append(f'TEXT {lx},{ly},"{font}",0,1,1,"{_escape_tspl(line)}"')
        elif el_type == "qr":
            tmpl = str(el.get("template") or "{qr_url}")
            data = render_template_string(tmpl, variables)
            x = _mm_to_dots(float(el.get("x_mm") or 0))
            y = _mm_to_dots(float(el.get("y_mm") or 0))
            size_mm = float(el.get("size_mm") or 14)
            cell = max(2, min(10, int(round(size_mm / 3.5))))
            parts.append(f'QRCODE {x},{y},M,{cell},A,0,"{_escape_tspl(data)}"')
    parts.append("PRINT 1")
    return "\r\n".join(parts)


def _tspl_header(template: dict[str, Any]) -> str:
    media = template.get("media") or {}
    w = float(media.get("width_mm") or LABEL_WIDTH_MM)
    h = float(media.get("height_mm") or LABEL_HEIGHT_MM)
    gap = float(media.get("gap_mm") or LABEL_GAP_MM)
    return (
        f"SIZE {w} mm, {h} mm\r\n"
        f"GAP {gap} mm, 0 mm\r\n"
        "DIRECTION 1\r\n"
        "REFERENCE 0,0\r\n"
        "OFFSET 0 mm\r\n"
        "SET TEAR ON\r\n"
        "CODEPAGE 1251\r\n"
    )


def build_tspl_from_template(
    context: ContextType,
    variables: dict[str, str],
    template: Optional[dict[str, Any]] = None,
) -> bytes:
    tpl = template or get_template(context)
    header = _tspl_header(tpl)
    body = _body_from_template(tpl, variables)
    return (header + body + "\r\n").encode("cp1251", errors="replace")


def build_tspl_batch_from_template(
    context: ContextType,
    variables_list: list[dict[str, str]],
    template: Optional[dict[str, Any]] = None,
) -> bytes:
    if not variables_list:
        return b""
    tpl = template or get_template(context)
    header = _tspl_header(tpl)
    bodies = [_body_from_template(tpl, v) for v in variables_list]
    return (header + "\r\n".join(bodies) + "\r\n").encode("cp1251", errors="replace")


def _label_body_commands(label: LabelPayload) -> str:
    """Резервный макет, если шаблон пуст."""
    lines = _wrap_lines(label.name.strip())
    y = 95
    parts = ["CLS"]
    for line in lines:
        parts.append(f'TEXT 12,{y},"2",0,1,1,"{_escape_tspl(line)}"')
        y += 28
    size_y = max(y + 4, 140)
    parts.append(f'TEXT 12,{size_y},"3",0,1,1,"L: {label.length} mm"')
    if label.width > 1:
        parts.append(f'TEXT 12,{size_y + 36},"3",0,1,1,"W: {label.width} mm"')
    parts.append(f'QRCODE 330,95,M,4,A,0,"{_escape_tspl(label.qr_url)}"')
    parts.append("PRINT 1")
    return "\r\n".join(parts)


def build_tspl(label: LabelPayload, context: ContextType = "sheets") -> bytes:
    from label_template_utils import molding_variables, sheet_variables

    if context == "molding" or label.width <= 1:
        variables = molding_variables(
            {"name": label.name, "length": label.length, "quantity": 1, "comment": "", "sku": ""},
            label.qr_url,
        )
        ctx: ContextType = "molding"
    else:
        variables = sheet_variables(
            {
                "length": label.length,
                "width": label.width,
                "quantity": 1,
                "comment": "",
                "reserve_order": "",
            },
            label.qr_url,
            label.name,
        )
        ctx = "sheets"
    return build_tspl_from_template(ctx, variables)


def build_tspl_batch(labels: list[LabelPayload], context: ContextType = "sheets") -> bytes:
    if not labels:
        return b""
    from label_template_utils import molding_variables, sheet_variables

    tpl = get_template(context)
    variables_list: list[dict[str, str]] = []
    for label in labels:
        if context == "molding":
            variables_list.append(
                molding_variables(
                    {"name": label.name, "length": label.length, "quantity": 1, "comment": "", "sku": ""},
                    label.qr_url,
                )
            )
        else:
            variables_list.append(
                sheet_variables(
                    {
                        "length": label.length,
                        "width": label.width,
                        "quantity": 1,
                        "comment": "",
                        "reserve_order": "",
                    },
                    label.qr_url,
                    label.name,
                )
            )
    return build_tspl_batch_from_template(context, variables_list, template=tpl)


def send_raw_to_printer(data: bytes, host: Optional[str] = None, port: Optional[int] = None) -> None:
    target_host = host or LABEL_PRINTER_HOST
    target_port = port or LABEL_PRINTER_PORT
    with socket.create_connection(
        (target_host, target_port), timeout=LABEL_PRINT_TIMEOUT
    ) as sock:
        sock.sendall(data)


def _printer_connection(template: Optional[dict[str, Any]] = None) -> tuple[str, int]:
    if template:
        pr = template.get("printer") or {}
        host = str(pr.get("host") or LABEL_PRINTER_HOST)
        port = int(pr.get("port") or LABEL_PRINTER_PORT)
        return host, port
    return LABEL_PRINTER_HOST, LABEL_PRINTER_PORT


def print_label(
    label: LabelPayload,
    host: Optional[str] = None,
    port: Optional[int] = None,
    context: ContextType = "sheets",
) -> None:
    ctx = "molding" if context == "molding" or label.width <= 1 else "sheets"
    tpl = get_template(ctx)
    h, p = _printer_connection(tpl)
    send_raw_to_printer(build_tspl(label, context=ctx), host=host or h, port=port or p)


def print_labels(
    labels: list[LabelPayload],
    host: Optional[str] = None,
    port: Optional[int] = None,
    context: ContextType = "sheets",
) -> None:
    ctx = context
    if labels and labels[0].width <= 1:
        ctx = "molding"
    tpl = get_template(ctx)
    h, p = _printer_connection(tpl)
    send_raw_to_printer(build_tspl_batch(labels, context=ctx), host=host or h, port=port or p)


def print_with_variables(
    context: ContextType,
    variables: dict[str, str],
    template: Optional[dict[str, Any]] = None,
) -> None:
    tpl = template or get_template(context)
    host, port = _printer_connection(tpl)
    send_raw_to_printer(build_tspl_from_template(context, variables, template=tpl), host=host, port=port)
