"""
voice_engine.py - ElevenLabs Text-to-Speech & Speech-to-Speech (Voice Changer) Engine.
Includes strict credit-guard caching, dry-run mode, and character voice mappings.
"""

import os
import re
import difflib
import json
import hashlib
import time
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

class VoiceEngine:
    def __init__(self, api_key: Optional[str] = None, config_path: str = "config/voices.json", cache_dir: str = "cache", pronunciation_dict_path: str = "config/pronunciation_dictionary.json"):
        self.api_key = (api_key or os.getenv("ELEVENLABS_API_KEY", "")).strip()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Load character voice configuration
        self.config_path = Path(config_path)
        self.pronunciation_dict_path = Path(pronunciation_dict_path)
        self._load_config()

    def _load_pronunciation_dict(self) -> Dict[str, Any]:
        """Loads pronunciation dictionary fresh from disk."""
        if self.pronunciation_dict_path.exists():
            try:
                with open(self.pronunciation_dict_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[VoiceEngine] Warning reading pronunciation dictionary: {e}")
        return {"global": {}, "presets": {}}

    def get_pronunciation_dictionary(self) -> Dict[str, Any]:
        """Returns the full pronunciation dictionary."""
        return self._load_pronunciation_dict()

    def save_pronunciation_dictionary(self, data: Dict[str, Any]) -> bool:
        """Saves pronunciation dictionary to disk."""
        try:
            self.pronunciation_dict_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.pronunciation_dict_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"[VoiceEngine] Error saving pronunciation dictionary: {e}")
            return False

    def add_pronunciation_rule(self, word: str, replacement: str, preset: Optional[str] = None) -> Dict[str, Any]:
        """Adds or updates a pronunciation rule."""
        data = self._load_pronunciation_dict()
        word = str(word).strip()
        replacement = str(replacement).strip()
        if preset:
            if "presets" not in data:
                data["presets"] = {}
            if preset not in data["presets"]:
                data["presets"][preset] = {}
            data["presets"][preset][word] = replacement
        else:
            if "global" not in data:
                data["global"] = {}
            data["global"][word] = replacement
        self.save_pronunciation_dictionary(data)
        return data

    def delete_pronunciation_rule(self, word: str, preset: Optional[str] = None) -> Dict[str, Any]:
        """Deletes a pronunciation rule."""
        data = self._load_pronunciation_dict()
        word = str(word).strip()
        if preset and "presets" in data and preset in data["presets"]:
            data["presets"][preset].pop(word, None)
        elif "global" in data:
            data["global"].pop(word, None)
        self.save_pronunciation_dictionary(data)
        return data

    def apply_pronunciation_rules(self, text: str, preset: Optional[str] = None) -> str:
        """
        Applies phonetic pronunciation substitutions to text.
        Substitutes whole words using regex matching so substrings are not erroneously replaced.
        Preset rules override global rules.
        """
        if not text:
            return ""
        data = self._load_pronunciation_dict()
        rules: Dict[str, str] = {}
        if "global" in data and isinstance(data["global"], dict):
            rules.update(data["global"])
        eff_preset = preset or self.config.get("active_preset")
        if eff_preset and "presets" in data and eff_preset in data["presets"]:
            preset_rules = data["presets"][eff_preset]
            if isinstance(preset_rules, dict):
                rules.update(preset_rules)

        result = text
        for word, repl in rules.items():
            if not word or not repl:
                continue
            pattern = re.compile(rf'\b{re.escape(word)}\b', re.IGNORECASE)
            result = pattern.sub(repl, result)

        return result

    def get_api_key(self, force_reload: bool = False) -> str:
        """Returns the active ElevenLabs API key, automatically re-reading .env if updated."""
        if self.api_key and not force_reload:
            return self.api_key
            
        candidates = [
            Path(".env"),
            Path("/app/.env"),
            Path(__file__).resolve().parent.parent / ".env"
        ]
        for p in candidates:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("ELEVENLABS_API_KEY="):
                                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                                if val:
                                    self.api_key = val
                                    return val
                except Exception:
                    pass

        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
            k = os.getenv("ELEVENLABS_API_KEY", "").strip()
            if k:
                self.api_key = k
                return k
        except Exception:
            pass

        return self.api_key or ""
            
    def _load_config(self):
        """Loads configuration fresh from disk if it exists."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                print(f"[VoiceEngine] Warning loading config: {e}")
                if not hasattr(self, "config"):
                    self.config = {"default_model": "eleven_multilingual_v2", "presets": {}}
        else:
            self.config = {
                "default_model": "eleven_multilingual_v2",
                "presets": {}
            }

    def get_presets(self) -> Dict[str, Any]:
        """Returns all configured client presets."""
        self._load_config()
        return self.config.get("presets", {})

    def get_preset(self, preset_id: str) -> Optional[Dict[str, Any]]:
        """Returns a specific preset by ID."""
        self._load_config()
        return self.config.get("presets", {}).get(preset_id)

    def add_or_update_preset(self, preset_id: str, name: str, description: str = "", preset_type: str = "video") -> Dict[str, Any]:
        """Creates or updates a client preset."""
        self._load_config()
        if "presets" not in self.config:
            self.config["presets"] = {}
        if preset_id not in self.config["presets"]:
            self.config["presets"][preset_id] = {
                "name": name,
                "type": preset_type,
                "description": description,
                "characters": {}
            }
        else:
            self.config["presets"][preset_id]["name"] = name
            self.config["presets"][preset_id]["description"] = description
            self.config["presets"][preset_id]["type"] = preset_type
        self._save_config()
        return self.config["presets"][preset_id]

    def add_or_update_character(self, preset_id: str, character: str, voice_id: str, voice_name: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Adds or updates a character under a specific preset."""
        self._load_config()
        if "presets" not in self.config or preset_id not in self.config["presets"]:
            self.add_or_update_preset(preset_id, name=preset_id.title())
        
        char_obj = {
            "voice_id": voice_id,
            "voice_name": voice_name,
            "settings": settings
        }
        self.config["presets"][preset_id]["characters"][character] = char_obj
        self._save_config()
        return char_obj

    def delete_character(self, preset_id: str, character: str) -> bool:
        """Deletes a character from a preset."""
        self._load_config()
        if preset_id in self.config.get("presets", {}):
            if character in self.config["presets"][preset_id].get("characters", {}):
                del self.config["presets"][preset_id]["characters"][character]
                self._save_config()
                return True
        return False

    def delete_preset(self, preset_id: str) -> bool:
        """Deletes a client preset entirely."""
        self._load_config()
        if "presets" in self.config and preset_id in self.config["presets"]:
            del self.config["presets"][preset_id]
            self._save_config()
            return True
        return False

    def _save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def get_character_config(self, character_name: str, preset: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns voice_id and voice_settings for a character, respecting preset if provided.
        Employs robust normalized matching (space-stripped, case-insensitive, punctuation-stripped)
        so names like 'New Analytica', 'new analaytica', and 'newanalaytica' always match correctly.
        """
        presets = self.config.get("presets", {})
        char_name_clean = str(character_name or "").strip()

        # In Tata Motors module, Dex is the sole lead narrator (no separate narrator).
        eff_preset = preset or self.config.get("active_preset")
        if eff_preset == "tata" and char_name_clean.lower() in ["narrator", "lead narrator", "default", "vo", "narration", ""]:
            char_name_clean = "Dex"

        def _norm(s: str) -> str:
            return re.sub(r'[^a-zA-Z0-9]', '', str(s or '')).lower()

        target_norm = _norm(char_name_clean)

        def _find_in_chars(chars_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if not isinstance(chars_dict, dict):
                return None
            # 1. Exact match
            if char_name_clean in chars_dict:
                return chars_dict[char_name_clean]
            # 2. Case-insensitive match
            for k, v in chars_dict.items():
                if k.strip().lower() == char_name_clean.lower():
                    return v
            # 3. Space & punctuation stripped normalized match
            norm_map = {}
            for k, v in chars_dict.items():
                k_norm = _norm(k)
                norm_map[k_norm] = v
                if target_norm and k_norm == target_norm:
                    return v
            # 4. Fuzzy similarity matching (e.g. 'newanalytica' vs 'newanalaytica')
            if target_norm and norm_map:
                close = difflib.get_close_matches(target_norm, list(norm_map.keys()), n=1, cutoff=0.72)
                if close:
                    return norm_map[close[0]]
            # 5. Substring matching (e.g. 'Dex (Lead)' matches 'Dex')
            if target_norm and norm_map:
                for k_norm, v in norm_map.items():
                    if k_norm and (k_norm in target_norm or target_norm in k_norm):
                        return v
            return None

        # 1. Check specific preset if given
        if preset and preset in presets:
            match = _find_in_chars(presets[preset].get("characters", {}))
            if match:
                return match

        # 2. Check active preset configured in voices.json
        active_preset = self.config.get("active_preset")
        if active_preset and active_preset in presets:
            match = _find_in_chars(presets[active_preset].get("characters", {}))
            if match:
                return match

        # 3. Search across all other presets
        for p_id, p_data in presets.items():
            match = _find_in_chars(p_data.get("characters", {}))
            if match:
                return match

        # 4. Fallback to legacy characters dict if present
        legacy_chars = self.config.get("characters", {})
        match = _find_in_chars(legacy_chars)
        if match:
            return match

        # 5. Fallback default
        if "Narrator" in legacy_chars:
            return legacy_chars["Narrator"]

        # Default fallback with warning
        print(f"[VoiceEngine WARNING] Character '{character_name}' not found in presets! Falling back to Viraj.")
        return {
            "voice_id": "bajNon13EdhNMndG3z05",
            "voice_name": "Viraj",
            "settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.2, "speed": 0.9, "use_speaker_boost": True}
        }

    def _compute_tts_hash(self, voice_id: str, text: str, model_id: str, settings: Dict[str, Any]) -> str:
        payload = f"{voice_id}|{text.strip()}|{model_id}|{json.dumps(settings, sort_keys=True)}"
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _compute_file_hash(self, file_path: str) -> str:
        h = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def update_character_config(self, character: str, voice_id: str, voice_name: str, settings: Dict[str, Any]):
        """Updates and persists character voice settings."""
        if "characters" not in self.config:
            self.config["characters"] = {}
        self.config["characters"][character] = {
            "voice_id": voice_id,
            "voice_name": voice_name,
            "settings": settings
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)

    def fetch_account_voices(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Queries ElevenLabs API for available voices with rich metadata and disk caching."""
        cache_file = Path("cache/elevenlabs_voices.json")
        if not force_refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if isinstance(cached, list) and len(cached) > 0:
                        return cached
            except Exception as e:
                print(f"Error reading voices cache: {e}")

        key = self.get_api_key()
        if not key:
            return [
                {
                    "voice_id": "jUjRbhZWoMK4aDciW36V",
                    "name": "Anika",
                    "category": "premade",
                    "description": "Warm and engaging Indian female voice, ideal for instructional and dialogue.",
                    "labels": {"accent": "indian", "gender": "female", "age": "young", "use_case": "narration"},
                    "preview_url": ""
                },
                {
                    "voice_id": "Fpi5XUKwuD84kRo0jLtj",
                    "name": "Sanil",
                    "category": "premade",
                    "description": "Crisp and professional Indian male voice, perfect for technical and character dialogue.",
                    "labels": {"accent": "indian", "gender": "male", "age": "middle_aged", "use_case": "conversational"},
                    "preview_url": ""
                },
                {
                    "voice_id": "bajNon13EdhNMndG3z05",
                    "name": "Viraj",
                    "category": "premade",
                    "description": "Authoritative and clear Indian narrator voice.",
                    "labels": {"accent": "indian", "gender": "male", "age": "middle_aged", "use_case": "narration"},
                    "preview_url": ""
                },
                {
                    "voice_id": "21m00Tcm4TlvDq8ikWAM",
                    "name": "Rachel",
                    "category": "premade",
                    "description": "Calm, pleasant young American female voice.",
                    "labels": {"accent": "american", "gender": "female", "age": "young", "use_case": "narration"},
                    "preview_url": ""
                },
                {
                    "voice_id": "AZnzlk1XvdvUeBnXmlld",
                    "name": "Domi",
                    "category": "premade",
                    "description": "Strong, engaging young American female voice.",
                    "labels": {"accent": "american", "gender": "female", "age": "young", "use_case": "narration"},
                    "preview_url": ""
                },
                {
                    "voice_id": "EXAVITQu4vr4xnSDxMaL",
                    "name": "Bella",
                    "category": "premade",
                    "description": "Soft and sweet young American female voice.",
                    "labels": {"accent": "american", "gender": "female", "age": "young", "use_case": "narration"},
                    "preview_url": ""
                }
            ]
        try:
            resp = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for v in data.get("voices", []):
                    results.append({
                        "voice_id": v.get("voice_id", ""),
                        "name": v.get("name", ""),
                        "category": v.get("category", "premade"),
                        "description": v.get("description") or "",
                        "preview_url": v.get("preview_url") or "",
                        "labels": v.get("labels") or {}
                    })
                # Cache to disk for instant subsequent loading
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2)
                return results
        except Exception as e:
            print(f"Failed to fetch voices from ElevenLabs: {e}")
            if cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return []

    def fetch_community_voices(
        self,
        search: Optional[str] = None,
        accent: Optional[str] = None,
        gender: Optional[str] = None,
        category: Optional[str] = None,
        page: int = 0,
        page_size: int = 30
    ) -> Dict[str, Any]:
        """Queries ElevenLabs 19,000+ shared community voices with search & pagination."""
        key = self.get_api_key()
        if not key:
            return {"status": "success", "voices": [], "total_count": 0, "has_more": False, "page": page}

        params: Dict[str, Any] = {
            "page_size": min(max(page_size, 1), 50),
            "page": max(0, page)
        }
        if search and search.strip():
            params["search"] = search.strip()
        if accent and accent.lower() != "all":
            params["accent"] = accent.strip().lower()
        if gender and gender.lower() != "all":
            params["gender"] = gender.strip().lower()
        if category and category.lower() != "all":
            params["category"] = category.strip().lower()

        try:
            resp = requests.get(
                "https://api.elevenlabs.io/v1/shared-voices",
                headers={"xi-api-key": key},
                params=params,
                timeout=12
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for v in data.get("voices", []):
                    results.append({
                        "voice_id": v.get("voice_id", ""),
                        "name": v.get("name", ""),
                        "category": v.get("category", "community"),
                        "description": v.get("description") or "",
                        "preview_url": v.get("preview_url") or "",
                        "labels": {
                            "accent": v.get("accent") or "",
                            "gender": v.get("gender") or "",
                            "age": v.get("age") or "",
                            "descriptive": v.get("descriptive") or "",
                            "use_case": v.get("use_case") or ""
                        }
                    })
                return {
                    "status": "success",
                    "voices": results,
                    "total_count": data.get("total_count", len(results)),
                    "has_more": data.get("has_more", False),
                    "page": page
                }
            else:
                print(f"ElevenLabs shared-voices returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Error fetching community voices: {e}")

        return {"status": "error", "voices": [], "total_count": 0, "has_more": False, "page": page}

    def generate_tts(
        self,
        text: str,
        character: str,
        output_path: str,
        preset: Optional[str] = None,
        voice_id_override: Optional[str] = None,
        voice_name_override: Optional[str] = None,
        settings_override: Optional[Dict[str, Any]] = None,
        model_id_override: Optional[str] = None,
        dry_run: bool = False,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Generates TTS audio from text for a given character.
        Saves output to output_path. Respects overrides from UI.
        """
        dest_file = Path(output_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        char_cfg = self.get_character_config(character, preset=preset)
        voice_id = voice_id_override or char_cfg["voice_id"]
        voice_name = voice_name_override or char_cfg.get("voice_name", character)
        base_settings = char_cfg.get("settings", {})
        settings = {**base_settings, **settings_override} if settings_override else base_settings
        model_id = model_id_override or self.config.get("default_model", "eleven_multilingual_v2")

        spoken_text = self.apply_pronunciation_rules(text, preset=preset)
        cache_key_raw = self._compute_tts_hash(voice_id, text, model_id, settings)
        cache_key_spoken = self._compute_tts_hash(voice_id, spoken_text, model_id, settings)

        cached_file_raw = self.cache_dir / f"{cache_key_raw}.mp3"
        cached_file_spoken = self.cache_dir / f"{cache_key_spoken}.mp3"

        # Check existing cache (check spoken first, then raw text cache)
        cached_file = cached_file_spoken if cached_file_spoken.exists() else cached_file_raw

        # Check if already cached with these exact settings
        if cached_file.exists() and not overwrite:
            if not dest_file.exists() or dest_file.read_bytes() != cached_file.read_bytes():
                dest_file.write_bytes(cached_file.read_bytes())
            return {
                "status": "cached",
                "reason": "Restored from MD5 cache",
                "path": str(dest_file),
                "character": character,
                "voice_name": voice_name,
                "credits_used": 0
            }

        # If dest_file exists and matches cached_file, it was generated with the same settings
        if dest_file.exists() and not overwrite and cached_file.exists():
            return {
                "status": "skipped",
                "reason": "File already exists with matching settings",
                "path": str(dest_file),
                "character": character,
                "voice_name": voice_name,
                "credits_used": 0
            }

        if dry_run:
            return {
                "status": "dry_run",
                "character": character,
                "voice_name": voice_name,
                "voice_id": voice_id,
                "text_length": len(spoken_text),
                "estimated_credits": len(spoken_text),
                "path": str(dest_file)
            }

        key = self.get_api_key()
        if not key:
            raise ValueError("ELEVENLABS_API_KEY is not set. Add it to .env or pass to VoiceEngine.")

        # API Call
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": key,
            "Content-Type": "application/json"
        }
        body = {
            "text": spoken_text,
            "model_id": model_id,
            "voice_settings": {
                "stability": settings.get("stability", 0.50),
                "similarity_boost": settings.get("similarity_boost", 0.75),
                "style": settings.get("style", 0.20),
                "speed": settings.get("speed", 0.90),
                "use_speaker_boost": settings.get("use_speaker_boost", True)
            }
        }

        # Up to 3 retries
        for attempt in range(1, 4):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=60)
                if resp.status_code == 200:
                    audio_bytes = resp.content
                    dest_file.write_bytes(audio_bytes)
                    cached_file_spoken.write_bytes(audio_bytes)
                    if cached_file_raw != cached_file_spoken and not cached_file_raw.exists():
                        cached_file_raw.write_bytes(audio_bytes)
                    return {
                        "status": "success",
                        "path": str(dest_file),
                        "character": character,
                        "voice_name": voice_name,
                        "credits_used": len(spoken_text)
                    }
                elif resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(attempt * 2)
                else:
                    raise RuntimeError(f"ElevenLabs API error {resp.status_code}: {resp.text}")
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                time.sleep(attempt * 2)

        raise RuntimeError(f"Failed to generate TTS after 3 attempts.")

    def convert_speech_to_speech(
        self,
        input_audio_path: str,
        target_character: str,
        output_path: str,
        preset: Optional[str] = None,
        voice_id_override: Optional[str] = None,
        voice_name_override: Optional[str] = None,
        settings_override: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
        overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        ElevenLabs Speech-to-Speech (Voice Changer).
        Converts speech in input_audio_path into target_character's voice.
        """
        dest_file = Path(output_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        char_cfg = self.get_character_config(target_character, preset=preset)
        voice_id = voice_id_override or char_cfg["voice_id"]
        voice_name = voice_name_override or char_cfg.get("voice_name", target_character)
        base_settings = char_cfg.get("settings", {})
        settings = {**base_settings, **settings_override} if settings_override else base_settings
        model_id = self.config.get("default_sts_model", "eleven_multilingual_sts_v2")

        in_hash = self._compute_file_hash(input_audio_path)
        cache_key = hashlib.md5(f"{voice_id}|{in_hash}|{model_id}|{json.dumps(settings, sort_keys=True)}".encode("utf-8")).hexdigest()
        cached_file = self.cache_dir / f"sts_{cache_key}.mp3"

        if dest_file.exists() and not overwrite:
            return {
                "status": "skipped",
                "reason": "File already exists",
                "path": str(dest_file),
                "target_character": target_character,
                "voice_name": voice_name,
                "credits_used": 0
            }

        if cached_file.exists() and not overwrite:
            dest_file.write_bytes(cached_file.read_bytes())
            return {
                "status": "cached",
                "reason": "Restored from STS cache",
                "path": str(dest_file),
                "target_character": target_character,
                "voice_name": voice_name,
                "credits_used": 0
            }

        if dry_run:
            return {
                "status": "dry_run",
                "target_character": target_character,
                "voice_name": voice_name,
                "voice_id": voice_id,
                "input_audio": input_audio_path,
                "path": str(dest_file)
            }

        key = self.get_api_key()
        if not key:
            raise ValueError("ELEVENLABS_API_KEY is not set.")

        url = f"https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}"
        headers = {
            "xi-api-key": key
        }
        data = {
            "model_id": model_id,
            "voice_settings": json.dumps({
                "stability": settings.get("stability", 0.50),
                "similarity_boost": settings.get("similarity_boost", 0.75),
                "style": settings.get("style", 0.20),
                "use_speaker_boost": settings.get("use_speaker_boost", True)
            })
        }

        with open(input_audio_path, "rb") as audio_file:
            files = {
                "audio": audio_file
            }
            resp = requests.post(url, headers=headers, data=data, files=files, timeout=120)

        if resp.status_code == 200:
            dest_file.write_bytes(resp.content)
            cached_file.write_bytes(resp.content)
            return {
                "status": "success",
                "path": str(dest_file),
                "target_character": target_character,
                "voice_name": voice_name
            }
        else:
            raise RuntimeError(f"ElevenLabs STS error {resp.status_code}: {resp.text}")
