"""
orchestrator.exporter
=====================
Forensic PDF Report Generator for the MAS-D research platform.

Generates a high-fidelity A4 PDF report for a completed debate session
(status SUCCESS or ABORTED).  The report includes:
  - Title, topic, timestamp
  - Agent matrix (cast & configuration)
  - Prominent ABORT banner (if session was aborted via Kill-Switch)
  - Full debate transcript (from session chat log if available)
  - Scientific pagination (no broken lines)

Architecture Note
-----------------
This module is PURE domain logic.  It may import Django ORM models
(``dashboard.models``) to access session data, but it must NEVER import
anything from ``django.http``, ``django.views``, or ``dashboard.views``.
The HTTP wrapping (FileResponse, Content-Disposition) is the sole
responsibility of the view layer (``dashboard/views.py``).

Dependencies
------------
  - reportlab >= 4.0
  - Standard library only (io, datetime, textwrap)
"""

import io
import textwrap
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ────────────────────────────────────────────────────────────
#  Colour palette — Obsidian Observatory tokens
# ────────────────────────────────────────────────────────────

_BG_DARK   = colors.HexColor("#0b0e14")   # layer-0
_BG_MID    = colors.HexColor("#13171f")   # layer-1
_BG_PANEL  = colors.HexColor("#1a1f2b")   # layer-2
_CYAN      = colors.HexColor("#99f7ff")   # accent-cyan
_EMERALD   = colors.HexColor("#a5ffb8")   # accent-emerald
_AMBER     = colors.HexColor("#fdd663")   # accent-amber
_RED       = colors.HexColor("#f28b82")   # error/abort
_WHITE     = colors.HexColor("#e8eaed")   # text-primary
_MUTED     = colors.HexColor("#8a9099")   # text-muted

# Archetype accent colours (mirrors genetic.py)
_ARCHETYPE_COLORS = {
    "skeptic":     colors.HexColor("#f28b82"),
    "optimist":    colors.HexColor("#a5ffb8"),
    "pragmatist":  colors.HexColor("#99f7ff"),
    "conservative":colors.HexColor("#fdd663"),
    "innovator":   colors.HexColor("#d177ff"),
    "moderator":   colors.HexColor("#e8eaed"),
}

_ARCHETYPE_LABELS = {
    "skeptic":      "Le Sceptique",
    "optimist":     "L'Optimiste",
    "pragmatist":   "Le Pragmatique",
    "conservative": "Le Conservateur",
    "innovator":    "L'Innovateur",
    "moderator":    "Modérateur Architecte",
}


# ────────────────────────────────────────────────────────────
#  Page templates (header + footer on each page)
# ────────────────────────────────────────────────────────────

