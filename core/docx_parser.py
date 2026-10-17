"""
docx_parser.py - Universal Parser for eLearning Storyboard Word Documents (.docx).
Supports both:
1. Video Storyboards (Tatacomp mode) with characters (Dex, Analytica, Narrator).
2. Interactive / Gamified Storyboards (Eicher mode) with characters (Narrator, Customer, Service Advisor, Technician, Feedback VO).
Supports two extraction strategies:
- Strategy 1: Table-based extraction with intelligent column detection (Slide, Speaker, VO Script, Visuals).
- Strategy 2: Screenplay & Heading-based extraction (Slide 1, Speaker: Dialogue, bold prefixes).
Also includes generation of the official downloadable Word storyboard template.
"""

import os
import re
import json
import csv
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

def clean_vo_text(text: str) -> str:
    """Removes XML/animation tags, excess whitespace, typography quirks, and bullets."""
    if not text:
        return ""
    # Remove animation tags like <AX1>, <AX2>
    cleaned = re.sub(r'<AX\d+>', '', text)
    # Remove unicode artifacts / curly quotes
    cleaned = (cleaned
               .replace('\ufffd', "'")
               .replace('\u2018', "'")
               .replace('\u2019', "'")
               .replace('\u201c', '"')
               .replace('\u201d', '"')
               .replace('\u2026', '...')
               .replace('\u2013', '-')
               .replace('\u2014', '-'))
    # Remove bullet markers at line starts
    cleaned = re.sub(r'^[•\-\*]\s*', '', cleaned, flags=re.MULTILINE)
    # Normalize spaces
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # Normalize multiple newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

def is_visual_direction(text: str) -> bool:
    """Detects if a text block is a visual/developer layout direction rather than voiceover."""
    t_lower = text.lower().strip()
    direction_starters = (
        'visual', 'visuals', 'visual note', 'visuals notes', 'animation direction',
        'fade in', 'screen starts', 'query:', 'start with an office', 'highlight',
        'show the', 'show ', 'build the', 'establish the', 'suggested ost',
        'verbatim client script', 'male vo', 'female vo', 'slide 7: final',
        'revised design', 'click play to begin', 'test track area', 'on screen text',
        'on-screen text', 'ost:', 'bg music:', 'sfx:', 'developer note:'
    )
    if any(t_lower.startswith(prefix) for prefix in direction_starters):
        return True
    if 'verbatim client script' in t_lower:
        return True
    return False

def detect_speaker_and_type(raw_speaker: str, text: str, preset: str = "auto") -> Tuple[str, str]:
    """Classifies the character and dialogue type based on raw speaker string and text context."""
    s_low = (raw_speaker or "").lower().strip()
    t_low = (text or "").lower().strip()
    is_tata = preset in ("tata", "tatacomp")

    # Specific characters take priority
    if 'analytica' in s_low:
        typ = "question" if '?' in text else "dialogue"
        return "Analytica", typ
    if 'dex' in s_low:
        typ = "question" if '?' in text else "dialogue"
        return "Dex", typ
    if 'service advisor' in s_low or 'advisor' in s_low:
        return "Service Advisor", "dialogue"
    if 'technician' in s_low or 'tech' in s_low:
        return "Technician", "dialogue"
    if 'customer' in s_low:
        return "Customer", "dialogue"

    # Feedback detection for interactive/gamified modules (primarily Eicher)
    if 'feedback' in s_low:
        return "Feedback VO", "feedback"
    if 'incorrect' in s_low or re.search(r'\b(incorrect|try again|wrong answer)\b', t_low[:35]):
        fallback_spk = "Dex" if is_tata else "Narrator"
        return fallback_spk, "incorrect_feedback"
    if re.search(r'\b(that is correct|that\'s correct|correct answer|well done|good job)\b', t_low[:35]) or re.match(r'^correct[!.,]', t_low):
        fallback_spk = "Dex" if is_tata else "Narrator"
        return fallback_spk, "correct_feedback"

    # Standard Narrator / VO
    if any(k in s_low for k in ['narrator', 'vo', 'voiceover', 'voice over', 'audio', 'host', 'anchor']):
        typ = "question" if '?' in text else "narration"
        return ("Dex", typ) if is_tata else ("Narrator", typ)

    # If raw_speaker is empty or generic
    if not s_low:
        typ = "question" if '?' in text else "narration"
        return ("Dex", typ) if is_tata else ("Narrator", typ)

    # Custom character fallback
    custom_name = raw_speaker.strip().title()
    typ = "question" if '?' in text else "dialogue"
    return custom_name, typ

