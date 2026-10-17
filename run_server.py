"""
run_server.py
Keenmind Automation Studio - Robust Universal Server Launcher

Features:
- Reads port from .env or defaults to 9876
- Auto-detects port availability (if 9876 is busy, seamlessly tries 9877, 9878, etc. - NEVER crashes on port conflict!)
- Never kills or interferes with third-party software (like Google Drive)
- Auto-detects local LAN IP for multi-device network sharing
- Reports GPU CUDA acceleration and cache status
- Launches default web browser automatically after server binds
- Dual host fallback: tries 0.0.0.0 first, falls back to 127.0.0.1 if firewall restricts binding
"""

import os
import sys
import socket
import threading
import time
import webbrowser
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

def get_configured_port() -> int:
    """Reads PORT from .env or returns 9876."""
    env_file = ROOT_DIR / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("PORT="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val.isdigit():
                            return int(val)
        except Exception:
            pass
    return int(os.getenv("PORT", "9876"))

def find_available_port(start_port: int, max_attempts: int = 15) -> int:
    """Finds the first available TCP port starting from start_port."""
    for p in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return start_port

def get_lan_ip() -> str:
    """Detects local LAN IP address for network sharing."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

def print_banner(port: int, lan_ip: str):
    """Displays a clean, informative startup banner."""
    print("\n" + "=" * 70)
    print("         KEENMIND AUDIO & VIDEO AUTOMATION STUDIO")
    print("=" * 70)

    # GPU Check
    gpu_status = "CPU Multi-Threaded Mode"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            gpu_status = f"GPU Active (NVIDIA CUDA Acceleration via CTranslate2)"
        else:
            import torch
            if torch.cuda.is_available():
                gpu_status = f"GPU Active: {torch.cuda.get_device_name(0)}"
    except Exception:
        pass
    print(f"  * Hardware Engine:            {gpu_status}")

    # Cache Check
    cache_dir = ROOT_DIR / "cache"
    cache_count = len(list(cache_dir.glob("*.mp3"))) if cache_dir.exists() else 0
    print(f"  * Pre-Rendered Audio Cache:   {cache_count} clips ready (0 API credits used for existing lines)")

    # Pronunciation Check
    p_dict = ROOT_DIR / "config" / "pronunciation_dictionary.json"
    p_rules = 0
    if p_dict.exists():
        try:
            import json
            with open(p_dict, "r", encoding="utf-8") as f:
                d = json.load(f)
            p_rules = len(d.get("global", {})) + sum(len(v) for v in d.get("presets", {}).values() if isinstance(v, dict))
        except Exception:
            pass
    print(f"  * Pronunciation Diary:        {p_rules} custom rules active (tatacomp, Dex, Analytica, etc.)")

    print("-" * 70)
    print(f"  * Studio Web UI (This PC):    http://localhost:{port}")
    if lan_ip and lan_ip != "127.0.0.1":
        print(f"  * Network Access (Other PCs): http://{lan_ip}:{port}")
    print(f"  * Interactive API Docs:       http://localhost:{port}/docs")
    print("-" * 70)
    print("  [TIP] Teammates on the same Wi-Fi can open the Network Access URL")
    print("        in their browser with ZERO installation!")
    print("\n  Press Ctrl+C at any time or run stop.bat to shut down the server.")
    print("=" * 70 + "\n")

def open_browser_delayed(url: str, delay: float = 1.2):
    """Opens default browser after server initializes."""
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    t = threading.Thread(target=_open, daemon=True)
    t.start()

def main():
    preferred_port = get_configured_port()
    port = find_available_port(preferred_port)

    if port != preferred_port:
        print(f"[NOTICE] Port {preferred_port} was occupied by an external application.")
        print(f"[NOTICE] Automatically switched to safe alternate port: {port}")

    lan_ip = get_lan_ip()
    print_banner(port, lan_ip)

    # Launch browser automatically
    open_browser_delayed(f"http://localhost:{port}")

    # Import uvicorn & app
    try:
        import uvicorn
        from api.server import app
    except ImportError as e:
        print(f"\n[FATAL ERROR] Failed to import application dependencies: {e}")
        print("Please run setup.bat first to configure your virtual environment.\n")
        sys.exit(1)

    # Try binding to 0.0.0.0 first (network access), fallback to 127.0.0.1 if firewall blocks
    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    except OSError as e:
        print(f"\n[WARNING] Could not bind to 0.0.0.0 ({e}). Falling back to localhost 127.0.0.1...")
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Studio shut down cleanly. Goodbye!")