def _build_page_template(doc, session_title: str):
    """Return a PageTemplate with header and footer callbacks."""

    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setPageCompression(0)   # ensure text is readable in raw bytes
        w, h = A4

        # ── Dark background ────────────────────────────────
        canvas.setFillColor(_BG_DARK)
        canvas.rect(0, 0, w, h, fill=1, stroke=0)

        # ── Header bar ────────────────────────────────────
        canvas.setFillColor(_BG_PANEL)
        canvas.rect(0, h - 20 * mm, w, 20 * mm, fill=1, stroke=0)

        canvas.setFillColor(_CYAN)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(15 * mm, h - 13 * mm, "MAS-D  ·  RAPPORT DE SESSION FORENSIQUE")

        canvas.setFillColor(_MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(w - 15 * mm, h - 13 * mm,
                               session_title[:60] if len(session_title) > 60
                               else session_title)

        # ── Footer bar ────────────────────────────────────
        canvas.setFillColor(_BG_PANEL)
        canvas.rect(0, 0, w, 12 * mm, fill=1, stroke=0)

        canvas.setFillColor(_MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 4 * mm, "Généré automatiquement · MAS-D Research Platform")
        canvas.drawRightString(w - 15 * mm, 4 * mm, f"Page {doc.page}")

        canvas.restoreState()

    frame = Frame(
        15 * mm,          # x
        15 * mm,          # y  (above footer)
        A4[0] - 30 * mm,  # width
        A4[1] - 38 * mm,  # height (below header)
        id="main",
        leftPadding=0,
        rightPadding=0,
        topPadding=5 * mm,
        bottomPadding=0,
    )
    return PageTemplate(id="main", frames=[frame], onPage=_on_page)


# ────────────────────────────────────────────────────────────
#  Style factory
# ────────────────────────────────────────────────────────────

def _make_styles():
    """Return a dict of ParagraphStyle objects."""
    base = getSampleStyleSheet()

    return {
        "title": ParagraphStyle(
            "title",
            fontName="Helvetica-Bold",
            fontSize=22,
            textColor=_CYAN,
            spaceAfter=2 * mm,
            leading=26,
            alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            fontName="Helvetica",
            fontSize=11,
            textColor=_MUTED,
            spaceAfter=6 * mm,
            leading=14,
            alignment=TA_LEFT,
        ),
        "section_header": ParagraphStyle(
            "section_header",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=_MUTED,
            spaceBefore=8 * mm,
            spaceAfter=3 * mm,
            leading=11,
            alignment=TA_LEFT,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=9,
            textColor=_WHITE,
            leading=13,
            spaceAfter=2 * mm,
            alignment=TA_JUSTIFY,
        ),
        "mono": ParagraphStyle(
            "mono",
            fontName="Courier",
            fontSize=8,
            textColor=_WHITE,
            leading=12,
            spaceAfter=1 * mm,
            alignment=TA_LEFT,
        ),
        "abort_banner": ParagraphStyle(
            "abort_banner",
            fontName="Helvetica-Bold",
            fontSize=14,
            textColor=_RED,
            spaceAfter=3 * mm,
            spaceBefore=4 * mm,
            leading=18,
            alignment=TA_CENTER,
        ),
        "abort_body": ParagraphStyle(
            "abort_body",
            fontName="Helvetica",
            fontSize=10,
            textColor=_AMBER,
            leading=14,
            spaceAfter=4 * mm,
            alignment=TA_JUSTIFY,
        ),
        "agent_label": ParagraphStyle(
            "agent_label",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=_WHITE,
            leading=11,
        ),
        "agent_sub": ParagraphStyle(
            "agent_sub",
            fontName="Helvetica",
            fontSize=7,
            textColor=_MUTED,
            leading=9,
        ),
        "meta": ParagraphStyle(
            "meta",
            fontName="Helvetica",
            fontSize=8,
            textColor=_MUTED,
            leading=11,
            spaceAfter=1 * mm,
        ),
        "speech_agent": ParagraphStyle(
            "speech_agent",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=_CYAN,
            spaceBefore=3 * mm,
            leading=10,
        ),
        "speech_content": ParagraphStyle(
            "speech_content",
            fontName="Helvetica",
            fontSize=8,
            textColor=_WHITE,
            leading=11,
            spaceAfter=1 * mm,
            leftIndent=4 * mm,
            alignment=TA_JUSTIFY,
        ),
    }


# ────────────────────────────────────────────────────────────
#  Agent matrix table
# ────────────────────────────────────────────────────────────

def _build_agent_matrix(agents_qs, styles) -> Table:
    """Build a styled Table representing the agent matrix."""
    header = [
        Paragraph("SLOT", styles["agent_sub"]),
        Paragraph("PROVIDER", styles["agent_sub"]),
        Paragraph("ARCHÉTYPE", styles["agent_sub"]),
    ]
    rows = [header]

    for agent in agents_qs:
        archetype_key = agent.archetype
        label = _ARCHETYPE_LABELS.get(archetype_key, archetype_key.upper())
        accent = _ARCHETYPE_COLORS.get(archetype_key, _WHITE)

        agent_para = ParagraphStyle(
            f"agent_{archetype_key}",
            parent=styles["agent_label"],
            textColor=accent,
        )
        rows.append([
            Paragraph(f"[{agent.slot_number}]", styles["meta"]),
            Paragraph(agent.provider.upper(), styles["meta"]),
            Paragraph(label, agent_para),
        ])

    col_widths = [20 * mm, 40 * mm, 80 * mm]
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        # Header row
        ("BACKGROUND",    (0, 0), (-1, 0), _BG_PANEL),
        ("TEXTCOLOR",     (0, 0), (-1, 0), _MUTED),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 7),
        # Data rows
        ("BACKGROUND",    (0, 1), (-1, -1), _BG_MID),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [_BG_MID, _BG_PANEL]),
        # Grid
        ("LINEBELOW",     (0, 0), (-1, -1), 0.25, colors.HexColor("#ffffff11")),
        # Padding
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


