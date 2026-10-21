"""Narration timing and the generated voice module, without calling a TTS service."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
import wave

from deeptutor.agents.video_lesson import narration
from deeptutor.agents.video_lesson.narration import (
    Clip,
    NarrationLine,
    audio_seconds,
    estimate,
    mp3_seconds,
    voice_module,
)


def _wav(seconds: float, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\0\0" * int(seconds * rate))
    return buffer.getvalue()


def _mp3(frames: int) -> bytes:
    # MPEG-1 layer III, 128 kbit/s, 44.1 kHz: 417-byte frames of 1152 samples
    frame = bytes.fromhex("fffb9000") + b"\0" * 413
    return b"ID3\x03\0\0\0\0\0\x02xx" + frame * frames


def test_estimate_spaces_captions_by_sentence() -> None:
    clip = estimate("先看结论。再看为什么！", "zh")
    assert [text for _, text in clip.captions] == ["先看结论。", "再看为什么！"]
    assert clip.captions[0][0] == 0 < clip.captions[1][0] < clip.frames
    assert clip.audio is None


def test_audio_length_from_mp3_frames_and_wav_headers() -> None:
    assert abs(mp3_seconds(_mp3(100)) - 100 * 1152 / 44100) < 1e-9
    assert audio_seconds(_wav(1.5), "audio/wav") == 1.5


def test_configured_provider_sets_length_and_spreads_captions(monkeypatch) -> None:
    config = SimpleNamespace(provider_name="minimax", model="speech-2.8-hd", voice="v", speed=None)
    monkeypatch.setattr(narration, "_tts_config", lambda: config)

    async def fake_speech(text, *, response_format=None, strip_markdown=True):
        assert response_format == "mp3" and strip_markdown is False
        return _wav(2.0), "audio/wav"

    monkeypatch.setattr(narration, "synthesize_speech", fake_speech)
    clips = asyncio.run(narration.narrate([NarrationLine("s1", "第一句。第二句。")], "zh"))
    clip = clips["s1"]
    assert clip.frames == 60 and clip.mime == "audio/wav"
    assert clip.captions[0] == (0, "第一句。") and clip.captions[1][0] == 30


def test_narration_falls_back_to_captions_when_nothing_speaks(monkeypatch) -> None:
    monkeypatch.setattr(narration, "_tts_config", lambda: None)

    async def broken(*args, **kwargs) -> Clip:
        raise RuntimeError("offline")

    monkeypatch.setattr(narration, "speak_edge", broken)
    cache: dict = {}
    clips = asyncio.run(narration.narrate([NarrationLine("s1", "一句话。")], "zh", cache))
    assert clips["s1"].audio is None and clips["s1"].captions
    assert "一句话。" in cache


def test_edge_voice_follows_the_lesson_language() -> None:
    edge = SimpleNamespace(provider_name="edge", voice="zh-CN-YunxiNeural", speed=1.2)
    assert narration._edge_voice(edge, "zh") == ("zh-CN-YunxiNeural", "+20%")
    assert narration._edge_voice(edge, "en") == ("en-US-AvaNeural", "+20%")
    assert narration._edge_voice(None, "zh") == ("zh-CN-XiaoxiaoNeural", "+0%")


def test_voice_module_inlines_audio() -> None:
    module = voice_module(
        {"s1": Clip("a。", 30, [(0, "a。")], b"ID3"), "s2": estimate("b。", "zh")}
    )
    assert module.startswith("export const VOICE = ")
    assert '"src": "data:audio/mpeg;base64,SUQz"' in module
    assert module.count('"src"') == 1
