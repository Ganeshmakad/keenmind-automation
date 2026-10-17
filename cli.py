"""
cli.py - Command Line Interface for Keenmind Audio & Video Automation Engine.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from core.pptx_parser import parse_pptx_storyboard, export_manifest
from core.voice_engine import VoiceEngine
from core.video_processor import VideoProcessor

def cmd_parse(args):
    print(f"\n=== Parsing Storyboard PPTX: {args.pptx} ===")
    records = parse_pptx_storyboard(args.pptx)
    
    out_json = args.output_json or f"output/{Path(args.pptx).stem}_manifest.json"
    out_csv = args.output_csv or f"output/{Path(args.pptx).stem}_manifest.csv"
    export_manifest(records, out_json, out_csv)
    print(f"Success! {len(records)} voiceover items extracted.")

def cmd_generate_audio(args):
    print(f"\n=== Generating Audio from Manifest: {args.manifest} ===")
    with open(args.manifest, "r", encoding="utf-8") as f:
        data = json.load(f)
        items = data.get("items", [])

    if not items:
        print("No items found in manifest.")
        return

    engine = VoiceEngine(config_path=args.config, cache_dir=args.cache_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    limit = args.limit or len(items)
    selected_items = [it for it in items if it.get("approved", True)][:limit]

    print(f"Processing {len(selected_items)} items (Dry-run: {args.dry_run})...")

    results = []
    for idx, item in enumerate(selected_items, start=1):
        item_id = item["id"]
        speaker = item.get("speaker", "Narrator")
        text = item.get("text", "")
        dest_audio = out_dir / f"{item_id}.mp3"

        print(f"[{idx}/{len(selected_items)}] {item_id} ({speaker}): {text[:45]}...")
        try:
            res = engine.generate_tts(
                text=text,
                character=speaker,
                output_path=str(dest_audio),
                dry_run=args.dry_run,
                overwrite=args.overwrite
            )
            results.append({"id": item_id, "speaker": speaker, **res})
        except Exception as e:
            print(f"  Error: {e}")
            results.append({"id": item_id, "speaker": speaker, "status": "error", "error": str(e)})

    # Summary
    success_count = sum(1 for r in results if r.get("status") in ("success", "cached", "dry_run"))
    print(f"\nCompleted: {success_count}/{len(selected_items)} items processed successfully.")

def cmd_replace_video(args):
    print(f"\n=== Replacing Video Audio via Voice Changer ===")
    print(f"Input Video: {args.video}")
    print(f"Target Character: {args.character}")
    print(f"Output Video: {args.output}")
    print(f"Dry-run: {args.dry_run}")

    engine = VoiceEngine(config_path=args.config, cache_dir=args.cache_dir)
    processor = VideoProcessor(temp_dir=args.temp_dir)

    res = processor.replace_video_voice(
        video_path=args.video,
        target_character=args.character,
        voice_engine=engine,
        output_video_path=args.output,
        dry_run=args.dry_run,
        overwrite=args.overwrite
    )
    print("\nResult:", json.dumps(res, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Keenmind Audio & Video Automation Engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: parse
    p_parse = subparsers.add_parser("parse", help="Extract voiceover dialogue from storyboard PPTX")
    p_parse.add_argument("--pptx", required=True, help="Path to input .pptx storyboard")
    p_parse.add_argument("--output-json", help="Path to output JSON manifest")
    p_parse.add_argument("--output-csv", help="Path to output CSV manifest")
    p_parse.set_defaults(func=cmd_parse)

    # Subcommand: generate-audio
    p_gen = subparsers.add_parser("generate-audio", help="Batch synthesize audio from manifest")
    p_gen.add_argument("--manifest", required=True, help="Path to manifest JSON")
    p_gen.add_argument("--output-dir", default="output/audio", help="Output directory for audio files")
    p_gen.add_argument("--config", default="config/voices.json", help="Voice mappings config path")
    p_gen.add_argument("--cache-dir", default="cache", help="Cache directory")
    p_gen.add_argument("--limit", type=int, help="Limit number of items to generate (testing)")
    p_gen.add_argument("--dry-run", action="store_true", help="Validate without spending credits")
    p_gen.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    p_gen.set_defaults(func=cmd_generate_audio)

    # Subcommand: replace-video-audio
    p_vid = subparsers.add_parser("replace-video-audio", help="Replace character voice inside a video clip")
    p_vid.add_argument("--video", required=True, help="Input video file (.mp4)")
    p_vid.add_argument("--character", required=True, help="Target character (Analytica, Dex, etc.)")
    p_vid.add_argument("--output", required=True, help="Output video path")
    p_vid.add_argument("--config", default="config/voices.json", help="Voice config path")
    p_vid.add_argument("--cache-dir", default="cache", help="Cache directory")
    p_vid.add_argument("--temp-dir", default="temp", help="Temp working directory")
    p_vid.add_argument("--dry-run", action="store_true", help="Validate without spending credits")
    p_vid.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    p_vid.set_defaults(func=cmd_replace_video)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
