"""
pptx_parser.py - Universal Parser for eLearning Storyboard PPTX files.
Handles both:
1. Video Storyboards with character dialogues (Analytica, Dex, Narrator),
   including resolution of 'Same as OST' comic-bubble dialogues on slides.
2. Interactive / Gamified Storyboards (Highway Hero, Vibration Detective),
   including VO, Customer, Service Advisor, Technician, and Feedback notes.
"""

import os
import re
import csv
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional

NS = {
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
}

def clean_vo_text(text: str) -> str:
    """Removes XML/animation tags like <AX1>, clean excess whitespace, bullets."""
    if not text:
        return ""
    # Remove animation tags like <AX1>, <AX2>
    cleaned = re.sub(r'<AX\d+>', '', text)
    # Remove unicode artifacts / replacement characters
    cleaned = (cleaned
               .replace('\ufffd', "'")
               .replace('\u2018', "'")
               .replace('\u2019', "'")
               .replace('\u201c', '"')
               .replace('\u201d', '"')
               .replace('\u2026', '...')
               .replace('\u2013', '-')
               .replace('\u2014', '-'))
    # Remove bullet markers
    cleaned = re.sub(r'^[•\-\*]\s*', '', cleaned, flags=re.MULTILINE)
    # Normalize spaces
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # Normalize multiple newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

def is_visual_direction(text: str) -> bool:
    """Detects if a text block is developer / visual layout instruction."""
    t_lower = text.lower().strip()
    direction_starters = (
        'visual', 'visuals', 'visual note', 'visuals notes', 'animation direction',
        'fade in', 'screen starts', 'query:', 'start with an office', 'highlight',
        'show the', 'show ', 'build the', 'establish the', 'suggested ost',
        'verbatim client script', 'male vo', 'female vo', 'slide 7: final',
        'revised design', 'click play to begin', 'test track area'
    )
    if any(t_lower.startswith(prefix) for prefix in direction_starters):
        return True
    if 'verbatim client script' in t_lower:
        return True
    return False

