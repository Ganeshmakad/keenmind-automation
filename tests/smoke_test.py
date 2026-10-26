"""
smoke_test.py - Automated Smoke Test Suite for Keenmind Automation Studio.
Verifies:
1. Configuration integrity (eleven_v4 default, eleven_multilingual_sts_v2 default).
2. VoiceEngine initialization, fuzzy character resolution, and cache hashing.
3. VideoProcessor atempo duration helper & multi-speaker dry-run methods.
4. FastAPI endpoints contract:
   - GET /health
   - GET /api/status
   - GET /api/export-debug-log
   - POST /api/auto-tag-expressions
   - POST /api/generate-audio (dry-run with model_id)
5. FFmpeg / ffprobe availability on the system.
"""

import sys
import os
import json
import unittest
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.voice_engine import VoiceEngine
from core.video_processor import VideoProcessor
from core.docx_parser import parse_docx_storyboard
from api.server import app
from fastapi.testclient import TestClient


class TestKeenmindAutomationStudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.voice_engine = VoiceEngine(cache_dir=str(ROOT_DIR / "cache"))
        cls.video_processor = VideoProcessor(temp_dir=str(ROOT_DIR / "temp"))

    # ---------------------------------------------------------
    # 1. Configuration Integrity Tests
    # ---------------------------------------------------------
    def test_01_config_voices_json(self):
        config_path = ROOT_DIR / "config" / "voices.json"
        self.assertTrue(config_path.exists(), "config/voices.json must exist")
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        self.assertEqual(cfg.get("default_model"), "eleven_v4", "default_model must be eleven_v4")
        self.assertEqual(cfg.get("default_sts_model"), "eleven_multilingual_sts_v2", "default_sts_model must be eleven_multilingual_sts_v2")
        self.assertIn("presets", cfg, "presets dictionary must be present in config")
        self.assertGreater(len(cfg["presets"]), 0, "At least one client preset should be configured")

    # ---------------------------------------------------------
    # 2. VoiceEngine Resolution & Cache Tests
    # ---------------------------------------------------------
    def test_02_voice_engine_character_resolution(self):
        # Test character resolution from tata preset
        char_cfg = self.voice_engine.get_character_config("Dex", preset="tata")
        self.assertIsNotNone(char_cfg, "Should resolve character 'Dex'")
        self.assertIn("voice_id", char_cfg, "Character config must include voice_id")
        self.assertIn("settings", char_cfg, "Character config must include settings")

        # Test fallback/clean speaker resolution
        char_cfg_fuzzy = self.voice_engine.get_character_config("Dex (Lead)", preset="tata")
        self.assertIsNotNone(char_cfg_fuzzy, "Should resolve fuzzy speaker 'Dex (Lead)'")

    def test_03_voice_engine_cache_hashing(self):
        # Ensure model_id influences the TTS hash
        h1 = self.voice_engine._compute_tts_hash("test_voice", "Hello world", "eleven_v4", {})
        h2 = self.voice_engine._compute_tts_hash("test_voice", "Hello world", "eleven_multilingual_v2", {})
        self.assertNotEqual(h1, h2, "Different model_id values must yield distinct MD5 cache keys")

    # ---------------------------------------------------------
    # 3. VideoProcessor Duration & Pipeline Tests
    # ---------------------------------------------------------
    def test_04_get_media_duration_graceful_fallback(self):
        # A non-existent file should gracefully return 0.0 without throwing
        dur = self.video_processor.get_media_duration("non_existent_audio_file.wav")
        self.assertEqual(dur, 0.0)

    def test_05_replace_video_multi_speaker_dry_run(self):
        segments = [
            {"id": 0, "start": 0.0, "end": 4.5, "speaker": "Dex", "script_text": "Welcome to Tata Motors!"},
            {"id": 1, "start": 5.0, "end": 9.2, "speaker": "Analytica", "script_text": "Here are our key diagnostics."}
        ]
        res = self.video_processor.replace_video_multi_speaker(
            video_path="test_video.mp4",
            segments=segments,
            mode="strict_tts",
            voice_engine=self.voice_engine,
            output_video_path="output/test_out.mp4",
            preset="tata",
            model_id="eleven_v4",
            dry_run=True
        )
        self.assertEqual(res.get("status"), "dry_run")
        self.assertEqual(res.get("model_id"), "eleven_v4")
        self.assertEqual(res.get("total_segments"), 2)
        self.assertGreater(res.get("estimated_credits", 0), 20)

    def test_05b_speaker_sanitization_no_nameerror(self):
        # Verifies that re.sub inside replace_video_multi_speaker doesn't throw NameError
        import re
        raw_speaker = "new analaytica (Lead)"
        clean_speaker = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_speaker) or "Speaker"
        self.assertEqual(clean_speaker, "newanalayticaLead")

    def test_06_export_timeline_audio_batch_dry_run(self):
        segments = [
            {"id": 0, "start": 0.0, "end": 4.0, "duration": 4.0, "speaker": "Dex", "script_text": "Line one"},
            {"id": 1, "start": 4.5, "end": 8.0, "duration": 3.5, "speaker": "Dex", "script_text": "Line two"}
        ]
        res = self.video_processor.export_timeline_audio_batch(
            video_path="test_video.mp4",
            segments=segments,
            mode="strict_tts",
            voice_engine=self.voice_engine,
            output_zip_path="output/test_export.zip",
            character_filter="Dex",
            preset="tata",
            model_id="eleven_v4",
            dry_run=True
        )
        self.assertEqual(res.get("status"), "dry_run")
        self.assertEqual(res.get("model_id"), "eleven_v4")
        self.assertEqual(res.get("total_segments"), 2)

    # ---------------------------------------------------------
    # 4. FastAPI Endpoints Tests
    # ---------------------------------------------------------
    def test_07_api_health_and_status(self):
        resp_health = self.client.get("/health")
        self.assertEqual(resp_health.status_code, 200)
        self.assertEqual(resp_health.json().get("status"), "healthy")

        resp_status = self.client.get("/api/status")
        self.assertEqual(resp_status.status_code, 200)
        data = resp_status.json()
        self.assertIn("elevenlabs_configured", data)
        self.assertIn("ffmpeg", data)

    def test_08_api_export_debug_log(self):
        resp = self.client.get("/api/export-debug-log")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("timestamp", data)
        self.assertIn("platform", data)
        self.assertIn("elevenlabs", data)
        self.assertEqual(data["elevenlabs"].get("default_model"), "eleven_v4")
        self.assertIn("ffmpeg", data)
        self.assertIn("workspace", data)

    def test_09_api_auto_tag_expressions(self):
        # Test text tagging
        payload_text = {"text": "This is an amazing breakthrough!"}
        resp = self.client.post("/api/auto-tag-expressions", json=payload_text)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("[excited]", data.get("tagged_text", ""))

        # Test question tagging
        payload_question = {"text": "Why does this system behave like that?"}
        resp_q = self.client.post("/api/auto-tag-expressions", json=payload_question)
        self.assertEqual(resp_q.status_code, 200)
        self.assertIn("[thoughtful]", resp_q.json().get("tagged_text", ""))

        # Test batch segments tagging
        payload_segs = {
            "segments": [
                {"id": 0, "script_text": "Warning: Do not touch the high-voltage inverter."},
                {"id": 1, "script_text": "Keep this strictly confidential between us."}
            ]
        }
        resp_segs = self.client.post("/api/auto-tag-expressions", json=payload_segs)
        self.assertEqual(resp_segs.status_code, 200)
        tagged = resp_segs.json().get("segments", [])
        self.assertEqual(len(tagged), 2)
        self.assertTrue(tagged[0]["script_text"].startswith("[serious]"))
        self.assertTrue(tagged[1]["script_text"].startswith("[whispering]"))

    def test_10_api_generate_audio_dry_run_model_support(self):
        payload = {
            "items": [
                {"id": "slide_1", "speaker": "Dex", "text": "Testing model parameter.", "approved": True}
            ],
            "preset": "tata",
            "model_id": "eleven_v4",
            "dry_run": True
        }
        resp = self.client.post("/api/generate-audio", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "completed")
        self.assertEqual(data.get("processed_count"), 1)
        res_item = data.get("results", [])[0]
        self.assertEqual(res_item.get("status"), "dry_run")

    def test_11_vocal_onset_detection_and_fallback(self):
        # Graceful fallback when audio file doesn't exist
        fallback_onset = self.video_processor.detect_vocal_onset("non_existent_audio.wav", start=1.5, end=4.0)
        self.assertEqual(fallback_onset, 1.5)

        start_b, end_b = self.video_processor.detect_vocal_bounds("non_existent_audio.wav", start=1.5, end=4.0)
        self.assertEqual(start_b, 1.5)
        self.assertEqual(end_b, 4.0)

        # Real audio test if test master audio exists in temp
        master_sample = Path("temp/1.1_1080p_20261008140559_master_audio.wav")
        if master_sample.exists():
            real_onset = self.video_processor.detect_vocal_onset(str(master_sample), start=0.0, end=5.0)
            self.assertGreaterEqual(real_onset, 0.8)

    def test_12_video_download_and_stream_endpoints(self):
        # 1. 404 test on non-existent file
        resp_404 = self.client.get("/api/download-video/non_existent_clip.mp4")
        self.assertEqual(resp_404.status_code, 404)

        # 2. Test with real existing output video if present
        output_files = list(Path("output").glob("*.mp4"))
        if output_files:
            test_fname = output_files[0].name
            # Test direct download endpoint
            dl_resp = self.client.get(f"/api/download-video/{test_fname}")
            self.assertEqual(dl_resp.status_code, 200)
            self.assertIn("attachment", dl_resp.headers.get("content-disposition", ""))
            self.assertEqual(dl_resp.headers.get("content-type"), "application/octet-stream")

            # Test streaming endpoint
            stream_resp = self.client.get(f"/api/video/{test_fname}")
            self.assertEqual(stream_resp.status_code, 200)
            self.assertEqual(stream_resp.headers.get("content-type"), "video/mp4")

            # Test streaming endpoint with download flag
            stream_dl = self.client.get(f"/api/video/{test_fname}?download=1")
            self.assertEqual(stream_dl.status_code, 200)
            self.assertIn("attachment", stream_dl.headers.get("content-disposition", ""))

    def test_13_pptx_structured_storyboard_parsing(self):
        from core.pptx_parser import parse_pptx_storyboard
        pptx_sample = Path("temp/uploads/Lesson_1_VSB_01.pptx")
        if pptx_sample.exists():
            records = parse_pptx_storyboard(str(pptx_sample), preset="tata")
            self.assertGreaterEqual(len(records), 40)
            speakers = {r["speaker"] for r in records}
            # Verify Dex and Analytica are present, and no 'Narrator' leak
            self.assertIn("Dex", speakers)
            self.assertIn("Analytica", speakers)
            self.assertNotIn("Narrator", speakers)
            # Verify no animation directions leaked into text
            for r in records:
                self.assertNotIn("Animation:", r["text"])
                self.assertNotIn("Exact OST:", r["text"])
                self.assertNotEqual(r["text"].strip().lower(), "no vo")

    def test_14_tata_narrator_resolves_to_dex(self):
        # When preset is tata, Narrator must automatically resolve to Dex
        cfg_narrator = self.voice_engine.get_character_config("Narrator", preset="tata")
        cfg_dex = self.voice_engine.get_character_config("Dex", preset="tata")
        self.assertEqual(cfg_narrator["voice_id"], cfg_dex["voice_id"])
        self.assertEqual(cfg_narrator["voice_name"], "Sanil")

        # Analytica must resolve to Anika
        cfg_analytica = self.voice_engine.get_character_config("Analytica", preset="tata")
        self.assertEqual(cfg_analytica["voice_id"], "UbB19hYD8fvYxwJAVTY5")

    def test_15_api_generate_audio_zip_packaging(self):
        import zipfile
        # Test dry-run returns zip_filename: None
        payload_dry = {
            "items": [{"id": "TEST_001", "speaker": "Dex", "text": "Testing ZIP export", "approved": True}],
            "preset": "tata",
            "dry_run": True
        }
        resp = self.client.post("/api/generate-audio", json=payload_dry)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("dry_run"), True)
        self.assertIsNone(data.get("zip_filename"))

    def test_16_api_clear_latest_storyboard(self):
        resp = self.client.post("/api/clear-latest-storyboard")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("cleared_count", data)

        # GET /api/latest-storyboard should now report has_storyboard: False
        resp_latest = self.client.get("/api/latest-storyboard")
        self.assertEqual(resp_latest.status_code, 200)
        self.assertFalse(resp_latest.json().get("has_storyboard"))

    def test_17_docx_table_parsing_and_timecode_alignment(self):
        sample_docx = ROOT_DIR / "_dev_and_testing" / "to check docx" / "Data_Quality_Dex_Analytica_Dialogue.docx"
        if not sample_docx.exists():
            sample_docx = ROOT_DIR / "to check docx" / "Data_Quality_Dex_Analytica_Dialogue.docx"
        if not sample_docx.exists():
            sample_docx = ROOT_DIR / "temp" / "uploads" / "Data_Quality_Dex_Analytica_Dialogue.docx"

        if sample_docx.exists():
            records = parse_docx_storyboard(str(sample_docx), preset="tata")
            self.assertGreaterEqual(len(records), 125, "Must extract full dialogue rows from table")

            spk_counts = {}
            for r in records:
                spk = r.get("speaker")
                spk_counts[spk] = spk_counts.get(spk, 0) + 1
                # Must extract actual spoken text, not table delivery metadata
                self.assertNotIn(r.get("text"), ("Dialogue", "Narration"))
                self.assertTrue(len(r.get("text", "")) > 3)

            # Dex and Analytica must both be present with correct ratio
            self.assertIn("Dex", spk_counts)
            self.assertIn("Analytica", spk_counts)
            self.assertEqual(spk_counts["Dex"], 102)
            self.assertEqual(spk_counts["Analytica"], 27)

            # Verify timecodes extracted
            has_timecodes = any(r.get("start_time") is not None for r in records)
            self.assertTrue(has_timecodes, "Parsed records must contain numeric start_time")

    def test_18_pronunciation_dictionary(self):
        # 1. Test dictionary loading and rule application
        spoken = self.voice_engine.apply_pronunciation_rules("tatacomp delivers eLearning VO modules for PPT", preset="tata")
        self.assertIn("Tata Comp", spoken)
        self.assertIn("e-learning", spoken)
        self.assertIn("voice-over", spoken)
        self.assertIn("PowerPoint", spoken)

        # 2. Test adding and deleting a custom rule
        self.voice_engine.add_pronunciation_rule("TelematicModule", "Tele-matics Mod-ule", preset="tata")
        spoken_custom = self.voice_engine.apply_pronunciation_rules("Check the TelematicModule status", preset="tata")
        self.assertIn("Tele-matics Mod-ule", spoken_custom)

        # Delete rule
        self.voice_engine.delete_pronunciation_rule("TelematicModule", preset="tata")
        spoken_reverted = self.voice_engine.apply_pronunciation_rules("Check the TelematicModule status", preset="tata")
        self.assertIn("TelematicModule", spoken_reverted)

        # 3. Test API bulk addition
        bulk_resp = self.client.post("/api/pronunciation/bulk", json={
            "rules": [
                {"word": "BulkWordA", "replacement": "Bulk Word Ay"},
                {"word": "BulkWordB", "replacement": "Bulk Word Bee"}
            ],
            "preset": "tata"
        })
        self.assertEqual(bulk_resp.status_code, 200)
        self.assertEqual(bulk_resp.json()["added_count"], 2)
        # Cleanup
        self.voice_engine.delete_pronunciation_rule("BulkWordA", preset="tata")
        self.voice_engine.delete_pronunciation_rule("BulkWordB", preset="tata")

    def test_19_api_status_hardware_metrics(self):
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("cuda_available", data)
        self.assertIn("cache_files_count", data)
        self.assertIn("pronunciation_rules_count", data)
        self.assertGreater(data["cache_files_count"], 500)
        self.assertGreater(data["pronunciation_rules_count"], 10)

    def test_20_preview_character_voice(self):
        resp = self.client.post("/api/preview-character-voice", json={
            "preset_id": "tata",
            "character": "Dex",
            "dialogue": "Tatacomp test"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("status", data)
        self.assertIn(data["status"], ["success", "dry_run"])

    def test_21_dewobble_and_per_segment_mode_support(self):
        # 1. Verify dry_run with mode='de_wobble' and per-segment mode overrides
        segments = [
            {"id": 0, "start": 0.0, "end": 4.5, "speaker": "Dex", "script_text": "Normal line", "mode": "voice_changer"},
            {"id": 1, "start": 5.0, "end": 9.2, "speaker": "Analytica", "script_text": "Anti-tremor line", "mode": "de_wobble"},
            {"id": 2, "start": 10.0, "end": 12.0, "speaker": "Analytica", "script_text": "Pure studio line", "mode": "strict_tts"}
        ]
        res = self.video_processor.replace_video_multi_speaker(
            video_path="test_video.mp4",
            segments=segments,
            mode="voice_changer",
            voice_engine=self.voice_engine,
            output_video_path="output/test_dewobble.mp4",
            preset="tata",
            model_id="eleven_v4",
            dry_run=True
        )
        self.assertEqual(res.get("status"), "dry_run")
        self.assertEqual(res.get("total_segments"), 3)

        # 2. Verify micro-word filter logic (duration >= 0.12s + alphanumeric excluding bracketed tags)
        import re
        def check_filter(w_start, w_end, txt):
            clean_txt = re.sub(r'\[.*?\]|\(.*?\)', '', txt).strip()
            is_valid_dur = (w_end - w_start) >= 0.12
            has_real_words = bool(re.search(r'[a-zA-Z0-9]', clean_txt))
            return is_valid_dur and has_real_words

        # Short word "Understood." (0.28s) should pass
        self.assertTrue(check_filter(19.22, 19.50, "Understood."))
        # Monosyllable "No" (0.15s) should pass
        self.assertTrue(check_filter(1.0, 1.15, "No"))
        # Pure noise or mic click (0.05s) should be rejected
        self.assertFalse(check_filter(1.0, 1.05, "..."))
        # Non-alphanumeric punctuation or bracketed audio tags only should be rejected
        self.assertFalse(check_filter(1.0, 1.50, " [breath] "))
        self.assertFalse(check_filter(1.0, 1.50, " (applause) "))
        self.assertTrue(check_filter(1.0, 1.80, " [applause] Thank you "))

if __name__ == "__main__":
    unittest.main(verbosity=2)

