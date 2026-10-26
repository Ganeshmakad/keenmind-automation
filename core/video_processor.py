"""
video_processor.py - Video & Audio processing using FFmpeg.
Handles audio extraction, automated voice changer replacement, and video remuxing
with zero video quality loss (-c:v copy).
"""

import os
import re
import shutil
import difflib
import time
import uuid
import csv
import zipfile
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from core.voice_engine import VoiceEngine

# Auto-discover local portable FFmpeg in ./bin if present
local_bin = Path(__file__).resolve().parent.parent / "bin"
if local_bin.exists() and str(local_bin) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(local_bin) + os.pathsep + os.environ.get("PATH", "")

class VideoProcessor:
    def __init__(self, temp_dir: str = "temp"):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._whisper_model = None

    def get_whisper_model(self):
        """
        Retrieves the cached Whisper model or initializes a new one.
        Auto-detects NVIDIA CUDA GPU for 10x-20x acceleration,
        falling back cleanly to CPU int8 if CUDA is unavailable.
        """
        if self._whisper_model is not None:
            return self._whisper_model

        from faster_whisper import WhisperModel
        import numpy as np

        model = None
        has_cuda = False
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                has_cuda = True
            else:
                import torch
                if torch.cuda.is_available():
                    has_cuda = True
        except Exception:
            pass

        if has_cuda:
            try:
                candidate = WhisperModel("tiny", device="cuda", compute_type="float16")
                # Smoke-test inference on 0.1s silence to verify cublas/cudnn DLLs actually link
                dummy_audio = np.zeros(1600, dtype=np.float32)
                list(candidate.transcribe(dummy_audio, beam_size=1)[0])
                model = candidate
                print("[VideoProcessor] GPU Acceleration Active via CUDA (float16)")
            except Exception as e:
                print(f"[VideoProcessor] CUDA runtime unavailable or missing DLL ({e}). Falling back to CPU int8 mode.")
                model = None

        if model is None:
            try:
                model = WhisperModel("tiny", device="cpu", compute_type="int8")
                print("[VideoProcessor] Whisper initialized in CPU int8 mode (Fast & Universal).")
            except Exception as e:
                print(f"[VideoProcessor] CPU int8 init failed ({e}), trying standard CPU.")
                model = WhisperModel("tiny", device="cpu", compute_type="default")

        self._whisper_model = model
        return self._whisper_model


    def extract_audio(
        self,
        video_path: str,
        output_wav: str,
        start_seconds: Optional[float] = None,
        duration_seconds: Optional[float] = None
    ) -> str:
        """Extracts 44.1kHz PCM WAV audio from video using FFmpeg."""
        out_file = Path(output_wav)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["ffmpeg", "-y"]
        if start_seconds is not None:
            cmd.extend(["-ss", str(start_seconds)])
        if duration_seconds is not None:
            cmd.extend(["-t", str(duration_seconds)])
        cmd.extend([
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "2",
            str(out_file)
        ])

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {res.stderr}")
        return str(out_file)

    def get_media_duration(self, file_path: str) -> float:
        """Returns the media duration in seconds using ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return float(res.stdout.strip())
        except Exception as e:
            print(f"[VideoProcessor] Failed to probe duration for {file_path}: {e}")
        return 0.0

    def detect_vocal_onset(
        self,
        audio_path: str,
        start: float = 0.0,
        end: Optional[float] = None,
        noise_db: str = "-38dB"
    ) -> float:
        """
        Detects true vocal onset (opening of speaker's mouth) within an audio interval,
        eliminating leading silence/breathing from coarse STT bounds.
        """
        dur = max((end - start) if end is not None else 2.5, 0.1)
        probe_dur = min(dur, 2.5)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(max(start, 0.0)),
            "-t", str(probe_dur),
            "-i", str(audio_path),
            "-af", f"silencedetect=noise={noise_db}:d=0.04",
            "-f", "null", "-"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            silence_end = None
            for line in res.stderr.splitlines():
                if "silence_end:" in line:
                    m = re.search(r"silence_end:\s*([0-9\.]+)", line)
                    if m:
                        silence_end = float(m.group(1))
                        break
            if silence_end is not None and silence_end > 0.08:
                return round(start + silence_end, 2)
        except Exception as e:
            print(f"[VideoProcessor] Onset detect fallback: {e}")
        return round(start, 2)

    def detect_vocal_bounds(
        self,
        audio_path: str,
        start: float,
        end: float,
        noise_db: str = "-38dB"
    ) -> Tuple[float, float]:
        """
        Detects both vocal onset and offset within an audio segment.
        Returns (vocal_start, vocal_end).
        """
        dur = max(end - start, 0.1)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(max(start, 0.0)),
            "-t", str(dur),
            "-i", str(audio_path),
            "-af", f"silencedetect=noise={noise_db}:d=0.03",
            "-f", "null", "-"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            silences = []
            cur_start = None
            for line in res.stderr.splitlines():
                if "silence_start:" in line:
                    m = re.search(r"silence_start:\s*([0-9\.]+)", line)
                    if m:
                        cur_start = float(m.group(1))
                elif "silence_end:" in line:
                    m = re.search(r"silence_end:\s*([0-9\.]+)", line)
                    if m and cur_start is not None:
                        silences.append((cur_start, float(m.group(1))))
                        cur_start = None
            if cur_start is not None:
                silences.append((cur_start, dur))

            sp_start = 0.0
            if silences and silences[0][0] <= 0.05 and silences[0][1] > 0.08:
                sp_start = silences[0][1]

            sp_end = dur
            if silences and silences[-1][1] >= (dur - 0.05):
                sp_end = silences[-1][0]

            return round(start + sp_start, 2), round(start + sp_end, 2)
        except Exception:
            return round(start, 2), round(end, 2)

    def decode_audio_for_whisper(
        self,
        audio_or_video_path: str,
        duration_seconds: Optional[float] = None,
        start_seconds: Optional[float] = None
    ) -> Any:
        """
        Decodes audio directly via FFmpeg into a 16kHz mono float32 numpy array.
        Completely bypasses PyAV / av.open, preventing 'metadata_errors' and library version mismatches.
        """
        import numpy as np
        cmd = ["ffmpeg", "-y"]
        if start_seconds is not None:
            cmd.extend(["-ss", str(start_seconds)])
        if duration_seconds is not None:
            cmd.extend(["-t", str(duration_seconds)])
        cmd.extend([
            "-i", str(audio_or_video_path),
            "-f", "s16le",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-"
        ])
        try:
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0 and res.stdout:
                return np.frombuffer(res.stdout, np.int16).astype(np.float32) / 32768.0
        except Exception as e:
            print(f"[VideoProcessor] FFmpeg direct stream decode failed: {e}")
        return None

    def remux_audio_into_video(
        self,
        video_path: str,
        audio_path: str,
        output_video_path: str
    ) -> str:
        """
        Remuxes new audio into original video container.
        Uses -c:v copy to guarantee zero video re-encoding loss.
        """
        out_video = Path(output_video_path)
        out_video.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(out_video)
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg remuxing failed: {res.stderr}")
        return str(out_video)

    def blend_audio_clips(self, audio_paths: list, output_path: str, volume: float = 0.82) -> str:
        """
        Blends multiple audio files into a single composite track with balanced volume.
        Ensures sample-accurate sync and zero audio clipping for simultaneous dual-character speech.
        """
        valid_paths = [p for p in audio_paths if Path(p).exists() and Path(p).stat().st_size > 0]
        if not valid_paths:
            raise FileNotFoundError("No valid audio files provided to blend.")
        if len(valid_paths) == 1:
            shutil.copy(str(valid_paths[0]), str(output_path))
            return str(output_path)

        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        filter_inputs = "".join([f"[{i}:a]volume={volume:.2f}[a{i}];" for i in range(len(valid_paths))])
        filter_amix = "".join([f"[a{i}]" for i in range(len(valid_paths))]) + f"amix=inputs={len(valid_paths)}:duration=longest:dropout_transition=0:normalize=0[aout]"
        filter_complex = f"{filter_inputs}{filter_amix}"

        cmd = ["ffmpeg", "-y"]
        for p in valid_paths:
            cmd.extend(["-i", str(p)])
        cmd.extend(["-filter_complex", filter_complex, "-map", "[aout]", "-c:a", "libmp3lame", "-b:a", "192k", str(out_p)])
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return str(output_path)

    def replace_video_voice(
        self,
        video_path: str,
        target_character: str,
        voice_engine: VoiceEngine,
        output_video_path: str,
        preset: Optional[str] = None,
        voice_id_override: Optional[str] = None,
        settings_override: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        End-to-End Pipeline:
        1. Extract video audio to WAV.
        2. Convert voice using ElevenLabs Speech-to-Speech (Voice Changer).
        3. Remux converted audio back into video container.
        Zero manual Camtasia syncing required.
        """
        video_name = Path(video_path).stem
        temp_wav = self.temp_dir / f"{video_name}_extracted.wav"
        temp_converted = self.temp_dir / f"{video_name}_converted_{target_character}.mp3"

        # Step 1: Extract Audio
        print(f"[VideoProcessor] Extracting audio from {video_path}...")
        self.extract_audio(video_path, str(temp_wav))

        # Step 2: Convert Voice via Speech-to-Speech
        print(f"[VideoProcessor] Running ElevenLabs Voice Changer for character '{target_character}'...")
        sts_res = voice_engine.convert_speech_to_speech(
            input_audio_path=str(temp_wav),
            target_character=target_character,
            output_path=str(temp_converted),
            preset=preset,
            voice_id_override=voice_id_override,
            settings_override=settings_override,
            dry_run=dry_run,
            overwrite=overwrite
        )

        if dry_run:
            return {
                "status": "dry_run",
                "video": str(video_path),
                "target_character": target_character,
                "output_video": str(output_video_path),
                "sts_result": sts_res
            }

        # Step 3: Remux converted audio back into video
        print(f"[VideoProcessor] Remuxing converted audio into {output_video_path}...")
        self.remux_audio_into_video(
            video_path=video_path,
            audio_path=str(temp_converted),
            output_video_path=output_video_path
        )

        return {
            "status": "success",
            "video_path": str(output_video_path),
            "target_character": target_character,
            "voice_name": sts_res.get("voice_name")
        }

    def match_video_to_storyboard(
        self,
        video_path: str,
        storyboard_items: list,
        max_duration: float = 25.0
    ) -> Dict[str, Any]:
        """
        100% Free, Zero-Credit Automatic Matcher:
        Matches video dialogue to the exact slide from the PPT storyboard.
        1. Checks filename for slide markers (e.g. S03, Slide_4, M5_07).
        2. Runs local Whisper to transcribe video speech (100% free, offline).
        3. Fuzzy-matches against PPT storyboard lines using SequenceMatcher.
        4. Returns the best matching slide, speaker, ground-truth script, and confidence score.
        """
        import re
        import difflib

        v_path = Path(video_path)
        v_name = v_path.stem.lower()

        # 1. Filename heuristic check (e.g. S03, slide3, _03_)
        filename_slide_match = re.search(r'(?:slide|s|_)([0-9]{1,3})', v_name)
        hint_slide = int(filename_slide_match.group(1)) if filename_slide_match else None

        # 2. Extract audio preview (first 25s) for fast local transcription
        transcribed_text = ""
        try:
            audio_target = self.decode_audio_for_whisper(str(video_path), duration_seconds=max_duration)
            if audio_target is None:
                temp_preview = self.temp_dir / f"{v_path.stem}_preview.wav"
                self.extract_audio(str(video_path), str(temp_preview), duration_seconds=max_duration)
                audio_target = str(temp_preview)

            whisper = self.get_whisper_model()
            segments, _ = whisper.transcribe(audio_target, beam_size=1)
            transcribed_text = " ".join(seg.text for seg in segments).strip()
            print(f"[VideoProcessor] Local Whisper transcribed: '{transcribed_text}'")
        except Exception as e:
            print(f"[VideoProcessor] Local Whisper transcription skipped: {e}")

        best_item = None
        best_score = 0.0

        if storyboard_items:
            clean_trans = re.sub(r'[^a-zA-Z0-9\s]', '', transcribed_text.lower())
            for item in storyboard_items:
                slide_text = item.get("text", "")
                clean_slide = re.sub(r'[^a-zA-Z0-9\s]', '', slide_text.lower())

                score = 0.0
                if clean_trans and clean_slide:
                    # Calculate similarity between transcription and slide text
                    score = difflib.SequenceMatcher(None, clean_trans, clean_slide).ratio()

                # Boost score if filename hint matches
                if hint_slide is not None and item.get("slide") == hint_slide:
                    score = max(score, 0.95)

                if score > best_score:
                    best_score = score
                    best_item = item

            # If no transcription match but filename hint exists
            if (not best_item or best_score < 0.3) and hint_slide is not None:
                for item in storyboard_items:
                    if item.get("slide") == hint_slide:
                        best_item = item
                        best_score = 0.95
                        break

            # Fallback to first item if nothing matched
            if not best_item and storyboard_items:
                best_item = storyboard_items[0]
                best_score = 0.50

        if best_item:
            return {
                "matched": True,
                "slide": best_item.get("slide"),
                "speaker": best_item.get("speaker"),
                "script_text": best_item.get("text"),
                "confidence": round(best_score * 100, 1),
                "transcribed_text": transcribed_text,
                "method": "whisper_ai" if transcribed_text else "filename_detection"
            }
        else:
            return {
                "matched": False,
                "slide": None,
                "speaker": None,
                "script_text": transcribed_text,
                "confidence": 0.0,
                "transcribed_text": transcribed_text,
                "method": "none"
            }

    def analyze_video_timeline(
        self,
        video_path: str,
        storyboard_items: Optional[list] = None,
        max_duration: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        100% Free Local Speech Recognition:
        Extracts dialogue segments from video and fuzzy-matches against PPT storyboard lines.
        If max_duration is None, 0, or <= 0, transcribes the entire video without time truncation.
        """
        import re
        import difflib

        if storyboard_items is None:
            storyboard_items = []

        v_path = Path(video_path)
        temp_audio = self.temp_dir / f"{v_path.stem}_timeline.wav"
        
        # None, 0, or negative means full video length
        effective_duration = float(max_duration) if (max_duration is not None and float(max_duration) > 0) else None

        # 1. Extract audio & transcribe using faster-whisper (direct FFmpeg memory decode)
        raw_segments = []
        try:
            audio_target = self.decode_audio_for_whisper(str(video_path), duration_seconds=effective_duration)
            if audio_target is None:
                self.extract_audio(str(video_path), str(temp_audio), duration_seconds=effective_duration)
                audio_target = str(temp_audio)

            whisper = self.get_whisper_model()
            segs, info = whisper.transcribe(
                audio_target,
                beam_size=1,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=250)
            )
            for s in segs:
                txt = s.text.strip()
                if txt:
                    # Snap to true acoustic word boundaries if word timestamps are present
                    if s.words:
                        w_start = round(s.words[0].start, 2)
                        w_end = round(s.words[-1].end, 2)
                    else:
                        w_start = round(s.start, 2)
                        w_end = round(s.end, 2)
                    clean_txt = re.sub(r'\[.*?\]|\(.*?\)', '', txt).strip()
                    is_valid_dur = (w_end - w_start) >= 0.12
                    has_real_words = bool(re.search(r'[a-zA-Z0-9]', clean_txt))
                    if is_valid_dur and has_real_words:
                        raw_segments.append({
                            "start": w_start,
                            "end": w_end,
                            "text": txt
                        })
        except Exception as e:
            print(f"[VideoProcessor] Whisper timeline error: {e}")
            try:
                self.extract_audio(str(video_path), str(temp_audio), duration_seconds=effective_duration)
                whisper = self.get_whisper_model()
                segs, info = whisper.transcribe(
                    str(temp_audio),
                    beam_size=1,
                    word_timestamps=True,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=250)
                )
                for s in segs:
                    txt = s.text.strip()
                    if txt:
                        if s.words:
                            w_start = round(s.words[0].start, 2)
                            w_end = round(s.words[-1].end, 2)
                        else:
                            w_start = round(s.start, 2)
                            w_end = round(s.end, 2)
                        clean_txt = re.sub(r'\[.*?\]|\(.*?\)', '', txt).strip()
                        is_valid_dur = (w_end - w_start) >= 0.12
                        has_real_words = bool(re.search(r'[a-zA-Z0-9]', clean_txt))
                        if is_valid_dur and has_real_words:
                            raw_segments.append({
                                "start": w_start,
                                "end": w_end,
                                "text": txt
                            })
            except Exception as e2:
                print(f"[VideoProcessor] Whisper timeline fallback error: {e2}")

        # 3. Only merge micro-fragments (e.g. trailing word under 1s without punctuation)
        merged = []
        for s in raw_segments:
            if not merged:
                merged.append(s)
            else:
                prev = merged[-1]
                ends_with_terminal = prev["text"].rstrip().endswith(('.', '!', '?'))
                if not ends_with_terminal and (s["start"] - prev["end"] < 0.6) and (prev["end"] - prev["start"] < 3.5):
                    prev["end"] = s["end"]
                    prev["text"] = f"{prev['text']} {s['text']}".strip()
                else:
                    merged.append(s)

        # Ensure the last dialogue segment extends safely towards the video end if speech is near the tail
        total_media_dur = self.get_media_duration(str(video_path))
        if merged and total_media_dur > 0:
            last_seg = merged[-1]
            if (total_media_dur - last_seg["end"]) < 0.9:
                last_seg["end"] = round(total_media_dur, 2)

        # 4. Multi-level fuzzy matching against PPT storyboard lines and sub-sentences
        processed_segments = []
        last_matched_slide = None

        # Ensure temp_audio is present to detect high-precision vocal onset & bounds
        try:
            if not temp_audio.exists():
                self.extract_audio(str(video_path), str(temp_audio), duration_seconds=effective_duration)
        except Exception as e_wav:
            print(f"[VideoProcessor] Timeline master audio extraction warning: {e_wav}")

        for idx, s in enumerate(merged):
            s_start = s.get("start", 0.0)
            s_end = s.get("end", 0.0)
            s_clean = re.sub(r'[^a-zA-Z0-9\s]', '', s["text"].lower()).strip()
            best_candidate = None
            best_score = 0.0
            best_item = None

            if storyboard_items:
                for item in storyboard_items:
                    full_text = item.get("text", "").strip()
                    if not full_text:
                        continue
                    
                    # 1. Candidate text variants
                    candidates = [full_text]
                    sentences = [sent.strip() for sent in re.split(r'(?<=[.!?])\s+', full_text) if sent.strip()]
                    if len(sentences) > 1:
                        candidates.extend(sentences)
                        for k in range(len(sentences) - 1):
                            candidates.append(f"{sentences[k]} {sentences[k+1]}")

                    # 2. Timecode proximity factor if item has start_time and end_time
                    it_start = item.get("start_time")
                    it_end = item.get("end_time")
                    time_score = 0.0
                    has_timecode = (it_start is not None and it_end is not None)
                    if has_timecode:
                        overlap_s = max(s_start, it_start)
                        overlap_e = min(s_end, it_end)
                        overlap = max(0.0, overlap_e - overlap_s)
                        union = max(s_end, it_end) - min(s_start, it_start)
                        iou = overlap / union if union > 0 else 0.0
                        start_dist = abs(s_start - it_start)

                        if iou > 0.15:
                            time_score = max(iou, 1.0 - (start_dist / 6.0))
                        elif start_dist < 4.0:
                            time_score = max(0.0, 1.0 - (start_dist / 4.0))

                    for cand in candidates:
                        clean_cand = re.sub(r'[^a-zA-Z0-9\s]', '', cand.lower()).strip()
                        text_ratio = difflib.SequenceMatcher(None, s_clean, clean_cand).ratio()

                        if has_timecode and time_score > 0:
                            ratio = (text_ratio * 0.60) + (time_score * 0.40)
                        else:
                            ratio = text_ratio

                        # Bonus for chronological order
                        if last_matched_slide is not None and item.get("slide", 0) >= last_matched_slide:
                            ratio += 0.05

                        if ratio > best_score:
                            best_score = ratio
                            best_candidate = cand
                            best_item = item

            # If confident match found
            if best_item and best_score >= 0.32:
                matched_slide = best_item.get("slide")
                matched_speaker = best_item.get("speaker")
                if matched_speaker:
                    clean_ms = matched_speaker.strip().lower()
                    if clean_ms in ("both", "dex & analytica", "dex and analytica", "dex/analytica", "dex + analytica"):
                        matched_speaker = "Dex + Analytica"
                script_text = best_candidate or best_item.get("text")
                confidence = round(min(best_score, 1.0) * 100, 1)
                last_matched_slide = matched_slide
            else:
                matched_slide = None
                matched_speaker = "Dex"
                script_text = s["text"]
                confidence = 0.0

            # Detect acoustic vocal bounds (opening & closing of mouth)
            vocal_start, vocal_end = s["start"], s["end"]
            if temp_audio.exists():
                vocal_start, vocal_end = self.detect_vocal_bounds(str(temp_audio), s["start"], s["end"])

            # If vocal_end extends beyond s["end"], safely expand s["end"] so trailing syllables are not clipped
            effective_end = max(s["end"], vocal_end)

            processed_segments.append({
                "id": idx,
                "start": s["start"],
                "end": effective_end,
                "duration": round(effective_end - s["start"], 2),
                "vocal_start": round(vocal_start, 2),
                "vocal_end": round(vocal_end, 2),
                "vocal_duration": round(vocal_end - vocal_start, 2),
                "heard_text": s["text"],
                "script_text": script_text,
                "speaker": matched_speaker,
                "slide": matched_slide,
                "confidence": confidence
            })

        return {
            "status": "success",
            "video_path": str(video_path),
            "total_segments": len(processed_segments),
            "segments": processed_segments
        }

    def replace_video_multi_speaker(
        self,
        video_path: str,
        segments: list,
        mode: str,
        voice_engine: VoiceEngine,
        output_video_path: str,
        preset: Optional[str] = None,
        model_id: Optional[str] = None,
        settings_override: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Multi-Speaker Video Voice Replacement.
        Supports:
        - 'strict_tts': Strict PPT Script Mode (Text-to-Speech)
        - 'voice_changer': Voice Changer Mode (Speech-to-Speech Slicing)
        """
        import subprocess

        v_path = Path(video_path)
        out_v = Path(output_video_path)
        out_v.parent.mkdir(parents=True, exist_ok=True)

        mode_label = (
            "Strict PPT Script Mode (Text-to-Speech)"
            if mode == "strict_tts"
            else "Voice Changer Mode (Speech-to-Speech)"
        )

        # Estimate Credits (scales if multiple characters speak simultaneously)
        def _get_spk_count(s):
            spk = (s.get("speaker") or "Dex").strip()
            parts = [p.strip() for p in re.split(r'[\+&]|(?:\s+and\s+)', spk, flags=re.IGNORECASE) if p.strip()]
            return max(len(parts), 1)

        if mode == "strict_tts":
            total_credits = sum(len(s.get("script_text", "")) * _get_spk_count(s) for s in segments)
        else:
            total_credits = sum(int(s.get("duration", 0) * 15) * _get_spk_count(s) for s in segments)

        if dry_run:
            return {
                "status": "dry_run",
                "mode": mode,
                "mode_label": mode_label,
                "model_id": model_id or "eleven_v4",
                "total_segments": len(segments),
                "estimated_credits": total_credits,
                "video_path": str(video_path),
                "output_video": str(output_video_path),
                "segments": segments
            }

        # Step 1: Extract master original audio from video
        master_audio = self.temp_dir / f"{v_path.stem}_master_audio.wav"
        self.extract_audio(str(video_path), str(master_audio))

        # Step 2: Generate/convert audio for each segment
        seg_audio_files = []
        for idx, seg in enumerate(segments):
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            raw_speaker = (seg.get("speaker") or "Dex").strip()
            clean_speaker = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_speaker) or "Speaker"
            script = seg.get("script_text", "")
            seg_out = self.temp_dir / f"seg_{v_path.stem}_{idx}_{clean_speaker}.mp3"

            # Parse speakers for simultaneous multi-character speech (e.g. "Dex + Analytica")
            speakers = [s.strip() for s in re.split(r'[\+&]|(?:\s+and\s+)', raw_speaker, flags=re.IGNORECASE) if s.strip()]
            if not speakers:
                speakers = [raw_speaker]

            effective_start = start
            seg_mode = (seg.get("mode") or mode).lower()
            if seg_mode == "strict_tts":
                if len(speakers) > 1:
                    part_files = []
                    for spk_idx, spk in enumerate(speakers):
                        clean_spk = re.sub(r'[^a-zA-Z0-9_\-]', '', spk) or f"Speaker{spk_idx}"
                        spk_out = self.temp_dir / f"seg_{v_path.stem}_{idx}_{clean_spk}_part.mp3"
                        voice_engine.generate_tts(
                            text=script,
                            character=spk,
                            output_path=str(spk_out),
                            preset=preset,
                            settings_override=settings_override,
                            model_id_override=model_id,
                            dry_run=False,
                            overwrite=overwrite
                        )
                        if spk_out.exists() and spk_out.stat().st_size > 0:
                            part_files.append(spk_out)
                    self.blend_audio_clips(part_files, str(seg_out), volume=0.82)
                else:
                    # Synthesize directly from PPT ground truth script with specified or default model
                    res = voice_engine.generate_tts(
                        text=script,
                        character=raw_speaker,
                        output_path=str(seg_out),
                        preset=preset,
                        settings_override=settings_override,
                        model_id_override=model_id,
                        dry_run=False,
                        overwrite=overwrite
                    )

                # 1. Strip leading digital silence / encoder padding from generated TTS
                trimmed_out = self.temp_dir / f"trim_{seg_out.name}"
                try:
                    trim_cmd = [
                        "ffmpeg", "-y",
                        "-i", str(seg_out),
                        "-af", "silenceremove=start_periods=1:start_duration=0.01:start_threshold=-45dB",
                        str(trimmed_out)
                    ]
                    res_trim = subprocess.run(trim_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    if res_trim.returncode == 0 and trimmed_out.exists() and trimmed_out.stat().st_size > 100:
                        shutil.move(str(trimmed_out), str(seg_out))
                except Exception as trim_err:
                    print(f"[VideoProcessor] Silence trim warning: {trim_err}")

                # 2. Align speech start with pre-roll acoustic onset to eliminate speech lag and initial repetition
                prev_end = segments[idx - 1]["end"] if idx > 0 else 0.0
                safe_pre_roll = max(prev_end + 0.03, start - 0.20)
                onset = seg.get("vocal_start")
                if onset is not None and onset < start and onset >= safe_pre_roll:
                    effective_start = onset
                else:
                    effective_start = max(safe_pre_roll, start - 0.12)

                # 3. Bi-directional cadence fitting using FFmpeg atempo
                # Calculate hard deadline using the next segment's start time to strictly prevent overlapping
                next_seg_start = segments[idx + 1]["start"] if idx + 1 < len(segments) else None
                hard_deadline = min(end, next_seg_start - 0.04) if next_seg_start else end
                effective_slot = max(hard_deadline - effective_start, 0.25)

                if effective_slot > 0.35:
                    actual_dur = self.get_media_duration(str(seg_out))
                    # Speed up if speech overflows the actor's window
                    if actual_dur > (effective_slot + 0.08):
                        speed_ratio = actual_dur / effective_slot
                        # Scale up to 1.55x smoothly with atempo so actor mouth closure is respected
                        safe_speed = min(speed_ratio, 1.55)
                        fitted_out = self.temp_dir / f"fit_{seg_out.name}"
                        try:
                            fit_cmd = [
                                "ffmpeg", "-y",
                                "-i", str(seg_out),
                                "-filter:a", f"atempo={safe_speed:.3f}",
                                str(fitted_out)
                            ]
                            res_fit = subprocess.run(fit_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                            if res_fit.returncode == 0 and fitted_out.exists() and fitted_out.stat().st_size > 0:
                                shutil.move(str(fitted_out), str(seg_out))
                                print(f"[VideoProcessor] atempo compression applied: {seg_out.name} ({actual_dur:.2f}s -> {effective_slot:.2f}s at {safe_speed:.2f}x)")
                        except Exception as fit_err:
                            print(f"[VideoProcessor] atempo fit failed: {fit_err}")
                    # Gentle stretch if speech finished significantly too fast (> 400ms earlier)
                    elif actual_dur < (effective_slot - 0.40) and effective_slot > 1.2:
                        speed_ratio = actual_dur / effective_slot
                        safe_speed = max(speed_ratio, 0.88)  # Limit slow-down to 12% to preserve natural timbre
                        fitted_out = self.temp_dir / f"fit_{seg_out.name}"
                        try:
                            fit_cmd = [
                                "ffmpeg", "-y",
                                "-i", str(seg_out),
                                "-filter:a", f"atempo={safe_speed:.3f}",
                                str(fitted_out)
                            ]
                            res_fit = subprocess.run(fit_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                            if res_fit.returncode == 0 and fitted_out.exists() and fitted_out.stat().st_size > 0:
                                shutil.move(str(fitted_out), str(seg_out))
                                print(f"[VideoProcessor] atempo stretch applied: {seg_out.name} ({actual_dur:.2f}s -> {effective_slot:.2f}s at {safe_speed:.2f}x)")
                        except Exception as fit_err:
                            print(f"[VideoProcessor] atempo fit failed: {fit_err}")
            else:
                # Slice original video audio snippet and voice-change
                # Ensure slice captures full vocal tail without cutting off trailing syllables
                slice_start = start
                slice_end = end
                vocal_e = seg.get("vocal_end")
                if vocal_e is not None and vocal_e > slice_end:
                    slice_end = vocal_e
                # If this is the last segment near video end, extend to video duration
                if idx == len(segments) - 1:
                    master_dur = self.get_media_duration(str(master_audio))
                    if master_dur > 0 and (master_dur - slice_end) < 0.9:
                        slice_end = master_dur

                raw_chunk = self.temp_dir / f"chunk_{v_path.stem}_{idx}.wav"
                slice_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(slice_start),
                    "-to", str(slice_end),
                    "-i", str(master_audio),
                    "-c:a", "pcm_s16le",
                    str(raw_chunk)
                ]
                subprocess.run(slice_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                is_dewobble_mode = seg_mode in ("de_wobble", "dewobble", "voice_changer_dewobble", "dewobbled")

                if is_dewobble_mode:
                    # Optional Mode: De-Wobbled Voice Changer with Clean Acoustic Pre-filtering (80Hz rumble cut + FFT spectral denoising)
                    # Never uses aggressive dynaudnorm or compressors which pump background hiss and clip word onsets
                    clean_chunk = self.temp_dir / f"chunk_{v_path.stem}_{idx}_clean.wav"
                    clean_cmd = [
                        "ffmpeg", "-y",
                        "-i", str(raw_chunk),
                        "-af", "highpass=f=80,afftdn=nf=-32",
                        str(clean_chunk)
                    ]
                    res_clean = subprocess.run(clean_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    input_for_sts = str(clean_chunk) if (res_clean.returncode == 0 and clean_chunk.exists() and clean_chunk.stat().st_size > 0) else str(raw_chunk)
                else:
                    # Standard Voice Changer: 100% Original Unmodified Audio Slice
                    input_for_sts = str(raw_chunk)

                if len(speakers) > 1:
                    # Option B: Dual-Morph Parallel Blend (Voice Changer STS)
                    part_files = []
                    for spk_idx, spk in enumerate(speakers):
                        clean_spk = re.sub(r'[^a-zA-Z0-9_\-]', '', spk) or f"Speaker{spk_idx}"
                        spk_out = self.temp_dir / f"seg_{v_path.stem}_{idx}_{clean_spk}_sts_part.mp3"
                        voice_engine.convert_speech_to_speech(
                            input_audio_path=input_for_sts,
                            target_character=spk,
                            output_path=str(spk_out),
                            preset=preset,
                            settings_override=settings_override,
                            de_wobble=is_dewobble_mode,
                            dry_run=False,
                            overwrite=overwrite
                        )
                        if spk_out.exists() and spk_out.stat().st_size > 0:
                            part_files.append(spk_out)
                    self.blend_audio_clips(part_files, str(seg_out), volume=0.82)
                else:
                    res = voice_engine.convert_speech_to_speech(
                        input_audio_path=input_for_sts,
                        target_character=raw_speaker,
                        output_path=str(seg_out),
                        preset=preset,
                        settings_override=settings_override,
                        de_wobble=is_dewobble_mode,
                        dry_run=False,
                        overwrite=overwrite
                    )

            seg_audio_files.append((seg, seg_out, effective_start))

        # Step 3: High-capacity sample-accurate audio track assembler
        # Replaces fragile multi-input FFmpeg amix (which crashes with status 244 on large videos / >30 segments)
        # with an ultra-fast, zero-memory-leak, sample-accurate PCM composite engine.
        import wave
        import numpy as np

        # 3a. Read master extracted original audio (PCM 16-bit)
        with wave.open(str(master_audio), 'rb') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            master_raw = wf.readframes(n_frames)

        master_samples = np.frombuffer(master_raw, dtype=np.int16).copy()
        if n_channels > 1:
            master_samples = master_samples.reshape(-1, n_channels)
        else:
            master_samples = master_samples.reshape(-1, 1)

        # 3b. Mute original audio during segment intervals with 5ms smooth crossfade
        fade_len = min(int(0.005 * framerate), 256)
        fade_out = np.linspace(1.0, 0.0, fade_len)[:, np.newaxis]
        fade_in = np.linspace(0.0, 1.0, fade_len)[:, np.newaxis]

        for seg_i, s in enumerate(segments):
            prev_end = segments[seg_i - 1]["end"] if seg_i > 0 else 0.0
            next_start = segments[seg_i + 1]["start"] if seg_i + 1 < len(segments) else None
            seg_m = (s.get("mode") or mode).lower()

            # For strict TTS, apply acoustic pre-roll & post-roll to completely erase original vocal onsets and tail bleed
            if seg_m == "strict_tts":
                m_start = max(prev_end + 0.03, s["start"] - 0.20)
                raw_end = max(s["end"], s.get("vocal_end", s["end"]))
                m_end = min(next_start - 0.03, raw_end + 0.15) if next_start else (raw_end + 0.15)
            else:
                m_start = s["start"]
                m_end = max(s["end"], s.get("vocal_end", s["end"]))

            s_idx = max(0, min(int(m_start * framerate), len(master_samples)))
            e_idx = max(0, min(int(m_end * framerate), len(master_samples)))
            if s_idx < e_idx:
                # Apply 5ms fade-out at start
                if s_idx + fade_len <= len(master_samples):
                    master_samples[s_idx:s_idx+fade_len] = (master_samples[s_idx:s_idx+fade_len] * fade_out).astype(np.int16)
                # Zero out middle
                mid_start = min(s_idx + fade_len, e_idx)
                mid_end = max(s_idx, e_idx - fade_len)
                if mid_start < mid_end:
                    master_samples[mid_start:mid_end] = 0
                # Apply 5ms fade-in at end
                if mid_end < e_idx and e_idx <= len(master_samples):
                    actual_fade = e_idx - mid_end
                    master_samples[mid_end:e_idx] = (master_samples[mid_end:e_idx] * fade_in[:actual_fade]).astype(np.int16)

        # 3c. Overlay each synthesized dialogue segment at its exact effective_start timestamp
        for seg_idx, (seg, audio_f, eff_start) in enumerate(seg_audio_files):
            if not Path(audio_f).exists():
                continue
            # Decode segment audio to matching PCM sample rate & channels
            cmd_dec = [
                "ffmpeg", "-y",
                "-i", str(audio_f),
                "-f", "s16le",
                "-ac", str(n_channels),
                "-acodec", "pcm_s16le",
                "-ar", str(framerate),
                "-"
            ]
            res_dec = subprocess.run(cmd_dec, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if res_dec.returncode != 0 or not res_dec.stdout:
                continue

            seg_data = np.frombuffer(res_dec.stdout, dtype=np.int16).copy().reshape(-1, n_channels)
            ins_start = max(0, int(eff_start * framerate))

            if ins_start < len(master_samples):
                usable_len = min(len(seg_data), len(master_samples) - ins_start)

                # HARD COLLISION BARRIER: Ensure this segment never bleeds into the next segment
                if seg_idx + 1 < len(seg_audio_files):
                    next_start_time = seg_audio_files[seg_idx + 1][2]
                    max_allowed_len = max(0, int((next_start_time - 0.04) * framerate) - ins_start)
                    if usable_len > max_allowed_len:
                        usable_len = max_allowed_len
                        # Apply gentle 10ms fade-out at cutoff point
                        cutoff_fade = min(int(0.010 * framerate), usable_len)
                        if cutoff_fade > 0:
                            fade_curve = np.linspace(1.0, 0.0, cutoff_fade)[:, np.newaxis]
                            seg_data[usable_len-cutoff_fade:usable_len] = (
                                seg_data[usable_len-cutoff_fade:usable_len] * fade_curve
                            ).astype(np.int16)

                mixed = (master_samples[ins_start:ins_start+usable_len].astype(np.int32) +
                         seg_data[:usable_len].astype(np.int32))
                master_samples[ins_start:ins_start+usable_len] = np.clip(mixed, -32768, 32767).astype(np.int16)

        # 3d. Write final mixed audio track
        final_mixed_wav = self.temp_dir / f"{v_path.stem}_final_mixed.wav"
        with wave.open(str(final_mixed_wav), 'wb') as wf:
            wf.setnchannels(n_channels)
            wf.setsampwidth(sampwidth)
            wf.setframerate(framerate)
            wf.writeframes(master_samples.tobytes())

        # 3e. Remux mixed audio into original video container (Zero video re-encoding)
        remux_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(final_mixed_wav),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(out_v)
        ]
        subprocess.run(remux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        if final_mixed_wav.exists():
            final_mixed_wav.unlink(missing_ok=True)

        return {
            "status": "success",
            "mode": mode,
            "mode_label": mode_label,
            "video_path": str(out_v),
            "video_url": f"/api/video-stream/{out_v.name}",
            "total_segments": len(segments),
            "segments_processed": len(seg_audio_files)
        }

    def export_timeline_audio_batch(
        self,
        video_path: str,
        segments: list,
        mode: str,
        voice_engine: VoiceEngine,
        output_zip_path: str,
        character_filter: Optional[str] = None,
        preset: Optional[str] = None,
        model_id: Optional[str] = None,
        settings_override: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Batch-generates standalone high-quality audio files for a selected subset/character
        and bundles them with a manifest into a downloadable ZIP archive.
        """
        import csv
        import zipfile
        import shutil
        import re

        v_path = Path(video_path)
        out_zip = Path(output_zip_path)
        out_zip.parent.mkdir(parents=True, exist_ok=True)

        # Filter segments if character_filter is specified
        if character_filter and character_filter.lower() != "all":
            filtered_segments = [s for s in segments if s.get("speaker", "").strip().lower() == character_filter.strip().lower()]
        else:
            filtered_segments = segments

        if not filtered_segments:
            raise ValueError(f"No segments found to export (character filter: '{character_filter}')")

        mode_label = (
            "Strict Script Mode (Text-to-Speech)"
            if mode == "strict_tts"
            else "Voice Changer Mode (Speech-to-Speech)"
        )

        # Estimate Credits (scales if multiple characters speak simultaneously)
        def _get_spk_count(s):
            spk = (s.get("speaker") or "Speaker").strip()
            parts = [p.strip() for p in re.split(r'[\+&]|(?:\s+and\s+)', spk, flags=re.IGNORECASE) if p.strip()]
            return max(len(parts), 1)

        if mode == "strict_tts":
            total_credits = sum(len(s.get("script_text") or s.get("heard_text") or "") * _get_spk_count(s) for s in filtered_segments)
        else:
            total_credits = sum(int(s.get("duration", 0) * 15) * _get_spk_count(s) for s in filtered_segments)

        if dry_run:
            return {
                "status": "dry_run",
                "mode": mode,
                "mode_label": mode_label,
                "character": character_filter or "all",
                "model_id": model_id or "eleven_v4",
                "total_segments": len(filtered_segments),
                "estimated_credits": total_credits,
                "segments": filtered_segments
            }

        # Create temporary working directory for this export
        import time, uuid
        export_folder = self.temp_dir / f"export_{v_path.stem}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        export_folder.mkdir(parents=True, exist_ok=True)

        master_audio = None
        if mode == "voice_changer":
            master_audio = self.temp_dir / f"{v_path.stem}_master_audio.wav"
            if not master_audio.exists():
                self.extract_audio(str(video_path), str(master_audio))

        generated_files = []
        manifest_rows = []

        def sanitize_name(val):
            return re.sub(r'[^a-zA-Z0-9_\-]', '', str(val)).strip()

        for idx, seg in enumerate(filtered_segments):
            start = seg.get("start", 0.0)
            end = seg.get("end", 0.0)
            dur = round(seg.get("duration", end - start), 2)
            raw_speaker = (seg.get("speaker") or "Speaker").strip()
            clean_speaker = sanitize_name(raw_speaker) or "Speaker"
            slide = seg.get("slide", "")
            script = seg.get("script_text") or seg.get("heard_text") or ""
            heard = seg.get("heard_text", "")

            # Formatted time string e.g. 00m07s
            start_str = f"{int(start//60):02d}m{int(start%60):02d}s"
            end_str = f"{int(end//60):02d}m{int(end%60):02d}s"
            seg_filename = f"{idx+1:02d}_{clean_speaker}_{start_str}_to_{end_str}.mp3"
            seg_out = export_folder / seg_filename

            if mode == "strict_tts":
                voice_engine.generate_tts(
                    text=script,
                    character=raw_speaker,
                    output_path=str(seg_out),
                    preset=preset,
                    settings_override=settings_override,
                    model_id_override=model_id,
                    dry_run=False,
                    overwrite=overwrite
                )
                # Trim leading digital silence for crisp standalone playback
                trimmed_seg = export_folder / f"trim_{seg_filename}"
                try:
                    res_trim = subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(seg_out),
                        "-af", "silenceremove=start_periods=1:start_duration=0.01:start_threshold=-45dB",
                        str(trimmed_seg)
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if res_trim.returncode == 0 and trimmed_seg.exists() and trimmed_seg.stat().st_size > 100:
                        shutil.move(str(trimmed_seg), str(seg_out))
                except Exception:
                    pass
            else:
                raw_chunk = export_folder / f"raw_chunk_{idx}.wav"
                slice_start = start
                slice_end = end
                vocal_e = seg.get("vocal_end")
                if vocal_e is not None and vocal_e > slice_end:
                    slice_end = vocal_e
                if idx == len(filtered_segments) - 1:
                    master_dur = self.get_media_duration(str(master_audio))
                    if master_dur > 0 and (master_dur - slice_end) < 0.9:
                        slice_end = master_dur

                slice_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(slice_start),
                    "-to", str(slice_end),
                    "-i", str(master_audio),
                    "-c:a", "pcm_s16le",
                    str(raw_chunk)
                ]
                subprocess.run(slice_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                speakers = [s.strip() for s in re.split(r'[\+&]|(?:\s+and\s+)', raw_speaker, flags=re.IGNORECASE) if s.strip()]
                if not speakers:
                    speakers = [raw_speaker]

                if len(speakers) > 1:
                    part_files = []
                    for spk_idx, spk in enumerate(speakers):
                        clean_spk = re.sub(r'[^a-zA-Z0-9_\-]', '', spk) or f"Speaker{spk_idx}"
                        spk_out = export_folder / f"part_{idx}_{clean_spk}.mp3"
                        voice_engine.convert_speech_to_speech(
                            input_audio_path=str(raw_chunk),
                            target_character=spk,
                            output_path=str(spk_out),
                            preset=preset,
                            settings_override=settings_override,
                            dry_run=False,
                            overwrite=overwrite
                        )
                        if spk_out.exists() and spk_out.stat().st_size > 0:
                            part_files.append(spk_out)
                    self.blend_audio_clips(part_files, str(seg_out), volume=0.82)
                else:
                    voice_engine.convert_speech_to_speech(
                        input_audio_path=str(raw_chunk),
                        target_character=raw_speaker,
                        output_path=str(seg_out),
                        preset=preset,
                        settings_override=settings_override,
                        dry_run=False,
                        overwrite=overwrite
                    )
                if raw_chunk.exists():
                    raw_chunk.unlink(missing_ok=True)

            generated_files.append(seg_out)
            manifest_rows.append({
                "Index": idx + 1,
                "Start_Seconds": start,
                "End_Seconds": end,
                "Duration_Sec": dur,
                "Speaker": raw_speaker,
                "Slide": slide,
                "Audio_Filename": seg_filename,
                "Script_Text": script,
                "Spoken_In_Video_STT": heard
            })

        # Write manifest.csv into the export folder
        manifest_csv_path = export_folder / "manifest.csv"
        if manifest_rows:
            with open(manifest_csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
                writer.writeheader()
                writer.writerows(manifest_rows)

        # Build ZIP archive
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zip_f:
            if manifest_csv_path.exists():
                zip_f.write(manifest_csv_path, arcname="manifest.csv")
            for gf in generated_files:
                if gf.exists():
                    zip_f.write(gf, arcname=gf.name)

        # Also copy generated MP3 files into output/audio for direct in-browser playback & direct MP3 download
        audio_dest_dir = Path("output/audio")
        audio_dest_dir.mkdir(parents=True, exist_ok=True)
        clip_details = []
        for gf in generated_files:
            if gf.exists():
                dest_clip = audio_dest_dir / gf.name
                shutil.copy2(str(gf), str(dest_clip))
                clip_details.append({
                    "filename": gf.name,
                    "audio_url": f"/api/audio/audio/{gf.name}"
                })

        # Clean up temporary export folder
        shutil.rmtree(export_folder, ignore_errors=True)

        return {
            "status": "success",
            "mode": mode,
            "mode_label": mode_label,
            "model_id": model_id or "eleven_v4",
            "total_files": len(generated_files),
            "output_zip": str(out_zip),
            "zip_filename": out_zip.name,
            "download_url": f"/api/download-export/{out_zip.name}",
            "clips": clip_details,
            "files": [f.name for f in generated_files]
        }

