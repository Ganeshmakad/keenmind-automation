"""
server.py - FastAPI service for Keenmind Automation Studio.
"""

import os
import shutil
import json
import hashlib
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Auto-discover local portable FFmpeg in ./bin if present
local_bin = Path(__file__).resolve().parent.parent / "bin"
if local_bin.exists() and str(local_bin) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")

from core.pptx_parser import parse_pptx_storyboard, export_manifest
from core.docx_parser import parse_docx_storyboard, generate_sample_docx_template
from core.voice_engine import VoiceEngine
from core.video_processor import VideoProcessor

app = FastAPI(
    title="Keenmind Audio & Video Automation Engine",
    description="REST API for automated storyboard VO extraction, TTS batching, and video voice replacement with ElevenLabs & FFmpeg.",
    version="1.1.0"
)

UPLOAD_DIR = Path("temp/uploads")
OUTPUT_DIR = Path("output")
CACHE_DIR = Path("cache")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

voice_engine = VoiceEngine(cache_dir=str(CACHE_DIR))
video_processor = VideoProcessor(temp_dir="temp")

class VoiceUpdateRequest(BaseModel):
    character: str
    voice_id: str
    voice_name: str
    settings: Dict[str, Any]

class AuditionRequest(BaseModel):
    text: str
    speaker: str
    preset: Optional[str] = None
    voice_id: Optional[str] = None
    voice_name: Optional[str] = None
    model_id: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    dry_run: bool = True
    overwrite: bool = False

class GenerateAudioRequest(BaseModel):
    manifest_path: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None
    preset: Optional[str] = None
    model_id: Optional[str] = None
    limit: Optional[int] = None
    dry_run: bool = True
    overwrite: bool = False

class AutoTagRequest(BaseModel):
    text: Optional[str] = None
    segments: Optional[List[Dict[str, Any]]] = None

class PreviewVoiceRequest(BaseModel):
    preset_id: Optional[str] = None
    preset: Optional[str] = None
    character: Optional[str] = None
    speaker: Optional[str] = None
    dialogue: Optional[str] = None
    text: Optional[str] = None
    voice_id: Optional[str] = None
    model_id: Optional[str] = None

STATIC_DIR = Path(__file__).parent / "static"

@app.get("/", response_class=FileResponse)
def serve_dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"message": "Keenmind Automation Engine API Running"})

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "Keenmind Automation Engine",
        "ffmpeg": shutil.which("ffmpeg") is not None
    }

@app.get("/api/status")
def get_system_status():
    key = voice_engine.get_api_key(force_reload=True)
    masked = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else ("Connected" if key else "Missing")

    cuda_active = False
    gpu_name = "None"
    try:
        import torch
        if torch.cuda.is_available():
            cuda_active = True
            gpu_name = torch.cuda.get_device_name(0)
    except Exception:
        pass

    cache_count = len(list(CACHE_DIR.glob("*.mp3"))) if CACHE_DIR.exists() else 0
    p_dict = voice_engine.get_pronunciation_dictionary()
    p_count = len(p_dict.get("global", {})) + sum(len(v) for v in p_dict.get("presets", {}).values() if isinstance(v, dict))

    return {
        "elevenlabs_configured": bool(key),
        "elevenlabs_key_masked": masked,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "cuda_available": cuda_active,
        "gpu_name": gpu_name,
        "cache_files_count": cache_count,
        "pronunciation_rules_count": p_count
    }

class PronunciationRuleRequest(BaseModel):
    word: str
    replacement: str
    preset: Optional[str] = None

class PronunciationDeleteRequest(BaseModel):
    word: str
    preset: Optional[str] = None

@app.get("/api/pronunciation")
def get_pronunciation_rules():
    """Returns the loaded Pronunciation Dictionary."""
    return voice_engine.get_pronunciation_dictionary()

@app.post("/api/pronunciation")
def add_pronunciation_rule(req: PronunciationRuleRequest):
    """Adds or updates a pronunciation rule in the dictionary."""
    updated = voice_engine.add_pronunciation_rule(req.word, req.replacement, preset=req.preset)
    return {"status": "success", "data": updated}

@app.delete("/api/pronunciation")
def delete_pronunciation_rule(req: PronunciationDeleteRequest):
    """Deletes a pronunciation rule from the dictionary."""
    updated = voice_engine.delete_pronunciation_rule(req.word, preset=req.preset)
    return {"status": "success", "data": updated}