def split_multispeaker_cell(cell_text: str) -> List[Tuple[Optional[str], str]]:
    """
    Detects if a single text block contains multiple speakers formatted like:
    Dex: First dialogue line
    Analytica: Second dialogue line
    """
    lines = [line.strip() for line in cell_text.splitlines() if line.strip()]
    speaker_pattern = re.compile(
        r'^(Dex|Analytica|Narrator|Customer|Service Advisor|Technician|Feedback VO|VO|Voiceover)\s*[:\-–—]\s*(.*)$',
        re.IGNORECASE
    )

    results = []
    current_speaker = None
    current_lines = []

    for line in lines:
        match = speaker_pattern.match(line)
        if match:
            if current_lines:
                results.append((current_speaker, ' '.join(current_lines)))
                current_lines = []
            current_speaker = match.group(1).strip()
            remainder = match.group(2).strip()
            if remainder:
                current_lines.append(remainder)
        else:
            current_lines.append(line)

    if current_lines:
        results.append((current_speaker, ' '.join(current_lines)))

    return results

def _parse_timecode_string(t_str: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Parses timecode strings like:
      - '00:00:09.200 00:00:27.220'
      - '00:00:09.200 - 00:00:27.220'
      - '00:00:09.200\n00:00:27.220'
    Returns (start_sec, end_sec) or (None, None).
    """
    if not t_str:
        return None, None
    parts = re.findall(r'(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d+))?', t_str)
    if len(parts) >= 2:
        def to_sec(p):
            h, m, s = int(p[0]), int(p[1]), int(p[2])
            ms = float('0.' + p[3]) if p[3] else 0.0
            return round(h * 3600 + m * 60 + s + ms, 3)
        return to_sec(parts[0]), to_sec(parts[1])
    elif len(parts) == 1:
        def to_sec(p):
            h, m, s = int(p[0]), int(p[1]), int(p[2])
            ms = float('0.' + p[3]) if p[3] else 0.0
            return round(h * 3600 + m * 60 + s + ms, 3)
        return to_sec(parts[0]), None
    return None, None

def _parse_docx_tables(doc: Document, preset: str = "auto") -> List[Dict[str, Any]]:
    """Strategy 1: Table-based extraction with intelligent column header mapping and timecode awareness."""
    records = []
    slide_counter = 1

    for table_idx, table in enumerate(doc.tables, start=1):
        if len(table.rows) < 2:
            continue

        # Inspect first 2 rows for header detection
        header_row_idx = -1
        slide_col = -1
        time_col = -1
        speaker_col = -1
        delivery_col = -1
        script_col = -1
        visual_col = -1

        for r_idx in range(min(2, len(table.rows))):
            row_cells = [c.text.strip().lower() for c in table.rows[r_idx].cells]
            cand_slide = -1
            cand_time = -1
            cand_speaker = -1
            cand_delivery = -1
            cand_script = -1
            cand_visual = -1

            for c_idx, cell_text in enumerate(row_cells):
                lower = cell_text.lower().strip()
                words = set(re.findall(r'[a-z0-9#]+', lower))

                is_time = any(w in words for w in ['start', 'end', 'time', 'timecode', 'timing', 'duration', 'tc', 'timestamp']) or 'start / end' in lower or 'start/end' in lower
                is_visual = any(w in words for w in ['visual', 'visuals', 'ost', 'notes', 'action', 'animation', 'graphics', 'description']) or 'on-screen' in lower or 'on screen' in lower
                is_delivery = any(w in words for w in ['delivery', 'style', 'format', 'mode'])
                is_speaker = any(w in words for w in ['speaker', 'character', 'talent', 'actor', 'who', 'role', 'voice']) and not any(w in words for w in ['script', 'dialogue', 'audio', 'text'])
                is_slide = any(w in words for w in ['slide', 'screen', 'scene', 'page', 'sno', 'srno', 'id', 'item', 'no']) or lower in ['id', '#', 'sno', 'sr.no', 'sr no']

                script_kws = ['spoken text', 'spoken', 'text', 'script', 'audio', 'dialogue', 'narration', 'voiceover', 'vo', 'speech', 'content', 'transcript', 'exact vo', 'exact dialogue']
                is_script = (any(kw in lower for kw in script_kws) or any(w in words for w in script_kws)) and not is_visual and not is_delivery and not is_speaker and not is_time

                if is_time and cand_time == -1:
                    cand_time = c_idx
                elif is_visual and cand_visual == -1:
                    cand_visual = c_idx
                elif is_delivery and cand_delivery == -1:
                    cand_delivery = c_idx
                elif is_script and cand_script == -1:
                    cand_script = c_idx
                elif is_speaker and cand_speaker == -1:
                    cand_speaker = c_idx
                elif is_slide and cand_slide == -1:
                    cand_slide = c_idx

            # If we found script column or both speaker and time
            if cand_script != -1 or (cand_speaker != -1 and cand_time != -1):
                if cand_script == -1:
                    for c_idx in range(len(row_cells)):
                        if c_idx not in (cand_slide, cand_time, cand_speaker, cand_delivery, cand_visual):
                            cand_script = c_idx
                            break

                slide_col = cand_slide
                time_col = cand_time
                speaker_col = cand_speaker
                delivery_col = cand_delivery
                script_col = cand_script
                visual_col = cand_visual
                header_row_idx = r_idx
                break

        # If no script column found, this table is likely not a storyboard table
        if script_col == -1 or header_row_idx == -1:
            continue

        current_slide_num = slide_counter
        table_items_count = 0

        for r_idx in range(header_row_idx + 1, len(table.rows)):
            row = table.rows[r_idx]
            num_cells = len(row.cells)

            # 1. Slide Number / ID
            if slide_col != -1 and slide_col < num_cells:
                raw_slide = row.cells[slide_col].text.strip()
                num_match = re.search(r'\d+', raw_slide)
                if num_match:
                    try:
                        current_slide_num = int(num_match.group(0))
                    except ValueError:
                        pass

            # 2. Reference Timecodes
            s_time, e_time = None, None
            raw_time_str = ""
            if time_col != -1 and time_col < num_cells:
                raw_time_str = row.cells[time_col].text.strip()
                s_time, e_time = _parse_timecode_string(raw_time_str)

            # 3. Speaker
            raw_speaker = ""
            if speaker_col != -1 and speaker_col < num_cells:
                raw_speaker = row.cells[speaker_col].text.strip()

            # 4. Delivery type
            raw_delivery = ""
            if delivery_col != -1 and delivery_col < num_cells:
                raw_delivery = row.cells[delivery_col].text.strip().lower()

            # 5. Audio / Script Text
            raw_script = ""
            if script_col < num_cells:
                raw_script = row.cells[script_col].text.strip()

            if not raw_script:
                continue

            # Check if script contains developer/visual directions
            if is_visual_direction(raw_script):
                continue
            if raw_script.lower() in ('same as ost.', 'n/a', 'none', '-'):
                continue

            # Check for multiple speakers in single cell
            split_chunks = split_multispeaker_cell(raw_script)
            for sub_spk, sub_txt in split_chunks:
                eff_spk = sub_spk or raw_speaker
                cleaned_text = clean_vo_text(sub_txt)
                if not cleaned_text or cleaned_text.lower() == 'same as ost.':
                    continue

                spk, typ = detect_speaker_and_type(eff_spk, cleaned_text, preset=preset)
                if raw_delivery in ('narration', 'dialogue', 'question'):
                    typ = raw_delivery

                table_items_count += 1
                clean_spk_id = spk.replace(" ", "")
                item_id = f"S{current_slide_num:03d}_{clean_spk_id}_{table_items_count:02d}"

                rec = {
                    "id": item_id,
                    "slide": current_slide_num,
                    "speaker": spk,
                    "type": typ,
                    "text": cleaned_text,
                    "source": "word_document",
                    "approved": True
                }
                if s_time is not None:
                    rec["start_time"] = s_time
                if e_time is not None:
                    rec["end_time"] = e_time
                if raw_time_str:
                    rec["timecode"] = raw_time_str.replace('\n', ' ')

                records.append(rec)

        slide_counter = current_slide_num + 1

    return records

def _parse_docx_paragraphs(doc: Document, preset: str = "auto") -> List[Dict[str, Any]]:
    """Strategy 2: Screenplay & Heading-based extraction for non-table Word scripts."""
    records = []
    current_slide = 1
    slide_item_counter = 0

    slide_heading_pattern = re.compile(
        r'^(?:slide|screen|scene|frame|module\s*slide)\s*#?\s*(\d+)[:\-\s]*(.*)$',
        re.IGNORECASE
    )
    speaker_line_pattern = re.compile(
        r'^(Dex|Analytica|Narrator|Customer|Service Advisor|Technician|Feedback VO|VO|Voiceover)\s*[:\-–—]\s*(.*)$',
        re.IGNORECASE
    )

    for p in doc.paragraphs:
        txt = p.text.strip()
        if not txt:
            continue

        # 1. Check for Slide Delimiters
        slide_match = slide_heading_pattern.match(txt)
        if slide_match:
            try:
                current_slide = int(slide_match.group(1))
                slide_item_counter = 0
            except ValueError:
                pass
            continue

        # Check for Heading styles
        if p.style and p.style.name and p.style.name.startswith('Heading'):
            num_match = re.search(r'\d+', txt)
            if num_match:
                try:
                    current_slide = int(num_match.group(0))
                    slide_item_counter = 0
                except ValueError:
                    pass
                continue

        # Skip visual instructions
        if is_visual_direction(txt):
            continue

        # 2. Check for Speaker Prefix
        speaker_match = speaker_line_pattern.match(txt)
        raw_speaker = ""
        script_text = ""

        if speaker_match:
            raw_speaker = speaker_match.group(1).strip()
            script_text = speaker_match.group(2).strip()
        else:
            # Check if first run is bold (common script style: **Speaker:** Dialogue)
            if len(p.runs) >= 2 and p.runs[0].bold and p.runs[0].text.strip().endswith(':'):
                potential_spk = p.runs[0].text.strip().rstrip(':')
                raw_speaker = potential_spk
                script_text = ''.join([r.text for r in p.runs[1:]]).strip()
            else:
                # Untagged line - default to Narrator
                raw_speaker = "Narrator"
                script_text = txt

        cleaned_text = clean_vo_text(script_text)
        if not cleaned_text or cleaned_text.lower() in ('same as ost.', 'n/a', '-'):
            continue

        spk, typ = detect_speaker_and_type(raw_speaker, cleaned_text, preset=preset)
        slide_item_counter += 1
        clean_spk_id = spk.replace(" ", "")
        item_id = f"S{current_slide:03d}_{clean_spk_id}_{slide_item_counter:02d}"

        records.append({
            "id": item_id,
            "slide": current_slide,
            "speaker": spk,
            "type": typ,
            "text": cleaned_text,
            "source": "word_document",
            "approved": True
        })

    return records

def parse_docx_storyboard(docx_path: str, preset: str = "auto") -> List[Dict[str, Any]]:
    """
    Parses any Word document storyboard (.docx) and returns a list of audio items.
    Uses Strategy 1 (Tables) first; if fewer than 2 items found, falls back to Strategy 2 (Headings & Screenplay).
    """
    doc = Document(docx_path)

    # Strategy 1: Table parsing
    records = _parse_docx_tables(doc, preset=preset)
    if records:
        print(f"[DocxParser] Extracted {len(records)} items using Table Strategy from {docx_path}")
        return records

    # Strategy 2: Screenplay / Paragraph parsing fallback
    records = _parse_docx_paragraphs(doc, preset=preset)
    print(f"[DocxParser] Extracted {len(records)} items using Screenplay Strategy from {docx_path}")
    return records

def generate_sample_docx_template(output_path: str) -> str:
    """Generates the official downloadable Storyboard template Word document."""
    doc = Document()

    # Title
    title = doc.add_heading("Keenmind Automation — Official Storyboard Template", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    intro = doc.add_paragraph(
        "This template is pre-formatted for 100% turnkey ingestion by Keenmind Automation Studio. "
        "Fill in your slide numbers, characters, and voiceover scripts below. "
        "The parser automatically separates visual notes from spoken dialogue."
    )
    intro.runs[0].font.size = Pt(10)
    intro.runs[0].font.color.rgb = RGBColor(100, 116, 139)

    # Section 1: Tatacomp Mode (Video Module)
    h1 = doc.add_heading("Section 1: Video Module (Tatacomp Mode)", level=1)
    doc.add_paragraph("Recognized characters: Dex, Analytica, Narrator (or custom characters assigned in preset).")

    t1 = doc.add_table(rows=1, cols=4)
    t1.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr_cells1 = t1.rows[0].cells
    hdr_cells1[0].text = "Slide #"
    hdr_cells1[1].text = "Character / Speaker"
    hdr_cells1[2].text = "Voiceover Script (Spoken Dialogue)"
    hdr_cells1[3].text = "Visuals / OST (Ignored by VO)"

    # Format header row
    for cell in hdr_cells1:
        cell.paragraphs[0].runs[0].font.bold = True
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(30, 41, 59)

    sample_rows1 = [
        ("1", "Narrator", "Welcome to the Electric Vehicle Powertrain diagnostic module. In this session, we will inspect the primary inverter.", "Show 3D exploded view of inverter housing with ambient glow."),
        ("2", "Dex", "Notice the secondary capacitor bank on the upper rail? High voltage must be discharged before testing.", "Dex points toward the terminal block with insulated probe."),
        ("2", "Analytica", "Telemetric logs confirm residual charge of forty-eight volts. Grounding protocol is mandatory.", "Blue circuit diagram highlights grounding path on screen."),
        ("3", "Narrator", "What is the recommended threshold before proceeding with terminal disassembly?", "Interactive quiz prompt appears with timer.")
    ]

    for r in sample_rows1:
        row = t1.add_row().cells
        row[0].text = r[0]
        row[1].text = r[1]
        row[2].text = r[2]
        row[3].text = r[3]

    doc.add_paragraph("")  # Spacer

    # Section 2: Eicher Mode (Interactive / Gamified Module)
    h2 = doc.add_heading("Section 2: Interactive Module (Eicher Mode)", level=1)
    doc.add_paragraph("Recognized characters: Narrator, Customer, Service Advisor, Technician, Feedback VO.")

    t2 = doc.add_table(rows=1, cols=4)
    t2.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr_cells2 = t2.rows[0].cells
    hdr_cells2[0].text = "Screen #"
    hdr_cells2[1].text = "Speaker"
    hdr_cells2[2].text = "Audio Script"
    hdr_cells2[3].text = "Action / Notes"

    for cell in hdr_cells2:
        cell.paragraphs[0].runs[0].font.bold = True
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(30, 41, 59)

    sample_rows2 = [
        ("1", "Narrator", "Highway Hero: Scenario 1. A heavy commercial truck arrives at the service bay with abnormal vibration.", "Workshop bay exterior with service ramp."),
        ("1", "Customer", "Good morning! The steering vibrates intensely whenever I accelerate past sixty kilometers per hour.", "Customer character approaches service desk."),
        ("2", "Service Advisor", "Understood. Let us perform a wheel balancing and driveline inspection.", "Advisor notes symptoms on digital tablet."),
        ("3", "Narrator", "Correct Feedback: Exactly right! Checking driveline alignment resolves dynamic oscillation.", "Green checkmark appears on screen with positive chime."),
        ("3", "Narrator", "Incorrect Feedback: Not quite. Inspecting tire pressure alone will not address high-speed oscillation.", "Amber warning indicator highlights propshaft.")
    ]

    for r in sample_rows2:
        row = t2.add_row().cells
        row[0].text = r[0]
        row[1].text = r[1]
        row[2].text = r[2]
        row[3].text = r[3]

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_file))
    print(f"[DocxParser] Template generated at {out_file}")
    return str(out_file)
