# -*- coding: utf-8 -*-
"""Direct PDF resume renderer - builds a PDF straight from the same tailored
data dict the docx templates use (see underline_header.py for the closest
visual match), via reportlab. Pure Python, no external office software
(Word/LibreOffice) and no watermark, unlike docx-to-PDF conversion options.
Produced alongside the .docx, not instead of it - see ollama_generate.py."""

from xml.sax.saxutils import escape as _esc

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer,
)

from .common import ADDRESS, EMAIL, EXPERIENCE, NAME, PHONE

BLACK = colors.HexColor("#000000")
GRAY = colors.HexColor("#555555")


def _styles():
    # ReportLab's ParagraphStyle defaults "leading" (line height) to a flat
    # 12pt when not given explicitly - fine for ~10pt body text, but far too
    # short for anything bigger (e.g. the 24pt name), which then visually
    # overlaps the line below it since it's only given 12pt of vertical
    # room to sit in. Every style below sets leading explicitly, ~1.25x its
    # fontSize, to avoid that.
    return {
        "name": ParagraphStyle(
            "name", fontName="Helvetica-Bold", fontSize=24, leading=29, textColor=BLACK,
            alignment=TA_CENTER, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName="Helvetica", fontSize=10.5, leading=14, textColor=GRAY,
            alignment=TA_CENTER, spaceAfter=6,
        ),
        "contact": ParagraphStyle(
            "contact", fontName="Helvetica", fontSize=10, leading=13, textColor=GRAY,
            alignment=TA_CENTER, spaceAfter=0,
        ),
        "section": ParagraphStyle(
            "section", fontName="Helvetica-Bold", fontSize=12.5, leading=15, textColor=BLACK,
            spaceBefore=14, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", fontName="Helvetica", fontSize=11, textColor=BLACK,
            leading=14, spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "bullet", fontName="Helvetica", fontSize=11, textColor=BLACK, leading=14,
        ),
        "role_title": ParagraphStyle(
            "role_title", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=BLACK,
            spaceBefore=10, spaceAfter=0,
        ),
        "role_meta": ParagraphStyle(
            "role_meta", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=BLACK,
            spaceAfter=4,
        ),
        "edu_school": ParagraphStyle(
            "edu_school", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=BLACK,
            spaceAfter=0,
        ),
        "edu_line": ParagraphStyle(
            "edu_line", fontName="Helvetica", fontSize=11, leading=14, textColor=GRAY, spaceAfter=4,
        ),
    }


def _section_header(story, styles, text):
    story.append(Paragraph(text.upper(), styles["section"]))
    story.append(HRFlowable(width="100%", thickness=0.75, color=BLACK, spaceAfter=4))


def build(data: dict, output_path: str) -> str:
    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        title=f"Jimmy Tran - {data.get('target_company', '')}",
    )
    styles = _styles()
    story = []

    story.append(Paragraph(NAME, styles["name"]))
    story.append(Paragraph(_esc(data["subtitle"]).upper(), styles["subtitle"]))
    story.append(Paragraph(f"Email: {EMAIL}", styles["contact"]))
    story.append(Paragraph(f"Phone: {PHONE}", styles["contact"]))
    story.append(Paragraph(f"Address: {ADDRESS}", styles["contact"]))
    story.append(Paragraph(f"Experience: {EXPERIENCE}", styles["contact"]))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=BLACK, spaceAfter=6))

    _section_header(story, styles, "Professional Summary")
    story.append(Paragraph(_esc(data["summary"]), styles["body"]))

    _section_header(story, styles, "Work Experience")
    for role in data["experience"]:
        story.append(Paragraph(_esc(role["title"]), styles["role_title"]))
        story.append(Paragraph(
            f'{_esc(role["company"])} | {_esc(role["location"])} | {_esc(role["dates"])}',
            styles["role_meta"],
        ))
        bullet_items = [
            ListItem(Paragraph(_esc(b), styles["bullet"]), leftIndent=14)
            for b in role["bullets"]
        ]
        story.append(ListFlowable(
            bullet_items, bulletType="bullet", start="•", leftIndent=18, bulletFontSize=9,
        ))

    _section_header(story, styles, "Skills")
    for label, value in data["skills"]:
        story.append(Paragraph(f"<b>{_esc(label)}:</b> {_esc(value)}", styles["body"]))

    _section_header(story, styles, "Education")
    story.append(Paragraph(_esc(data["education"]["school"]), styles["edu_school"]))
    story.append(Paragraph(_esc(data["education"]["degree_line"]), styles["edu_line"]))

    _section_header(story, styles, "Keywords")
    story.append(Paragraph(_esc(data["keywords"]), styles["body"]))

    doc.build(story)
    return output_path
