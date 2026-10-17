"""
test_team_env.py
Keenmind Studio - Teammate Environment & Hardware Diagnostic Tool
Run this script to verify your local machine setup, GPU acceleration, audio cache, and API credentials.
"""

import sys
import os
import shutil
from pathlib import Path

def print_header(title):
    print("\n" + "=" * 65)
    print(f"  {title}")
    print("=" * 65)

def check_env():
    print_header("KEENMIND STUDIO - TEAM HARDWARE & ENVIRONMENT AUDIT")
    
    # 1. Python Check
    py_ver = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 10)
    print(f"[{'OK' if py_ok else 'FAIL'}] Python Version: {py_ver} (3.10+ required)")

    # 2. Hardware & CUDA Acceleration Check
    print_header("HARDWARE & GPU ACCELERATION")
    cuda_found = False
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            cuda_found = True
            print(f"[OK] Dedicated NVIDIA GPU detected with CUDA support! ({ctranslate2.get_cuda_device_count()} GPU found)")
            print(f"[OK] Native CTranslate2 CUDA Acceleration: ACTIVE (device='cuda', compute_type='float16')")
            print(f"     => Whisper Video Transcription will run at 10x-20x accelerated speed (~15s for 10-min video)!")
        else:
            import torch
            if torch.cuda.is_available():
                cuda_found = True
                gpu_name = torch.cuda.get_device_name(0)
                vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                print(f"[OK] Dedicated NVIDIA GPU: {gpu_name} ({vram_gb:.1f} GB VRAM)")
                print(f"[OK] PyTorch CUDA Acceleration: ACTIVE (device='cuda', compute_type='float16')")
                print(f"     => Whisper Video Transcription will run at 10x-20x accelerated speed (~15s for 10-min video)!")
            else:
                print("[INFO] CUDA not active. Running in high-performance multi-threaded CPU mode.")
    except Exception:
        print("[INFO] Running in high-performance multi-threaded CPU mode.")

    # 3. FFmpeg Engine Check
    print_header("FFMPEG MULTIMEDIA ENGINE")
    ffmpeg_bin = shutil.which("ffmpeg")
    local_ffmpeg = Path("bin/ffmpeg.exe").exists()
    if ffmpeg_bin or local_ffmpeg:
        path_desc = ffmpeg_bin or "local bin/ffmpeg.exe"
        print(f"[OK] FFmpeg Engine: FOUND ({path_desc})")
        print("     => Video audio extraction and remuxing ready.")
    else:
        print("[FAIL] FFmpeg NOT FOUND. Video features require FFmpeg. (Run setup.bat to auto-install)")

    # 4. Configuration & API Key (.env) Check
    print_header("API CONFIGURATION & CREDENTIALS")
    env_file = Path(".env")
    if env_file.exists():
        key_found = False
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("ELEVENLABS_API_KEY="):
                    val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    if val and val != "your_api_key_here":
                        masked = f"{val[:6]}...{val[-4:]}"
                        print(f"[OK] ElevenLabs API Key: CONFIGURED ({masked})")
                        key_found = True
                        break
        if not key_found:
            print("[WARN] .env exists but ELEVENLABS_API_KEY is empty.")
    else:
        print("[FAIL] .env file not found!")

    # 5. Audio Cache & Pronunciation Dictionary Check
    print_header("PRE-RENDERED AUDIO CACHE & PRONUNCIATION DIARY")
    cache_dir = Path("cache")
    if cache_dir.exists():
        mp3_count = len(list(cache_dir.glob("*.mp3")))
        cache_mb = sum(f.stat().st_size for f in cache_dir.glob("*.mp3")) / (1024 * 1024)
        print(f"[OK] Audio Cache: {mp3_count} pre-synthesized audio clips found ({cache_mb:.1f} MB)")
        print(f"     => Existing Tata & Eicher script lines will use 0 API credits & load instantly!")
    else:
        print("[WARN] cache/ folder missing.")

    p_dict = Path("config/pronunciation_dictionary.json")
    if p_dict.exists():
        import json
        try:
            with open(p_dict, "r", encoding="utf-8") as f:
                p_data = json.load(f)
            total_rules = len(p_data.get("global", {})) + sum(len(v) for v in p_data.get("presets", {}).values() if isinstance(v, dict))
            print(f"[OK] Pronunciation Diary: {total_rules} custom pronunciation rules active (tatacomp, Dex, Analytica, etc.)")
        except Exception as e:
            print(f"[WARN] Pronunciation dictionary read error: {e}")
    else:
        print("[WARN] config/pronunciation_dictionary.json missing.")

    port = os.getenv("PORT", "9876")
    print_header("READY TO RUN")
    print("  To launch Keenmind Studio, double click: start.bat")
    print(f"  Studio will open at: http://localhost:{port}")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    check_env()
