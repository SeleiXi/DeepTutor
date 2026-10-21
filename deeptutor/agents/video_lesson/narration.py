"""Voice-over for video lessons: speak each scene's narration and time its captions.

Speech comes from the TTS provider chosen in Settings › Voice. Without one, or when
it fails, Microsoft Edge's free read-aloud voices (the edge-tts package) step in.
When neither can speak, a scene keeps its captions, timed from an estimated
speaking rate, and plays silently.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import importlib.util
import io
import json
import logging
import math
import re
from typing import Any
import wave

from deeptutor.services.voice import synthesize_speech
from deeptutor.services.voice.adapters.edge import edge_rate

logger = logging.getLogger(__name__)

FPS = 30
VOICES = {"zh": "zh-CN-XiaoxiaoNeural", "en": "en-US-AvaNeural"}
SPEAK_TIMEOUT_S = 90
_CHARS_PER_SECOND = {"zh": 4.2, "en": 15.0}
_TICKS_PER_SECOND = 10_000_000  # edge-tts boundary offsets are in 100 ns units
_PARALLEL = 4
_SENTENCE_END = re.compile(r"(?<=[。！？；!?])|(?<=[.;])\s+")


@dataclass
class NarrationLine:
    id: str
    text: str


@dataclass
class Clip:
    text: str
    frames: int
    captions: list[tuple[int, str]]
    audio: bytes | None = None
    mime: str = "audio/mpeg"


def _lang(language: str) -> str:
    return "zh" if language.startswith("zh") else "en"


def estimate(text: str, language: str) -> Clip:
    """Silent clip: captions spaced by an average speaking rate."""
    rate = _CHARS_PER_SECOND[_lang(language)]
    at, captions = 0.0, []
    for sentence in (s.strip() for s in _SENTENCE_END.split(text)):
        if sentence:
            captions.append((round(at * FPS), sentence))
            at += len(sentence) / rate
    return Clip(text, max(1, math.ceil(at * FPS)), captions or [(0, text)])


# ------------------------------------------------------------------ audio length

_MP3_KBPS = {
    True: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    False: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_seconds(data: bytes) -> float:
    """Sum the MPEG layer III frames; works for constant and variable bitrates."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        i = 10 + (
            (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | data[9] & 0x7F
        )
    seconds = 0.0
    while i + 4 <= len(data):
        head = int.from_bytes(data[i : i + 4], "big")
        version, layer = (head >> 19) & 3, (head >> 17) & 3
        bitrate, rate_index = (head >> 12) & 15, (head >> 10) & 3
        if (
            head >> 21 != 0x7FF
            or version == 1
            or layer != 1
            or bitrate in (0, 15)
            or rate_index == 3
        ):
            i += 1
            continue
        mpeg1 = version == 3
        rate = _MP3_RATES[version][rate_index]
        seconds += (1152 if mpeg1 else 576) / rate
        i += (144 if mpeg1 else 72) * _MP3_KBPS[mpeg1][bitrate] * 1000 // rate + ((head >> 9) & 1)
    return seconds


def audio_seconds(data: bytes, content_type: str) -> float:
    if "wav" in content_type or data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as clip:
            return clip.getnframes() / clip.getframerate()
    if "mpeg" in content_type or "mp3" in content_type:
        return mp3_seconds(data)
    raise ValueError(f"cannot time {content_type} audio")


# ------------------------------------------------------------------ speakers


def _tts_config() -> Any | None:
    """The Settings › Voice selection, or None when nothing usable is configured."""
    try:
        from deeptutor.services.config.provider_runtime import resolve_tts_runtime_config

        return resolve_tts_runtime_config()
    except Exception:
        return None