def parse_pptx_storyboard(pptx_path: str, preset: str = "auto") -> List[Dict[str, Any]]:
    """Parses any storyboard PPTX with optional preset ('tata', 'eicher', 'auto') and returns a list of audio items."""
    records = []
    
    # Resolve preset
    active_preset = "tata"
    try:
        cfg_path = Path("config/voices.json")
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                active_preset = json.load(f).get("active_preset", "tata")
    except Exception:
        pass
    if preset == "auto":
        preset = active_preset
    
    with zipfile.ZipFile(pptx_path, 'r') as z:
        namelist = set(z.namelist())
        
        # 1. Determine slide order from presentation.xml
        slides_ordered = []
        if 'ppt/presentation.xml' in namelist and 'ppt/_rels/presentation.xml.rels' in namelist:
            pres_tree = ET.fromstring(z.read('ppt/presentation.xml'))
            rels_tree = ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))
            rel_map = {r.attrib['Id']: r.attrib['Target'] for r in rels_tree}
            
            for s in pres_tree.findall('.//p:sldId', NS):
                rid = s.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                target = rel_map.get(rid)
                if target:
                    # Target is typically 'slides/slide1.xml'
                    norm_path = 'ppt/' + target if not target.startswith('ppt/') else target
                    slides_ordered.append(norm_path)
        
        if not slides_ordered:
            # Fallback natural sort of slide files
            slide_files = [f for f in namelist if f.startswith('ppt/slides/slide') and f.endswith('.xml')]
            def get_num(s):
                m = re.search(r'slide(\d+)\.xml', s)
                return int(m.group(1)) if m else 9999
            slides_ordered = sorted(slide_files, key=get_num)
            
        print(f"[Parser] Found {len(slides_ordered)} slides in presentation. Active preset: {preset}")
        
        # 2. Iterate each slide
        for slide_idx, slide_file in enumerate(slides_ordered, start=1):
            # Check relationships for notesSlide
            rel_file = slide_file.replace('ppt/slides/', 'ppt/slides/_rels/') + '.rels'
            notes_file = None
            if rel_file in namelist:
                rel_tree = ET.fromstring(z.read(rel_file))
                for r in rel_tree:
                    if 'notesSlide' in r.attrib.get('Type', ''):
                        target = r.attrib.get('Target', '')
                        # Target is typically '../notesSlides/notesSlide1.xml'
                        target_clean = os.path.normpath(os.path.join('ppt/slides', target)).replace('\\', '/')
                        notes_file = target_clean
                        break
                        
            notes_text = ""
            if notes_file and notes_file in namelist:
                n_tree = ET.fromstring(z.read(notes_file))
                notes_paras = []
                for p in n_tree.findall('.//a:p', NS):
                    p_txt = ''.join([t.text for t in p.findall('.//a:t', NS) if t.text])
                    if p_txt.strip():
                        notes_paras.append(p_txt.strip())
                notes_text = '\n'.join(notes_paras)
            
            # Read shapes on slide canvas
            slide_shapes = []
            if slide_file in namelist:
                s_tree = ET.fromstring(z.read(slide_file))
                for sp in s_tree.findall('.//p:sp', NS):
                    txBody = sp.find('p:txBody', NS)
                    if txBody is not None:
                        paras = []
                        for p in txBody.findall('.//a:p', NS):
                            p_txt = ''.join([t.text for t in p.findall('.//a:t', NS) if t.text])
                            if p_txt.strip():
                                paras.append(p_txt.strip())
                        if paras:
                            shape_full = ' '.join(paras)
                            slide_shapes.append(shape_full)

            # --- PARSING DECISION LOGIC ---
            notes_clean = re.sub(r'^\s*Frame\s*number\s*:\s*\d+\s*[\|\-]?\s*', '', notes_text, flags=re.IGNORECASE).strip()
            notes_lower = notes_clean.lower()
            
            # Skip explicit 'No VO' or empty notes with no shapes
            if notes_lower.startswith("no vo") or notes_lower in ["na", "n/a", "no audio"]:
                continue

            # CASE 0: Structured Storyboard Format (Dex / Analytica with 'Audio speaker:' and 'Exact VO:')
            if 'exact vo:' in notes_lower or 'audio speaker:' in notes_lower:
                spk_m = re.search(r'Audio\s*speaker\s*:\s*([^\n\r]+)', notes_clean, re.IGNORECASE)
                raw_spk = spk_m.group(1).strip() if spk_m else ('Dex' if preset == 'tata' else 'Narrator')
                spk_clean = re.sub(r'[^a-zA-Z0-9_\s]', '', raw_spk).strip()

                if spk_clean.lower() in ['none', 'no vo', 'n/a', 'na', 'no audio', 'music only', 'light background music', 'bg music']:
                    continue

                vo_m = re.search(r'Exact\s*VO\s*:\s*([\s\S]+?)(?=(?:Exact\s*OST\s*:|Descriptive\s*OST\s*:|OST\s*:|Animation\s*:|Visual\s*:|Visual\s*&\s*animation\s*:|Frame\s*number\s*:|$))', notes_clean, re.IGNORECASE)
                if vo_m:
                    vo_text = vo_m.group(1).strip()
                else:
                    vo_m2 = re.search(r'VO\s*:\s*([\s\S]+?)(?=(?:Exact\s*OST\s*:|Descriptive\s*OST\s*:|OST\s*:|Animation\s*:|Visual\s*:|Visual\s*&\s*animation\s*:|Frame\s*number\s*:|$))', notes_clean, re.IGNORECASE)
                    vo_text = vo_m2.group(1).strip() if vo_m2 else ''

                vo_text = clean_vo_text(vo_text)
                if not vo_text or vo_text.lower() in ['no vo', 'none', 'n/a', 'na']:
                    continue

                if 'analytica' in spk_clean.lower():
                    final_spk = 'Analytica'
                elif 'dex' in spk_clean.lower():
                    final_spk = 'Dex'
                elif preset == 'tata' and ('narrator' in spk_clean.lower() or not spk_clean):
                    final_spk = 'Dex'
                else:
                    final_spk = spk_clean.title() or ('Dex' if preset == 'tata' else 'Narrator')

                item_id = f"S{slide_idx:03d}_{final_spk.replace(' ', '')}_01"
                records.append({
                    "id": item_id,
                    "slide": slide_idx,
                    "speaker": final_spk,
                    "type": "dialogue" if final_spk in ["Dex", "Analytica"] else "narration",
                    "text": vo_text,
                    "source": "speaker_notes",
                    "approved": True
                })
                continue
            
            # CASE A: Notes say 'Same as OST' or empty notes with comic dialogue on slide
            if 'same as ost' in notes_lower or (not notes_clean and any('verbatim client script' in s.lower() for s in slide_shapes)):
                # Inspect slide shapes for dialogue
                dialogue_shapes = []
                female_marker = any('female vo' in s.lower() or 'sara' in s.lower() for s in slide_shapes)
                male_marker = any('male vo' in s.lower() or 'neo' in s.lower() for s in slide_shapes)
                
                for s_txt in slide_shapes:
                    if is_visual_direction(s_txt):
                        continue
                    if s_txt.isdigit():  # Slide number indicator
                        continue
                    dialogue_shapes.append(clean_vo_text(s_txt))
                    
                # If we have 2 shapes in a dialogue slide (e.g. Analytica asks, Dex answers)
                if len(dialogue_shapes) == 2:
                    records.append({
                        "id": f"S{slide_idx:03d}_Analytica_01",
                        "slide": slide_idx,
                        "speaker": "Analytica",
                        "type": "dialogue",
                        "text": dialogue_shapes[0],
                        "source": "slide_bubble",
                        "approved": True
                    })
                    records.append({
                        "id": f"S{slide_idx:03d}_Dex_02",
                        "slide": slide_idx,
                        "speaker": "Dex",
                        "type": "dialogue",
                        "text": dialogue_shapes[1],
                        "source": "slide_bubble",
                        "approved": True
                    })
                elif len(dialogue_shapes) == 1:
                    default_lead = "Dex" if preset == "tata" else "Narrator"
                    speaker = "Analytica" if female_marker and not male_marker else ("Dex" if male_marker else default_lead)
                    records.append({
                        "id": f"S{slide_idx:03d}_{speaker}_01",
                        "slide": slide_idx,
                        "speaker": speaker,
                        "type": "dialogue" if speaker in ["Dex", "Analytica"] else "narration",
                        "text": dialogue_shapes[0],
                        "source": "slide_bubble",
                        "approved": True
                    })
                elif len(dialogue_shapes) > 2:
                    for d_idx, d_text in enumerate(dialogue_shapes, start=1):
                        records.append({
                            "id": f"S{slide_idx:03d}_Dialogue_{d_idx:02d}",
                            "slide": slide_idx,
                            "speaker": "Analytica" if d_idx % 2 == 1 else "Dex",
                            "type": "dialogue",
                            "text": d_text,
                            "source": "slide_bubble",
                            "approved": True
                        })
                continue
                
            # CASE B: Standard notes with explicit prefixes (VO:, Dex:, Customer:, Service Advisor:, Feedback:, etc.)
            if notes_clean:
                lines = [l.strip() for l in notes_clean.split('\n') if l.strip()]
                
                first_line = lines[0].lower() if lines else ""
                if first_line.startswith("audio:") and any(na in first_line for na in ['na', 'bg music', 'light background music', 'no audio']):
                    continue
                if first_line in ['no vo', 'none', 'na', 'n/a']:
                    continue
                
                prefix_pattern = re.compile(
                    r'^(Dex(?:\s+audio)?|Analytica|Narrator(?:\s+audio)?|Speaker:\s*Dex|Customer|Service\s+Advisor|Technician|VO|Voice\s+Over|Activity\s+VO|Incorrect\s+Feedback(?:\s+VO)?|Correct\s+Feedback(?:\s+VO)?|Audio)\s*:\s*(.*)',
                    re.IGNORECASE
                )
                
                parsed_items = []
                current_speaker = None
                current_type = "narration"
                current_text = []
                
                for line in lines:
                    if line.isdigit():
                        continue
                    if is_visual_direction(line):
                        continue
                    line_low = line.lower()
                    if line_low.startswith('visual & animation direction') or line_low.startswith('animation:') or line_low.startswith('exact ost:') or line_low.startswith('descriptive ost:'):
                        break
                    if line_low.startswith('correct answers are highlighted'):
                        break
                        
                    m = prefix_pattern.match(line)
                    if m:
                        if current_speaker and current_text:
                            full_txt = clean_vo_text(' '.join(current_text))
                            if full_txt and full_txt.lower() not in ['no vo', 'none', 'same as ost.', 'same as ost']:
                                parsed_items.append((current_speaker, current_type, full_txt))
                            current_text = []
                            
                        raw_prefix = m.group(1).strip()
                        raw_content = m.group(2).strip()
                        
                        p_low = raw_prefix.lower()
                        if 'dex' in p_low:
                            current_speaker = "Dex"
                            current_type = "dialogue"
                        elif 'analytica' in p_low:
                            current_speaker = "Analytica"
                            current_type = "dialogue"
                        elif 'customer' in p_low:
                            current_speaker = "Customer"
                            current_type = "dialogue"
                        elif 'service advisor' in p_low:
                            current_speaker = "Service Advisor"
                            current_type = "dialogue"
                        elif 'technician' in p_low:
                            current_speaker = "Technician"
                            current_type = "dialogue"
                        elif 'incorrect' in p_low:
                            current_speaker = "Dex" if preset == "tata" else "Narrator"
                            current_type = "incorrect_feedback"
                        elif 'correct' in p_low:
                            current_speaker = "Dex" if preset == "tata" else "Narrator"
                            current_type = "correct_feedback"
                        elif 'vo' in p_low or 'narrator' in p_low or 'audio' in p_low:
                            current_speaker = "Dex" if preset == "tata" else "Narrator"
                            current_type = "narration"
                        else:
                            current_speaker = "Dex" if preset == "tata" else "Narrator"
                            current_type = "narration"
                            
                        if raw_content:
                            current_text.append(raw_content)
                    else:
                        if current_speaker:
                            current_text.append(line)
                        else:
                            current_speaker = "Dex" if preset == "tata" else "Narrator"
                            current_type = "narration"
                            current_text.append(line)
                            
                # Flush final
                if current_speaker and current_text:
                    full_txt = clean_vo_text(' '.join(current_text))
                    if full_txt and full_txt.lower() not in ['no vo', 'none', 'same as ost.', 'same as ost']:
                        parsed_items.append((current_speaker, current_type, full_txt))
                        
                for item_idx, (spk, typ, txt) in enumerate(parsed_items, start=1):
                    if '?' in txt and typ == "narration":
                        typ = "question"
                        
                    clean_spk = spk.replace(" ", "")
                    item_id = f"S{slide_idx:03d}_{clean_spk}_{item_idx:02d}"
                    records.append({
                        "id": item_id,
                        "slide": slide_idx,
                        "speaker": spk,
                        "type": typ,
                        "text": txt,
                        "source": "speaker_notes",
                        "approved": True
                    })
                    
    print(f"[Parser] Extracted {len(records)} total VO items from {pptx_path}")
    return records

def export_manifest(records: List[Dict[str, Any]], output_json: str, output_csv: str):
    """Exports parsed records to JSON and CSV formats."""
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    
    # Save JSON
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump({"total_items": len(records), "items": records}, f, indent=2, ensure_ascii=False)
        
    # Save CSV
    if records:
        keys = ["id", "slide", "speaker", "type", "text", "source", "approved"]
        with open(output_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for r in records:
                writer.writerow({k: r.get(k, '') for k in keys})
                
    print(f"[Parser] Manifest written to {output_json} and {output_csv}")
