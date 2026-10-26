"""
package_team_release.py
Keenmind Automation Studio - Official Team Distribution Packager

Creates a clean, turnkey, production distribution ZIP file for teammates:
- Preserves live .env configuration (working ElevenLabs key & models)
- Preserves full audio cache (570+ files, ~40MB) so 0 API credits are consumed
- Preserves learned Pronunciation Diary (tatacomp, Dex, Analytica, ECU, etc.)
- Includes 1-click turnkey Windows scripts (setup.bat with GPU CUDA detection, start.bat, stop.bat)
- Excludes heavy test videos (*.mp4), test storyboards, legacy archives, and junk directories
"""

import os
import sys
import time
import json
import zipfile
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

OUTPUT_ZIP = ROOT_DIR / "Keenmind_Studio_Team_v2.zip"

EXPLICIT_ROOT_FILES = [
    ".env",
    ".env.example",
    "requirements.txt",
    "setup.bat",
    "start.bat",
    "stop.bat",
    "run_server.py",
    "cli.py",
    "test_team_env.py",
    "version.json",
    "TEAM_SETUP_GUIDE.md",
    "README.md"
]

CORE_DIRS = [
    "config",
    "core",
    "api",
    "scripts",
    "tests",
    "cache"
]

SKELETON_DIRS = [
    "output",
    "output/audio",
    "output/videos",
    "temp",
    "temp/uploads",
    "temp/auditions"
]

EXCLUDE_DIRS = {
    "_dev_and_testing",
    "_archive_legacy_packages",
    "woobly audio video",
    "venv",
    ".venv",
    "env",
    ".git",
    ".agents",
    ".claude",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode"
}

EXCLUDE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".log", ".tmp", ".crdownload"
}

def create_team_package():
    print("=" * 70)
    print("   KEENMIND AUTOMATION STUDIO - TEAM DISTRIBUTION PACKAGER")
    print("=" * 70)
    print(f"Target Release: {OUTPUT_ZIP.name}")
    print(f"Source Root:    {ROOT_DIR}")
    print("-" * 70)

    # 0. Sync version.json with current git commit SHA
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            cur_sha = res.stdout.strip()
            v_path = ROOT_DIR / "version.json"
            v_data = {"commit_sha": cur_sha, "version": "2.0.0", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            with open(v_path, "w", encoding="utf-8") as fp:
                json.dump(v_data, fp, indent=4)
            print(f"  * Synced version.json to active Git commit: {cur_sha[:7]}")
    except Exception as e:
        print(f"  * Warning syncing version.json: {e}")

    # 1. Verification of critical assets
    missing = []
    for f in [".env", "config/voices.json", "config/presets.json", "config/pronunciation_dictionary.json", "setup.bat", "start.bat"]:
        if not (ROOT_DIR / f).exists():
            missing.append(f)
    if missing:
        print(f"[ERROR] Required assets missing: {missing}")
        return False

    file_count = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Add root files
        for fname in EXPLICIT_ROOT_FILES:
            fpath = ROOT_DIR / fname
            if fpath.exists():
                zf.write(fpath, arcname=fname)
                file_count += 1
                sz = fpath.stat().st_size
                total_uncompressed_bytes += sz
                print(f"  + [Root File]    {fname} ({sz / 1024:.1f} KB)")

        # Add Core Directories
        for dname in CORE_DIRS:
            dp = ROOT_DIR / dname
            if not dp.exists():
                continue
            for root, dirs, files in os.walk(dp):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix.lower() in EXCLUDE_EXTENSIONS:
                        continue
                    rel_path = file_path.relative_to(ROOT_DIR)
                    zf.write(file_path, arcname=str(rel_path).replace("\\", "/"))
                    file_count += 1
                    sz = file_path.stat().st_size
                    total_uncompressed_bytes += sz

            count_in_dir = sum(1 for _ in dp.rglob("*") if _.is_file())
            dir_mb = sum(_.stat().st_size for _ in dp.rglob("*") if _.is_file()) / (1024 * 1024)
            print(f"  + [Directory]    {dname}/ ({count_in_dir} files, {dir_mb:.2f} MB)")

        # Add Empty Directory Skeletons with .gitkeep
        for sdir in SKELETON_DIRS:
            arc_name = f"{sdir}/.gitkeep"
            zf.writestr(arc_name, "# Keenmind Studio Working Directory\n")
            file_count += 1
            print(f"  + [Directory]    {sdir}/ (Clean Skeleton)")

    zip_size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    uncomp_mb = total_uncompressed_bytes / (1024 * 1024)

    print("-" * 70)
    print(f"[SUCCESS] Team Release Package Created!")
    print(f"  * Total Files Packaged: {file_count}")
    print(f"  * Uncompressed Size:    {uncomp_mb:.2f} MB")
    print(f"  * Compressed ZIP Size:  {zip_size_mb:.2f} MB")
    print(f"  * Output Location:      {OUTPUT_ZIP.resolve()}")
    print("=" * 70)
    print("Ready to share with teammates via Google Drive, OneDrive, or local LAN transfer!\n")
    return True

if __name__ == "__main__":
    create_team_package()