async def speak_edge(text: str, voice: str, rate: str = "+0%") -> Clip:
    """Edge speech with sentence boundaries, so captions land on the spoken words."""
    import edge_tts

    audio = bytearray()
    captions: list[tuple[int, str]] = []
    async for chunk in edge_tts.Communicate(text, voice, rate=rate).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
        elif chunk["type"] == "SentenceBoundary":
            captions.append((round(chunk["offset"] / _TICKS_PER_SECOND * FPS), chunk["text"]))
    if not audio:
        raise RuntimeError("edge-tts returned no audio")
    frames = math.ceil(mp3_seconds(bytes(audio)) * FPS)
    return Clip(text, frames, captions or [(0, text)], bytes(audio))


async def speak_configured(text: str, language: str, formats: list[str]) -> Clip:
    """Any configured provider: time the audio itself, then spread the captions over it."""
    fmt = next((f for f in ("mp3", "wav") if f in formats), None)
    audio, content_type = await synthesize_speech(text, response_format=fmt, strip_markdown=False)
    frames = math.ceil(audio_seconds(audio, content_type) * FPS)
    if frames <= 0:
        raise RuntimeError("the provider returned empty audio")
    guess = estimate(text, language)
    captions = [(round(at * frames / guess.frames), line) for at, line in guess.captions]
    return Clip(text, frames, captions, audio, content_type.split(";")[0] or "audio/mpeg")


def _edge_voice(config: Any | None, language: str) -> tuple[str, str]:
    """A configured Edge voice in the lesson's language wins; otherwise the default."""
    lang = _lang(language)
    if config is not None and config.provider_name == "edge":
        voice = config.voice or ""
        rate = edge_rate(config.speed) if config.speed else "+0%"
        return (voice if voice.lower().startswith(lang) else VOICES[lang]), rate
    return VOICES[lang], "+0%"


async def narrate(
    lines: list[NarrationLine], language: str, cache: dict[str, Clip] | None = None
) -> dict[str, Clip]:
    """Record every line; ``cache`` keeps clips across retries of the same lesson."""
    cache = {} if cache is None else cache
    config = _tts_config()
    use_provider = config is not None and config.provider_name != "edge"
    formats: list[str] = []
    if use_provider:
        from deeptutor.services.voice.options import voice_model_options

        formats = voice_model_options(config.provider_name, "tts", config.model)["formats"]
    has_edge = importlib.util.find_spec("edge_tts") is not None
    edge_voice, rate = _edge_voice(config, language)
    gate = asyncio.Semaphore(_PARALLEL)

    async def one(text: str) -> Clip:
        if text in cache:
            return cache[text]
        clip = None
        async with gate:
            if use_provider:
                try:
                    clip = await asyncio.wait_for(
                        speak_configured(text, language, formats), SPEAK_TIMEOUT_S
                    )
                except Exception as exc:
                    logger.warning(
                        "video lesson narration via %s failed: %s", config.provider_name, exc
                    )
            if clip is None and has_edge:
                try:
                    clip = await asyncio.wait_for(
                        speak_edge(text, edge_voice, rate), SPEAK_TIMEOUT_S
                    )
                except Exception as exc:  # the free service is best effort; captions remain
                    logger.warning("video lesson narration via edge-tts failed: %s", exc)
        cache[text] = clip or estimate(text, language)
        return cache[text]

    clips = await asyncio.gather(*(one(line.text) for line in lines))
    return {line.id: clip for line, clip in zip(lines, clips)}


def voice_module(clips: dict[str, Clip]) -> str:
    """The ``voice.ts`` the kit imports: text, length, captions and inline audio per scene."""
    data = {}
    for key, clip in clips.items():
        entry: dict = {
            "text": clip.text,
            "frames": clip.frames,
            "captions": [{"at": at, "text": text} for at, text in clip.captions],
        }
        if clip.audio:
            entry["src"] = f"data:{clip.mime};base64," + base64.b64encode(clip.audio).decode()
        data[key] = entry
    return "export const VOICE = " + json.dumps(data, ensure_ascii=False) + ";\n"


__all__ = [
    "Clip",
    "NarrationLine",
    "VOICES",
    "audio_seconds",
    "estimate",
    "mp3_seconds",
    "narrate",
    "speak_configured",
    "speak_edge",
    "voice_module",
]
