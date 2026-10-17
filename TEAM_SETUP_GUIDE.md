# Keenmind Audio & Video Automation Studio — Team Setup & Quickstart Guide

Welcome to **Keenmind Automation Studio**! This distribution package is pre-configured and ready to run with zero manual API setup.

---

## 🚀 Quick Start (1-2-3 Steps)

### Step 1: Extract the Package
Extract this ZIP folder to any convenient location on your drive (e.g. `C:\Keenmind_Studio` or `D:\Keenmind_Studio`).

### Step 2: One-Time Local Setup
Double-click:
```cmd
setup.bat
```
What this does automatically:
- ✅ Checks your **Python 3.10+** installation (prompts to auto-install if missing).
- ✅ Detects your **NVIDIA GPU** and configures **CUDA acceleration** for 10x–20x faster Whisper video transcription (~15 seconds for a 10-minute video).
- ✅ Verifies **FFmpeg** audio & video engine.
- ✅ Creates an isolated virtual environment (`venv`) and installs all dependencies.
- ✅ Verifies that your pre-configured `.env` credentials and audio cache are active.

> **Time required:** ~60 to 90 seconds (one time only).

---

### Step 3: Launch the Studio
Double-click:
```cmd
start.bat
```
- The backend engine starts up in the console.
- Your default web browser will automatically open to:
  👉 **http://localhost:9876**
- The console will also display your **Local Network IP** (e.g. `http://192.168.1.15:9876`). Other teammates on your local Wi-Fi can open that URL directly in their browser without installing anything!

---

## 🛑 How to Stop / Close the Studio
- Press `Ctrl + C` in the console window, **OR**
- Double-click `stop.bat` to cleanly terminate the background server.

---

## 🔍 Pre-Loaded Team Assets & Features

### 1. Zero Credit Waste (`cache/` Included)
All **570+ pre-synthesized audio segments** for the Tata Motors and Eicher modules are already included in your `cache/` folder (~40 MB). 
- When you load storyboards or test existing video clips, audio plays back **instantaneously** from disk.
- **Zero ElevenLabs API credits** are consumed for existing lines!

### 2. Pronunciation Diary (`config/pronunciation_dictionary.json`)
Client names, character names, and technical terms are pre-mapped for flawless phonetic delivery:
- `tatacomp` $\rightarrow$ Spoken as **Tata Comp**
- `Dex` $\rightarrow$ Spoken as **Decks**
- `Analytica` $\rightarrow$ Spoken as **Ana-lit-ica**
- `VO`, `UI`, `PPT`, `ECU`, `RPM`, `ADAS` $\rightarrow$ Spoken cleanly with natural acronym timing.
- You can add or edit pronunciation rules anytime by clicking **📖 Pronunciation Diary** on the top command deck!

### 3. Hardware Diagnostics
You can verify your GPU, FFmpeg, and API connectivity anytime by running:
```cmd
python test_team_env.py
```
