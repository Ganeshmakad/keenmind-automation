# Keenmind Audio & Video Automation Engine

A high-performance pipeline for automating voiceover synthesis and video voice replacement for eLearning modules:
1. **Linear Video Storyboards (Module 05 / Flow / Veo 3.1):** Automatically extracts dialogue from speaker notes and on-screen comic bubbles (`Same as OST.`), runs ElevenLabs Voice Changer (Speech-to-Speech), and remuxes consistent character voices into videos without manual Camtasia syncing.
2. **Interactive / Gamified Storyboards (Highway Hero / Case 3019):** Automatically extracts questions, feedback, and customer/technician dialogue into discrete audio files with full manifests.
3. **Turnkey Automation:** Complete **Web Studio UI** and **REST API** fully containerized with **Docker** and **FFmpeg**.

---

## Architecture Overview

```
                          ┌──────────────────────────┐
                          │   Storyboard (.pptx)     │
                          │   or Video Clip (.mp4)   │
                          └─────────────┬────────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
        ┌───────────────────────┐               ┌───────────────────────┐
        │  Smart PPTX Parser    │               │ Video Voice Replacer  │
        │ • Speaker notes       │               │ • FFmpeg audio extract│
        │ • Comic dialog bubbles│               │ • ElevenLabs STS      │
        │ • Interactive quizzes │               │ • Zero-loss remuxing  │
        └───────────┬───────────┘               └───────────┬───────────┘
                    │                                       │
                    ▼                                       ▼
        ┌───────────────────────┐               ┌───────────────────────┐
        │ Structured Manifest   │               │ Finished Video Clip   │
        │ (.json & .csv)        │               │ (.mp4)                │
        └───────────┬───────────┘               └───────────────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ ElevenLabs TTS Batch  │
        │ • MD5 Hash Cache      │
        │ • Zero-credit dry-run │
        └───────────────────────┘
```

---

## 1. Quick Setup

### Prerequisites
- Python 3.10+
- FFmpeg installed and in PATH
- (Optional) Docker & Docker Compose for containerized deployment

### Installation
```bash
pip install -r requirements.txt
cp .env.example .env
```
Edit `.env` and add your ElevenLabs API key:
```env
ELEVENLABS_API_KEY=your_key_here
```

---

## 2. Character Voice Mapping (`config/voices.json`)

Configure character voice IDs and generation settings in `config/voices.json`:
- **Analytica** &rarr; `Anika` (`jUjRbhZWoMK4aDciW36V`)
- **Dex** &rarr; `Sanil` (`Fpi5XUKwuD84kRo0jLtj`)
- **Narrator** &rarr; `Viraj` (`bajNon13EdhNMndG3z05`)
- **Customer / Technician / Service Advisor** &rarr; Custom mappings

---

## 3. CLI Usage

### Parse Storyboard PPTX
Extract all voiceover items, resolve comic speech bubbles, and generate manifests:
```bash
python cli.py parse --pptx "VSB_Module 05_V03.pptx"
# Or interactive module:
python cli.py parse --pptx "VSB_01 compressed.pptx"
```

### Dry-Run Validation (Zero Credits)
```bash
python cli.py generate-audio --manifest output/VSB_Module 05_V03_manifest.json --limit 5 --dry-run
```

### Batch Generate Audio (TTS)
```bash
python cli.py generate-audio --manifest output/VSB_Module 05_V03_manifest.json --limit 10
```

### Replace Video Voice (Zero Manual Camtasia Syncing)
Extracts audio from video, converts voice via ElevenLabs Speech-to-Speech, and remuxes back into the video:
```bash
# Dry run
python cli.py replace-video-audio --video "input_clip.mp4" --character "Analytica" --output "output/analytica_clip.mp4" --dry-run

# Production run
python cli.py replace-video-audio --video "input_clip.mp4" --character "Analytica" --output "output/analytica_clip.mp4"
```

---

## 4. Web Studio & REST API

Start the FastAPI microservice and Web Studio:
```bash
uvicorn api.server:app --host 0.0.0.0 --port 9876
```
- **Web Studio UI:** `http://localhost:9876`
- **Interactive API Docs (Swagger):** `http://localhost:9876/docs`

### Key REST API Endpoints

#### 1. Parse Storyboard
- **Method:** `POST`
- **URL:** `http://localhost:9876/api/parse-pptx`
- **Body:** `Multipart Form-Data` with `file` (binary PPTX) or `pptx_path`
- **Response:** JSON manifest containing all slides, characters, and extracted dialogues.

#### 2. Batch Audio Generation (TTS)
- **Method:** `POST`
- **URL:** `http://localhost:9876/api/generate-audio`
- **Body:** `JSON`
```json
{
  "manifest_path": "output/VSB_Module 05_V03_manifest.json",
  "limit": 10,
  "dry_run": false
}
```

#### 3. Replace Video Voice (STS Voice Changer)
- **Method:** `POST`
- **URL:** `http://localhost:9876/api/replace-video-audio`
- **Body:** `Multipart Form-Data`
  - `video`: Uploaded video file
  - `character`: Target voice preset (e.g. `Analytica`)
  - `target_text`: Exact dialogue line from the storyboard
  - `dry_run`: `false`

---

## 5. Docker Deployment

Deploy the entire engine inside Docker:
```bash
docker compose up --build -d
```
The Keenmind Automation Studio and API will be immediately available at `http://localhost:9876` with pre-configured FFmpeg and volume persistence for `output/`, `cache/`, and `config/`.

---

## 6. Windows 1-Click Batch Execution

For rapid Windows onboarding without manual command-line typing:

| Batch File | Function |
|---|---|
| [`setup.bat`](file:///e:/Keenmind%20Automation/setup.bat) | Automated wizard: Verifies Python 3.10+, checks/installs FFmpeg, creates `venv`, installs all packages from `requirements.txt`, and creates `.env`. |
| [`start.bat`](file:///e:/Keenmind%20Automation/start.bat) | Activates virtual environment, detects local Wi-Fi IP address, launches FastAPI server, and opens default browser to `http://localhost:9876`. |
| [`stop.bat`](file:///e:/Keenmind%20Automation/stop.bat) | Cleanly terminates any running server instance on port 9876. |
| [`package_release.bat`](file:///e:/Keenmind%20Automation/package_release.bat) | Interactive distribution packager that creates clean, lightweight ZIP files ready to share. |

---

## 7. Multi-Device Sharing Methods

### Option A: Local Wi-Fi / LAN Sharing (Zero-Install on Other Devices)
FastAPI binds to `0.0.0.0:9876`. Run `start.bat` on one host PC. Any other laptop, desktop, tablet, or mobile phone on the same Wi-Fi network can directly open:
```
http://<HOST_IP>:9876
```
- **No Python or FFmpeg needed on client devices.**
- **Shared ElevenLabs Cache:** Saves API credits across team members.

### Option B: Standalone Portable ZIP (Complete Local Independence)
Run [`package_release.bat`](file:///e:/Keenmind%20Automation/package_release.bat), send the resulting 56 KB ZIP to colleagues. They unzip, run `setup.bat` once, and double-click `start.bat`.