class CreatePresetRequest(BaseModel):
    preset_id: str
    name: str
    description: Optional[str] = ""
    type: Optional[str] = "video"

class CharacterAddRequest(BaseModel):
    character: str
    voice_id: str
    voice_name: str
    settings: Dict[str, Any]

@app.get("/api/presets")
def get_all_presets():
    """Returns all client presets and characters."""
    return {"presets": voice_engine.get_presets()}

@app.post("/api/presets")
def create_preset(req: CreatePresetRequest):
    """Creates a new client preset."""
    p = voice_engine.add_or_update_preset(req.preset_id, req.name, req.description, req.type)
    return {"status": "success", "preset_id": req.preset_id, "preset": p}

@app.post("/api/presets/{preset_id}/characters")
def add_preset_character(preset_id: str, req: CharacterAddRequest):
    """Adds or updates a character under a specific client preset."""
    c = voice_engine.add_or_update_character(preset_id, req.character, req.voice_id, req.voice_name, req.settings)
    return {"status": "success", "preset_id": preset_id, "character": req.character, "data": c}

@app.delete("/api/presets/{preset_id}/characters/{character}")
def delete_preset_character(preset_id: str, character: str):
    """Deletes a character from a client preset."""
    ok = voice_engine.delete_character(preset_id, character)
    if not ok:
        raise HTTPException(status_code=404, detail="Character or preset not found")
    return {"status": "success", "deleted": character}

@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str):
    """Deletes an entire client preset."""
    ok = voice_engine.delete_preset(preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"status": "success", "deleted_preset": preset_id}

@app.get("/api/voices")
def get_voices():
    """Returns current character voice configurations."""
    return voice_engine.config

class AssignVoiceRequest(BaseModel):
    preset_id: str
    character: str
    voice_id: str
    voice_name: str
    settings: Optional[Dict[str, Any]] = None

@app.get("/api/elevenlabs-voices")
def list_elevenlabs_voices(refresh: bool = False):
    """Fetches voices from ElevenLabs account or defaults, supporting on-demand refresh."""
    voices = voice_engine.fetch_account_voices(force_refresh=refresh)
    return {"status": "success", "voices": voices, "total": len(voices)}

@app.get("/api/community-voices")
def list_community_voices(
    search: Optional[str] = None,
    accent: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
    page: int = 0,
    page_size: int = 30
):
    """Searches ElevenLabs 19,000+ shared community voices with pagination."""
    return voice_engine.fetch_community_voices(
        search=search,
        accent=accent,
        gender=gender,
        category=category,
        page=page,
        page_size=page_size
    )

