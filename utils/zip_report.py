from __future__ import annotations

import json
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Dict, List, Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _ensure_font(font_path: Path, font_name: str = "CustomPersian") -> str:
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    return font_name


def build_summary_pdf(entries: List[Dict[str, Any]], pdf_path: Path, font_path: Path) -> Path:
    font_name = _ensure_font(font_path)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Persian", fontName=font_name, fontSize=12, leading=16, alignment=0))
    styles.add(ParagraphStyle(name="PersianTitle", fontName=font_name, fontSize=14, leading=18, alignment=0, spaceAfter=12))

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)

    elems = []
    elems.append(Paragraph("سپاس از استفاده شما از ربات ما.", styles["PersianTitle"]))

    headers = ["ردیف", "عنوان", "سال انتشار", "اسم فایل", "وضعیت هزینه", "وضعیت دانلود"]
    data = [headers]

    free_count = 0
    paid_count = 0
    fail_count = 0

    for idx, item in enumerate(entries, start=1):
        title = item.get("title") or "—"
        year = item.get("year") or "—"
        fname = item.get("filename") or "—"
        cost = item.get("cost") or "—"
        status = item.get("status") or "—"

        if cost == "رایگان" and status.startswith("موفق"):
            free_count += 1
        elif cost == "هزینه‌دار" and status.startswith("موفق"):
            paid_count += 1
        else:
            fail_count += 1

        data.append([str(idx), title, str(year), fname, cost, status])

    table = Table(data, colWidths=[1.2 * cm, 6 * cm, 2.2 * cm, 4.5 * cm, 3 * cm, 4 * cm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightyellow]),
    ]))

    elems.append(table)
    elems.append(Spacer(1, 12))

    summary_text = (
        f"دانلود رایگان: {free_count} مورد | "
        f"دارای هزینه: {paid_count} مورد | "
        f"ناموفق/انجام‌نشده: {fail_count} مورد"
    )
    elems.append(Paragraph(summary_text, styles["Persian"]))

    doc.build(elems)
    return pdf_path


def build_zip_with_summary(entries: List[Dict[str, Any]], zip_path: Path, font_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_pdf = zip_path.with_suffix(".summary.pdf")

    build_summary_pdf(entries, tmp_pdf, font_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp_pdf, arcname="فهرست.pdf")
        for item in entries:
            fpath = item.get("file_path")
            if fpath:
                p = Path(fpath)
                if p.exists():
                    zf.write(p, arcname=p.name)

        # فایل JSON کوچک برای دیباگ
        meta = [{"doi": e.get("doi"), "title": e.get("title"), "year": e.get("year"),
                 "filename": e.get("filename"), "cost": e.get("cost"), "status": e.get("status")} for e in entries]
        zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

    with suppress(Exception):
        tmp_pdf.unlink()
    return zip_path