# ────────────────────────────────────────────────────────────
#  Public API
# ────────────────────────────────────────────────────────────

def generate_pdf_report(session) -> bytes:
    """
    Generate and return a forensic PDF report for *session* as ``bytes``.

    Parameters
    ----------
    session:
        A ``dashboard.models.Session`` instance.  Must have at least:
        - ``title``, ``topic``, ``token_budget``, ``status``
        - ``abort_justification`` (may be None / blank)
        - ``agents`` related manager (SessionAgent queryset)
        - ``created_at``, ``updated_at``

    Returns
    -------
    bytes
        Raw PDF binary blob suitable for writing to a file or
        streaming via ``django.http.FileResponse``.

    Notes
    -----
    This function is **synchronous** (CPU/I/O bound via ReportLab).
    When calling from an async Django view, wrap it with
    ``sync_to_async(generate_pdf_report)(session)``.
    """
    buffer = io.BytesIO()

    # ── Document setup ─────────────────────────────────────
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=20 * mm,
        bottomMargin=15 * mm,
        compress=0,
    )
    doc.addPageTemplates([_build_page_template(doc, session.title)])

    styles = _make_styles()
    story = []

    # ── Cover metadata ─────────────────────────────────────
    ts = session.created_at
    if ts is not None:
        ts_str = ts.strftime("%Y-%m-%d %H:%M UTC") if ts.tzinfo else ts.strftime("%Y-%m-%d %H:%M")
    else:
        ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    story.append(Paragraph(session.title, styles["title"]))
    story.append(Paragraph(f"Session Forensique — {ts_str}", styles["subtitle"]))
    story.append(HRFlowable(
        width="100%", thickness=1,
        color=_CYAN, spaceAfter=4 * mm,
    ))

    # ── ABORT banner (prominent, before everything else) ───
    if session.status == "ABORTED" and session.abort_justification:
        story.append(Spacer(1, 3 * mm))
        story.append(HRFlowable(width="100%", thickness=2, color=_RED))
        story.append(Paragraph(
            "⚠  DÉBAT INTERROMPU  ⚠",
            styles["abort_banner"],
        ))
        story.append(Paragraph(
            "Ce débat a été manuellement interrompu par le chercheur "
            "via le Justified Kill-Switch.  La justification enregistrée "
            "est reproduite ci-dessous.",
            styles["abort_body"],
        ))
        # The justification text itself
        story.append(Paragraph(
            f'"{session.abort_justification}"',
            ParagraphStyle(
                "abort_quote",
                parent=styles["abort_body"],
                fontName="Helvetica-BoldOblique",
                fontSize=11,
                textColor=_RED,
                leftIndent=6 * mm,
                rightIndent=6 * mm,
            ),
        ))
        story.append(HRFlowable(width="100%", thickness=2, color=_RED, spaceAfter=4 * mm))

    # ── Session metadata block ─────────────────────────────
    story.append(Paragraph("INFORMATIONS GÉNÉRALES", styles["section_header"]))
    meta_rows = [
        ["Statut", session.get_status_display()],
        ["Sujet", session.topic],
        ["Budget de tokens", f"{session.token_budget:,}"],
        ["Date de création", ts_str],
    ]
    meta_table = Table(meta_rows, colWidths=[40 * mm, 130 * mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (0, -1), _BG_PANEL),
        ("BACKGROUND",   (1, 0), (1, -1), _BG_MID),
        ("TEXTCOLOR",    (0, 0), (0, -1), _MUTED),
        ("TEXTCOLOR",    (1, 0), (1, -1), _WHITE),
        ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",     (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW",    (0, 0), (-1, -1), 0.25, colors.HexColor("#ffffff11")),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(meta_table)

    # ── Agent matrix ───────────────────────────────────────
    story.append(Paragraph("MATRICE DES AGENTS PARTICIPANTS", styles["section_header"]))
    agents_list = list(session.agents.all().order_by("slot_number"))

    if agents_list:
        story.append(_build_agent_matrix(agents_list, styles))
    else:
        story.append(Paragraph(
            "Aucun agent configuré pour cette session.", styles["body"]
        ))

    # ── Moderator note ─────────────────────────────────────
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "⬡  Modérateur Architecte — présent implicitement dans toutes les sessions "
        "(agent de protocole, non stocké en base).",
        ParagraphStyle(
            "mod_note",
            parent=styles["meta"],
            textColor=_MUTED,
            leftIndent=4 * mm,
            fontName="Helvetica-Oblique",
        ),
    ))

    # ── Summary / transcript section ───────────────────────
    story.append(Paragraph("RÉSUMÉ ET TRANSCRIPT DU DÉBAT", styles["section_header"]))
    story.append(HRFlowable(
        width="100%", thickness=0.5,
        color=_MUTED, spaceAfter=3 * mm,
    ))

    # Try to pull a transcript from the session if it exists as a field,
    # otherwise generate a placeholder summary.
    transcript = getattr(session, "transcript", None) or getattr(session, "chat_log", None)

    if transcript:
        # Transcript is stored — render each line
        lines = transcript if isinstance(transcript, list) else str(transcript).splitlines()
        for line in lines:
            line = str(line).strip()
            if not line:
                story.append(Spacer(1, 2 * mm))
                continue
            # Attempt to detect "AGENT: message" format
            if ":" in line and len(line.split(":", 1)[0]) < 30:
                agent_id, content = line.split(":", 1)
                agent_id = agent_id.strip()
                label = _ARCHETYPE_LABELS.get(agent_id.lower(), agent_id.upper())
                accent = _ARCHETYPE_COLORS.get(agent_id.lower(), _WHITE)
                story.append(Paragraph(
                    label,
                    ParagraphStyle(
                        f"sp_{agent_id}",
                        parent=styles["speech_agent"],
                        textColor=accent,
                    ),
                ))
                story.append(Paragraph(
                    content.strip(),
                    styles["speech_content"],
                ))
            else:
                story.append(Paragraph(line, styles["body"]))
    else:
        # No transcript stored — generate summary from session data
        status_desc = {
            "SUCCESS":  "Le débat s'est conclu avec succès — une résolution a été atteinte.",
            "ABORTED":  "Le débat a été interrompu prématurément via le Justified Kill-Switch.",
        }.get(session.status, f"Statut final : {session.status}")

        story.append(Paragraph(status_desc, styles["body"]))

        if agents_list:
            participants = ", ".join(
                f"{_ARCHETYPE_LABELS.get(a.archetype, a.archetype)} ({a.provider.upper()})"
                for a in agents_list
            )
            story.append(Paragraph(
                f"Participants : {participants}.",
                styles["body"],
            ))

        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            "Note : Le transcript complet n'est pas disponible dans cette version "
            "de la plateforme. Les événements bruts sont enregistrés dans les logs "
            "de la session SSE pour archivage.",
            ParagraphStyle(
                "note",
                parent=styles["meta"],
                fontName="Helvetica-Oblique",
                textColor=_MUTED,
                leftIndent=4 * mm,
            ),
        ))

    # ── Build PDF ──────────────────────────────────────────
    doc.build(story)
    return buffer.getvalue()