@app.post("/api/assign-voice-to-character")
def assign_voice_to_character(req: AssignVoiceRequest):
    """Assigns an ElevenLabs voice to a character in a specific client preset, preserving tuned audition settings."""
    preset = voice_engine.get_preset(req.preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")
    
    # Priority: 1. Explicit tuned settings passed from client (audition workbench)
    #           2. Existing character settings if already configured
    #           3. Default high-fidelity settings
    if req.settings and isinstance(req.settings, dict):
        try:
            speed_val = max(0.5, min(1.5, float(req.settings.get("speed", 0.90))))
            stab_val = max(0.0, min(1.0, float(req.settings.get("stability", 0.50))))
            sim_val = max(0.0, min(1.0, float(req.settings.get("similarity_boost", 0.75))))
            style_val = max(0.0, min(1.0, float(req.settings.get("style", 0.20))))
            boost_val = bool(req.settings.get("use_speaker_boost", True))
            target_settings = {
                "speed": round(speed_val, 2),
                "stability": round(stab_val, 2),
                "similarity_boost": round(sim_val, 2),
                "style": round(style_val, 2),
                "use_speaker_boost": boost_val
            }
        except (ValueError, TypeError):
            target_settings = {
                "speed": 0.90, "stability": 0.50, "similarity_boost": 0.75, "style": 0.20, "use_speaker_boost": True
            }
    else:
        existing_char = preset.get("characters", {}).get(req.character, {})
        target_settings = existing_char.get("settings", {
            "speed": 0.90,
            "stability": 0.50,
            "similarity_boost": 0.75,
            "style": 0.20,
            "use_speaker_boost": True
        })
    
    updated = voice_engine.add_or_update_character(
        preset_id=req.preset_id,
        character=req.character,
        voice_id=req.voice_id,
        voice_name=req.voice_name,
        settings=target_settings
    )
    return {"status": "success", "preset_id": req.preset_id, "character": req.character, "settings": target_settings, "data": updated}

@app.get("/api/audio/{folder}/{filename}")
def stream_audio(folder: str, filename: str):
    """Streams an audio file for in-browser playback."""
    safe_folders = {
        "audio": OUTPUT_DIR / "audio",
        "cache": CACHE_DIR,
        "temp": Path("temp"),
        "auditions": Path("temp/auditions")
    }
    if folder not in safe_folders:
        raise HTTPException(status_code=400, detail="Invalid audio folder.")
    target = safe_folders[folder] / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return FileResponse(target, media_type="audio/mpeg")

@app.post("/api/audition-line")
def api_audition_line(req: AuditionRequest):
    """Auditions a single voiceover line with live settings."""
    temp_dir = Path("temp/auditions")
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Calculate unique hash including voice_id, model_id, text, and voice settings
    m_tag = req.model_id or "default"
    s_hash = hashlib.md5((json.dumps(req.settings or {}, sort_keys=True) + m_tag).encode()).hexdigest()[:8]
    t_hash = hashlib.md5(req.text.encode()).hexdigest()[:8]
    v_id = (req.voice_id or req.speaker).replace("/", "_")
    out_file = temp_dir / f"audition_{v_id}_{m_tag}_{t_hash}_{s_hash}.mp3"

    # Friendly check if trying live synthesis without API key
    active_key = voice_engine.get_api_key(force_reload=True)
    if not req.dry_run and not active_key:
        return {
            "status": "dry_run",
            "character": req.speaker,
            "voice_name": req.voice_name or req.speaker,
            "text": req.text,
            "model_id": req.model_id,
            "estimated_credits": len(req.text),
            "note": "API key not set yet in .env — simulated dry run preview."
        }

    try:
        res = voice_engine.generate_tts(
            text=req.text,
            character=req.speaker,
            output_path=str(out_file),
            preset=req.preset,
            voice_id_override=req.voice_id,
            voice_name_override=req.voice_name,
            settings_override=req.settings,
            model_id_override=req.model_id,
            dry_run=req.dry_run,
            overwrite=req.overwrite
        )
        if req.dry_run:
            return {
                "status": "dry_run",
                "character": req.speaker,
                "voice_name": req.voice_name or req.speaker,
                "text": req.text,
                "model_id": req.model_id,
                "estimated_credits": len(req.text)
            }
        else:
            return {
                "status": "success",
                "audio_url": f"/api/audio/auditions/{out_file.name}",
                "character": req.speaker,
                "voice_name": req.voice_name,
                "model_id": req.model_id,
                "settings_applied": req.settings
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/preview-character-voice")
def api_preview_character_voice(req: PreviewVoiceRequest):
    """Previews / tests audio pronunciation for a word or phrase with a character voice."""
    preset_id = req.preset_id or req.preset or "tata"
    character = req.character or req.speaker or "Dex"
    spoken_text = (req.dialogue or req.text or "").strip()
    if not spoken_text:
        raise HTTPException(status_code=400, detail="Text or dialogue is required.")

    temp_dir = Path("temp/auditions")
    temp_dir.mkdir(parents=True, exist_ok=True)

    m_tag = req.model_id or "eleven_v4"
    t_hash = hashlib.md5(spoken_text.encode()).hexdigest()[:8]
    c_hash = hashlib.md5(f"{preset_id}_{character}_{m_tag}".encode()).hexdigest()[:6]
    out_file = temp_dir / f"pronounce_{character}_{c_hash}_{t_hash}.mp3"

    active_key = voice_engine.get_api_key(force_reload=True)
    if not active_key:
        return {
            "status": "dry_run",
            "character": character,
            "text": spoken_text,
            "note": "API key not set in .env — simulated dry run preview."
        }

    try:
        res = voice_engine.generate_tts(
            text=spoken_text,
            character=character,
            output_path=str(out_file),
            preset=preset_id,
            voice_id_override=req.voice_id,
            model_id_override=req.model_id,
            dry_run=False,
            overwrite=True
        )
        return {
            "status": "success",
            "audio_url": f"/api/audio/auditions/{out_file.name}",
            "character": character,
            "preset": preset_id,
            "text": spoken_text
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/latest-storyboard")
def get_latest_storyboard():
    """Returns the most recently parsed storyboard manifest if available."""
    manifest_files = list(OUTPUT_DIR.glob("*_manifest.json"))
    if manifest_files:
        latest = max(manifest_files, key=os.path.getmtime)
        try:
            with open(latest, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data.get("items", [])
                base_stem = latest.name.replace("_manifest.json", "")
                is_docx = any(it.get("source") == "word_document" for it in items[:5])
                ext = ".docx" if is_docx else ".pptx"
                return {
                    "has_storyboard": True,
                    "filename": f"{base_stem}{ext}",
                    "file_type": "docx" if is_docx else "pptx",
                    "total_items": len(items),
                    "items": items
                }
        except Exception:
            pass
    return {"has_storyboard": False, "filename": None, "file_type": None, "total_items": 0, "items": []}

@app.post("/api/clear-latest-storyboard")
def clear_latest_storyboard():
    """Clears cached storyboard manifests so the application can reset to a clean state."""
    count = 0
    for mf in OUTPUT_DIR.glob("*_manifest.json"):
        try:
            mf.unlink()
            count += 1
        except Exception:
            pass
    for mf_csv in OUTPUT_DIR.glob("*_manifest.csv"):
        try:
            mf_csv.unlink()
        except Exception:
            pass
    return {"status": "success", "cleared_count": count}

@app.get("/api/download-storyboard-template")
def download_storyboard_template():
    """Generates and serves the official Word storyboard (.docx) template."""
    tmpl_dir = Path("temp/templates")
    tmpl_dir.mkdir(parents=True, exist_ok=True)
    tmpl_file = tmpl_dir / "Keenmind_Storyboard_Template.docx"
    generate_sample_docx_template(str(tmpl_file))
    return FileResponse(
        str(tmpl_file),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="Keenmind_Storyboard_Template.docx"
    )

@app.post("/api/parse-storyboard")
@app.post("/api/parse-pptx")
async def api_parse_storyboard(
    file: Optional[UploadFile] = File(None),
    pptx_path: Optional[str] = Form(None),
    file_path: Optional[str] = Form(None),
    preset: str = Form("auto")
):
    """Parses a storyboard (.pptx or .docx) via uploaded file or local path with client preset."""
    target_path = None
    if file:
        dest = UPLOAD_DIR / file.filename
        with open(dest, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        target_path = str(dest)
    elif file_path or pptx_path:
        target_path = file_path or pptx_path
    else:
        raise HTTPException(status_code=400, detail="Provide either 'file' or 'file_path'.")

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"File not found: {target_path}")

    try:
        lower_path = target_path.lower()
        if lower_path.endswith(('.docx', '.doc')):
            records = parse_docx_storyboard(target_path, preset=preset)
            doc_type = "docx"
        else:
            records = parse_pptx_storyboard(target_path, preset=preset)
            doc_type = "pptx"

        base_name = Path(target_path).stem
        out_json = str(OUTPUT_DIR / f"{base_name}_manifest.json")
        out_csv = str(OUTPUT_DIR / f"{base_name}_manifest.csv")
        export_manifest(records, out_json, out_csv)
        
        return {
            "status": "success",
            "preset": preset,
            "doc_type": doc_type,
            "filename": Path(target_path).name,
            "total_items": len(records),
            "manifest_json": out_json,
            "manifest_csv": out_csv,
            "items": records
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate-audio")
def api_generate_audio(req: GenerateAudioRequest):
    """Generates audio for parsed and user-edited manifest items."""
    items = []
    if req.items:
        items = req.items
    elif req.manifest_path and os.path.exists(req.manifest_path):
        with open(req.manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data.get("items", [])
    else:
        raise HTTPException(status_code=400, detail="Provide either 'items' or a valid 'manifest_path'.")

    limit = req.limit or len(items)
    selected = [it for it in items if it.get("approved", True)][:limit]
    audio_out_dir = OUTPUT_DIR / "audio"
    audio_out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for it in selected:
        item_id = it["id"]
        speaker = it.get("speaker", "Narrator")
        text = it.get("text", "")
        voice_id = it.get("voice_id")
        voice_name = it.get("voice_name")
        settings = it.get("settings")
        dest = str(audio_out_dir / f"{item_id}.mp3")

        try:
            res = voice_engine.generate_tts(
                text=text,
                character=speaker,
                output_path=dest,
                preset=req.preset,
                voice_id_override=voice_id,
                voice_name_override=voice_name,
                settings_override=settings,
                model_id_override=req.model_id,
                dry_run=req.dry_run,
                overwrite=req.overwrite
            )
            audio_url = f"/api/audio/audio/{item_id}.mp3" if not req.dry_run else None
            results.append({"id": item_id, "speaker": speaker, "audio_url": audio_url, **res})
        except Exception as e:
            results.append({"id": item_id, "speaker": speaker, "status": "error", "error": str(e)})

    # Package into a ZIP archive if not a dry-run and files were generated
    zip_filename = None
    download_url = None
    if not req.dry_run and results:
        import time
        import zipfile
        active_preset = req.preset or voice_engine.config.get("active_preset", "storyboard")
        ts = int(time.time())
        zip_filename = f"{active_preset}_storyboard_audio_{ts}.zip"
        zip_path = OUTPUT_DIR / zip_filename
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for r in results:
                    p = r.get("path")
                    if p and os.path.exists(p):
                        zf.write(p, arcname=Path(p).name)
                # Include manifest CSV inside ZIP
                manifest_lines = ["id,speaker,voice_name,status,filename"]
                for r in results:
                    fname = Path(r.get("path", "")).name if r.get("path") else ""
                    manifest_lines.append(f"{r.get('id', '')},{r.get('speaker', '')},{r.get('voice_name', '')},{r.get('status', '')},{fname}")
                zf.writestr("manifest.csv", "\n".join(manifest_lines))
            download_url = f"/api/download-export/{zip_filename}"
        except Exception as e:
            print(f"[API ERROR] Failed to create audio ZIP archive: {e}")

    return {
        "status": "completed",
        "processed_count": len(results),
        "dry_run": req.dry_run,
        "zip_filename": zip_filename,
        "download_url": download_url,
        "results": results
    }

@app.post("/api/match-slide")
async def api_match_slide(
    video_file: Optional[UploadFile] = File(None),
    video_name: Optional[str] = Form(None),
    storyboard_items: Optional[str] = Form(None)
):
    """
    100% Free AI slide matcher:
    Matches a video clip with the exact PPT slide using filename detection or local Whisper STT.
    Returns matched slide, character, verbatim script, and confidence score.
    """
    items = []
    if storyboard_items:
        try:
            items = json.loads(storyboard_items)
        except Exception:
            items = []

    # If no items passed, fallback to latest parsed manifest in output/
    if not items:
        manifest_files = list(OUTPUT_DIR.glob("*_manifest.json"))
        if manifest_files:
            latest = max(manifest_files, key=os.path.getmtime)
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    items = json.load(f).get("items", [])
            except Exception:
                pass

    target_path = None
    if video_file:
        dest = UPLOAD_DIR / video_file.filename
        with open(dest, "wb") as buffer:
            shutil.copyfileobj(video_file.file, buffer)
        target_path = str(dest)
    elif video_name and (UPLOAD_DIR / video_name).exists():
        target_path = str(UPLOAD_DIR / video_name)
    elif video_name and Path(video_name).exists():
        target_path = str(video_name)

    # Fast filename matching if video file not yet on disk
    if not target_path and video_name:
        import re
        m = re.search(r'(?:slide|s|_)([0-9]{1,3})', video_name.lower())
        if m:
            s_num = int(m.group(1))
            for it in items:
                if it.get("slide") == s_num:
                    return {
                        "status": "success",
                        "matched": True,
                        "slide": it.get("slide"),
                        "speaker": it.get("speaker"),
                        "script_text": it.get("text"),
                        "confidence": 99.0,
                        "method": "filename_detection"
                    }

    if not target_path:
        raise HTTPException(status_code=400, detail="Provide either 'video_file' or 'video_name'.")

    try:
        res = video_processor.match_video_to_storyboard(target_path, items)
        return {"status": "success", **res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def resolve_target_video(video_file: Optional[UploadFile] = None, video_path: Optional[str] = None, video_name: Optional[str] = None) -> str:
    target_path = None
    if video_file and video_file.filename:
        dest = UPLOAD_DIR / video_file.filename
        try:
            spool = video_file.file.tell() if hasattr(video_file.file, "tell") else 0
            video_file.file.seek(0, os.SEEK_END)
            up_size = video_file.file.tell()
            video_file.file.seek(spool)
        except Exception:
            up_size = 1000
        if up_size > 100 or not dest.exists():
            with open(dest, "wb") as buffer:
                shutil.copyfileobj(video_file.file, buffer)
        target_path = str(dest)
    elif video_path and os.path.exists(video_path):
        target_path = video_path
    elif video_path and (UPLOAD_DIR / video_path).exists():
        target_path = str(UPLOAD_DIR / video_path)
    elif video_path and (UPLOAD_DIR / Path(video_path).name).exists():
        target_path = str(UPLOAD_DIR / Path(video_path).name)
    elif video_name and (UPLOAD_DIR / video_name).exists():
        target_path = str(UPLOAD_DIR / video_name)
    elif video_name and (UPLOAD_DIR / Path(video_name).name).exists():
        target_path = str(UPLOAD_DIR / Path(video_name).name)
    elif video_name and Path(video_name).exists():
        target_path = str(video_name)

    if not target_path or not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail=f"Video file not found: {target_path or video_name or video_path}")
    return target_path

@app.post("/api/analyze-video-timeline")
async def api_analyze_video_timeline(
    video_file: Optional[UploadFile] = File(None),
    video_path: Optional[str] = Form(None),
    video_name: Optional[str] = Form(None),
    storyboard_items: Optional[str] = Form(None),
    max_duration: Optional[float] = Form(None)
):
    """Analyzes a video clip, detects dialogue timestamps, and matches with PPT storyboard lines."""
    items = []
    if storyboard_items:
        try:
            items = json.loads(storyboard_items)
        except Exception:
            items = []

    if not items:
        manifest_files = list(OUTPUT_DIR.glob("*_manifest.json"))
        if manifest_files:
            latest = max(manifest_files, key=os.path.getmtime)
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    items = json.load(f).get("items", [])
            except Exception:
                pass

    target_path = resolve_target_video(video_file, video_path, video_name)

    try:
        res = video_processor.analyze_video_timeline(target_path, items, max_duration=max_duration)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/replace-video-audio")
async def api_replace_video_audio(
    video_file: Optional[UploadFile] = File(None),
    video_path: Optional[str] = Form(None),
    character: Optional[str] = Form("Dex"),
    preset: Optional[str] = Form(None),
    mode: Optional[str] = Form("strict_tts"),
    model_id: Optional[str] = Form(None),
    segments: Optional[str] = Form(None),
    settings: Optional[str] = Form(None),
    voice_id_override: Optional[str] = Form(None),
    dry_run: bool = Form(True),
    overwrite: bool = Form(False)
):
    """Replaces video audio using ElevenLabs (Multi-speaker Timeline or Single Character)."""
    target_path = resolve_target_video(video_file, video_path)

    settings_dict = None
    if settings:
        try:
            settings_dict = json.loads(settings)
        except Exception:
            pass

    # Multi-speaker Timeline Replacement
    if segments:
        try:
            seg_list = json.loads(segments)
        except Exception:
            seg_list = []
        if seg_list:
            v_stem = Path(target_path).stem
            unique_speakers = list(set(s.get("speaker") for s in seg_list if s.get("speaker")))
            spk_tag = unique_speakers[0] if len(unique_speakers) == 1 else "multispeaker"
            out_video = str(OUTPUT_DIR / f"{v_stem}_{spk_tag}_{mode}.mp4")
            try:
                res = video_processor.replace_video_multi_speaker(
                    video_path=target_path,
                    segments=seg_list,
                    mode=mode,
                    voice_engine=voice_engine,
                    output_video_path=out_video,
                    preset=preset,
                    model_id=model_id,
                    settings_override=settings_dict,
                    dry_run=dry_run,
                    overwrite=overwrite
                )
                return {
                    "status": "success",
                    "dry_run": dry_run,
                    "output_video": out_video,
                    "filename": Path(out_video).name if not dry_run else None,
                    "video_url": f"/api/video/{Path(out_video).name}" if not dry_run else None,
                    "download_url": f"/api/download-video/{Path(out_video).name}" if not dry_run else None,
                    "details": res
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

    # Single-character Fallback
    v_stem = Path(target_path).stem
    out_video = str(OUTPUT_DIR / f"{v_stem}_{character}_voice_changed.mp4")

    try:
        res = video_processor.replace_video_voice(
            video_path=target_path,
            target_character=character,
            voice_engine=voice_engine,
            output_video_path=out_video,
            preset=preset,
            voice_id_override=voice_id_override,
            settings_override=settings_dict,
            dry_run=dry_run,
            overwrite=overwrite
        )
        return {
            "status": "success",
            "dry_run": dry_run,
            "output_video": out_video,
            "filename": Path(out_video).name if not dry_run else None,
            "video_url": f"/api/video/{Path(out_video).name}" if not dry_run else None,
            "download_url": f"/api/download-video/{Path(out_video).name}" if not dry_run else None,
            "details": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/export-timeline-audio")
async def api_export_timeline_audio(
    video_file: Optional[UploadFile] = File(None),
    video_path: Optional[str] = Form(None),
    video_name: Optional[str] = Form(None),
    segments: str = Form(...),
    mode: Optional[str] = Form("strict_tts"),
    character: Optional[str] = Form(None),
    preset: Optional[str] = Form(None),
    model_id: Optional[str] = Form(None),
    settings: Optional[str] = Form(None),
    dry_run: bool = Form(False),
    overwrite: bool = Form(False)
):
    """
    Batch-generates audio clips for a specific character or selected timeline segments,
    and returns a downloadable ZIP archive containing named MP3s and a manifest CSV.
    """
    target_path = resolve_target_video(video_file, video_path, video_name)

    settings_dict = None
    if settings:
        try:
            settings_dict = json.loads(settings)
        except Exception:
            settings_dict = None

    try:
        seg_list = json.loads(segments)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid segments JSON: {str(e)}")

    if not seg_list:
        raise HTTPException(status_code=400, detail="No segments provided for export.")

    v_stem = Path(target_path).stem
    char_tag = character if character and character.lower() != "all" else "batch"
    out_zip = OUTPUT_DIR / f"{v_stem}_{char_tag}_{mode}_audio.zip"

    try:
        res = video_processor.export_timeline_audio_batch(
            video_path=target_path,
            segments=seg_list,
            mode=mode,
            voice_engine=voice_engine,
            output_zip_path=str(out_zip),
            character_filter=character,
            preset=preset,
            model_id=model_id,
            settings_override=settings_dict,
            dry_run=dry_run,
            overwrite=overwrite
        )
        return {
            "status": "success",
            "dry_run": dry_run,
            "zip_filename": out_zip.name,
            "download_url": f"/api/download-export/{out_zip.name}" if not dry_run else None,
            "details": res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download-export/{filename}")
def download_export_archive(filename: str):
    """Downloads an exported audio ZIP archive."""
    target = OUTPUT_DIR / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="Export ZIP not found.")
    return FileResponse(target, media_type="application/zip", filename=filename)

@app.get("/api/download-video/{filename}")
def download_video(filename: str):
    """Forces direct browser file download for a processed video with attachment disposition."""
    target = OUTPUT_DIR / filename
    if not target.exists():
        target = UPLOAD_DIR / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="Video file not found.")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/api/video/{filename}")
@app.get("/api/video-stream/{filename}")
def stream_video(filename: str, download: Optional[int] = Query(None)):
    """Streams a processed video file for in-browser playback, or forces download if ?download=1."""
    target = OUTPUT_DIR / filename
    if not target.exists():
        target = UPLOAD_DIR / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="Video file not found.")
    if download:
        return FileResponse(
            target,
            media_type="application/octet-stream",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    return FileResponse(target, media_type="video/mp4")

@app.get("/api/export-debug-log")
def export_debug_log():
    """Generates an extensive diagnostic report for troubleshooting and support."""
    import platform
    import sys
    import datetime

    key = voice_engine.get_api_key(force_reload=True)
    masked_key = f"{key[:6]}...{key[-4:]}" if len(key) > 10 else ("Configured" if key else "Missing")

    # FFmpeg details
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    ffmpeg_ver = "Unknown"
    if ffmpeg_bin:
        try:
            ver_res = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True)
            ffmpeg_ver = ver_res.stdout.splitlines()[0] if ver_res.stdout else "Available"
        except Exception:
            ffmpeg_ver = "Present (error reading version)"

    # Config & Preset stats
    presets = voice_engine.get_presets()
    total_chars = sum(len(p.get("characters", {})) for p in presets.values())

    # Cache stats
    cache_files = list(CACHE_DIR.glob("*"))
    cache_size_bytes = sum(f.stat().st_size for f in cache_files if f.is_file())

    # Temp and uploads stats
    upload_files = list(UPLOAD_DIR.glob("*"))
    output_files = list(OUTPUT_DIR.glob("*"))

    debug_data = {
        "timestamp": datetime.datetime.now().isoformat(),
        "platform": {
            "os": platform.platform(),
            "python_version": sys.version,
            "architecture": list(platform.architecture())
        },
        "elevenlabs": {
            "key_status": masked_key,
            "default_model": voice_engine.config.get("default_model", "eleven_v4"),
            "default_sts_model": voice_engine.config.get("default_sts_model", "eleven_multilingual_sts_v2"),
            "cached_audio_files_count": len(cache_files),
            "cached_audio_size_mb": round(cache_size_bytes / (1024 * 1024), 2)
        },
        "ffmpeg": {
            "ffmpeg_available": ffmpeg_bin is not None,
            "ffmpeg_path": ffmpeg_bin,
            "ffmpeg_version": ffmpeg_ver,
            "ffprobe_available": ffprobe_bin is not None,
            "ffprobe_path": ffprobe_bin
        },
        "workspace": {
            "active_presets_count": len(presets),
            "total_configured_characters": total_chars,
            "preset_ids": list(presets.keys()),
            "recent_uploads_count": len(upload_files),
            "recent_outputs_count": len(output_files)
        }
    }
    return JSONResponse(
        content=debug_data,
        headers={"Content-Disposition": f"attachment; filename=keenmind_debug_log_{int(datetime.datetime.now().timestamp())}.json"}
    )

@app.post("/api/auto-tag-expressions")
def api_auto_tag_expressions(req: AutoTagRequest):
    """
    Intelligently tags dialogue text or timeline segments with ElevenLabs v4 emotional expression tags:
    [happy], [thoughtful], [excited], [serious], [whispering], [short pause].
    """
    def tag_text(line: str) -> str:
        if not line or not line.strip():
            return line
        clean = line.strip()
        # Do not re-tag if already has an expression tag at the start
        if re.match(r'^\[(happy|thoughtful|excited|serious|whispering|shouting|calm|sad|short pause)\]', clean, re.IGNORECASE):
            return clean

        lower = clean.lower()
        tag = None

        # Excited / Enthusiastic
        if any(w in lower for w in ["!", "awesome", "amazing", "great", "fantastic", "excited", "wow", "hurray", "congratulations", "welcome to"]):
            tag = "[excited]"
        # Thoughtful / Pondering / Inquisitive
        elif any(w in lower for w in ["?", "wonder", "why", "what if", "consider", "think about", "imagine", "could it be", "perhaps"]):
            tag = "[thoughtful]"
        # Serious / Caution / Directive
        elif any(w in lower for w in ["warning", "caution", "crucial", "important", "must", "note that", "do not", "critical", "mandatory", "remember"]):
            tag = "[serious]"
        # Whispering / Confidential
        elif any(w in lower for w in ["whisper", "secret", "between us", "psst", "quietly", "listen closely"]):
            tag = "[whispering]"
        # Happy / Warm
        elif any(w in lower for w in ["glad", "happy", "pleasure", "smile", "delighted", "love", "enjoy", "hello"]):
            tag = "[happy]"
        else:
            # Default mild expression or thoughtful for explanations
            if len(clean) > 80:
                tag = "[thoughtful]"
            else:
                tag = "[happy]"

        return f"{tag} {clean}" if tag else clean

    if req.segments:
        enriched_segments = []
        for s in req.segments:
            s_copy = dict(s)
            script = s_copy.get("script_text") or s_copy.get("heard_text") or ""
            s_copy["script_text"] = tag_text(script)
            enriched_segments.append(s_copy)
        return {"status": "success", "segments": enriched_segments}
    elif req.text:
        return {"status": "success", "tagged_text": tag_text(req.text)}
    else:
        raise HTTPException(status_code=400, detail="Provide either 'text' or 'segments'.")

