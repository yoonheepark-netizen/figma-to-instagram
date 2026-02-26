"""효과음(SFX) 생성 모듈 — 1분건강톡 릴스.

외부 에셋 없이 numpy로 간단한 효과음 합성.
- whoosh: 스와이프 전환
- pop: 텍스트 등장
- ding: 강조/포인트
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import wave

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100


def _write_wav(samples: np.ndarray, path: str) -> str:
    """float32 samples → WAV 파일."""
    # -1~1 범위 클리핑 + int16 변환
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    return path


def generate_whoosh(duration: float = 0.35, path: str | None = None) -> str:
    """스와이프 전환 효과음 (whoosh/swish).

    주파수가 올라갔다 내려가는 노이즈 필터.
    """
    if path is None:
        path = tempfile.mktemp(suffix=".wav", prefix="sfx_whoosh_")
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, dtype=np.float32)

    # 화이트 노이즈 기반
    noise = np.random.randn(n).astype(np.float32) * 0.3

    # 주파수 sweep (200Hz → 2000Hz → 200Hz)
    freq = 200 + 1800 * np.sin(np.pi * t / duration)
    sweep = np.sin(2 * np.pi * freq * t) * 0.15

    # 엔벨로프 (가운데 최대)
    envelope = np.sin(np.pi * t / duration) ** 2

    samples = (noise + sweep) * envelope * 0.4
    return _write_wav(samples, path)


def generate_pop(duration: float = 0.15, path: str | None = None) -> str:
    """텍스트 등장 팝 효과음."""
    if path is None:
        path = tempfile.mktemp(suffix=".wav", prefix="sfx_pop_")
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, dtype=np.float32)

    # 감쇠하는 사인파 (800Hz → 400Hz)
    freq = 800 - 400 * t / duration
    wave_data = np.sin(2 * np.pi * freq * t)

    # 빠른 감쇠 엔벨로프
    envelope = np.exp(-t * 20)

    samples = wave_data * envelope * 0.5
    return _write_wav(samples, path)


def generate_ding(duration: float = 0.5, path: str | None = None) -> str:
    """강조 포인트 딩 효과음."""
    if path is None:
        path = tempfile.mktemp(suffix=".wav", prefix="sfx_ding_")
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, dtype=np.float32)

    # 맑은 벨 소리 (2000Hz + 3000Hz 하모닉)
    wave1 = np.sin(2 * np.pi * 2000 * t) * 0.4
    wave2 = np.sin(2 * np.pi * 3000 * t) * 0.2

    # 감쇠 엔벨로프
    envelope = np.exp(-t * 5)

    samples = (wave1 + wave2) * envelope * 0.4
    return _write_wav(samples, path)


# SFX 타입 → 생성 함수 매핑
SFX_GENERATORS = {
    "whoosh": generate_whoosh,
    "pop": generate_pop,
    "ding": generate_ding,
}


def generate_sfx(sfx_type: str, path: str | None = None) -> str | None:
    """SFX 타입으로 효과음 파일 생성."""
    gen = SFX_GENERATORS.get(sfx_type)
    if gen is None:
        logger.warning(f"알 수 없는 SFX 타입: {sfx_type}")
        return None
    try:
        return gen(path=path)
    except Exception as e:
        logger.error(f"SFX 생성 실패 ({sfx_type}): {e}")
        return None
