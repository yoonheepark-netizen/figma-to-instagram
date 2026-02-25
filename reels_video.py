"""릴스 영상 합성 모듈 — 1분건강톡.

edge-tts 나레이션 + MoviePy 영상 합성.
INTRO.mp4 → 슬라이드(스와이프 전환) → BUMPER.mov
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path

import edge_tts
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
    CompositeAudioClip,
)
from PIL import Image

logger = logging.getLogger(__name__)

# ── 경로 ─────────────────────────────────────────────────
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "1min_health")
INTRO_PATH = os.path.join(_ASSETS_DIR, "INTRO.mp4")
BUMPER_PATH = os.path.join(_ASSETS_DIR, "BUMPER.mov")

# ── 상수 ─────────────────────────────────────────────────
W, H = 1080, 1920  # 9:16
FPS = 30
TRANSITION_DUR = 0.4  # 스와이프 전환 시간(초)
SLIDE_PADDING = 0.5   # 나레이션 후 여유 시간(초)

# ── 음성 프리셋 ──────────────────────────────────────────
VOICES = {
    "여성 (선히)": "ko-KR-SunHiNeural",
    "남성 (인준)": "ko-KR-InJoonNeural",
}
DEFAULT_VOICE = "ko-KR-SunHiNeural"


# ═════════════════════════════════════════════════════════
# 나레이션 생성 (edge-tts)
# ═════════════════════════════════════════════════════════

async def _generate_narration_async(text: str, output_path: str,
                                     voice: str = DEFAULT_VOICE) -> str:
    """edge-tts로 한국어 나레이션 MP3 생성."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path


def generate_narration(text: str, output_path: str,
                       voice: str = DEFAULT_VOICE) -> str:
    """동기 래퍼: 나레이션 MP3 생성 후 경로 반환."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Streamlit 등 이미 이벤트 루프가 돌고 있는 경우
            import nest_asyncio
            nest_asyncio.apply()
            loop.run_until_complete(
                _generate_narration_async(text, output_path, voice))
        else:
            loop.run_until_complete(
                _generate_narration_async(text, output_path, voice))
    except RuntimeError:
        asyncio.run(_generate_narration_async(text, output_path, voice))
    return output_path


def generate_narrations(slides: list[dict], tmp_dir: str,
                        voice: str = DEFAULT_VOICE) -> list[str]:
    """전체 슬라이드 나레이션 일괄 생성. 경로 리스트 반환."""
    paths = []
    for i, slide in enumerate(slides):
        narration = slide.get("narration", "")
        if not narration.strip():
            paths.append("")
            continue
        out = os.path.join(tmp_dir, f"narration_{i}.mp3")
        try:
            generate_narration(narration, out, voice)
            paths.append(out)
            logger.info(f"나레이션 생성 완료: slide_{i} → {out}")
        except Exception as e:
            logger.error(f"나레이션 생성 실패 (slide_{i}): {e}")
            paths.append("")
    return paths


# ═════════════════════════════════════════════════════════
# 오디오 유틸
# ═════════════════════════════════════════════════════════

def get_audio_duration(audio_path: str) -> float:
    """오디오 파일 길이(초) 반환."""
    if not audio_path or not os.path.exists(audio_path):
        return 3.0  # 나레이션 없으면 기본 3초
    try:
        clip = AudioFileClip(audio_path)
        dur = clip.duration
        clip.close()
        return dur
    except Exception:
        return 3.0


# ═════════════════════════════════════════════════════════
# 클립 유틸
# ═════════════════════════════════════════════════════════

def _fit_clip_to_reel(clip: VideoFileClip) -> VideoFileClip:
    """영상 클립을 1080×1920에 맞게 리사이즈+크롭."""
    cw, ch = clip.size
    target_ratio = W / H  # 0.5625

    if cw / ch > target_ratio:
        # 가로가 더 넓음 → 높이에 맞추고 좌우 크롭
        new_h = H
        new_w = int(cw * (H / ch))
    else:
        # 세로가 더 길음 → 너비에 맞추고 상하 크롭
        new_w = W
        new_h = int(ch * (W / cw))

    resized = clip.resized((new_w, new_h))
    # 중앙 크롭
    x_center = new_w / 2
    y_center = new_h / 2
    cropped = resized.cropped(
        x1=x_center - W / 2, y1=y_center - H / 2,
        x2=x_center + W / 2, y2=y_center + H / 2,
    )
    return cropped


def _image_bytes_to_clip(img_bytes: bytes, duration: float) -> ImageClip:
    """PNG bytes → MoviePy ImageClip."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    # PIL → numpy array
    import numpy as np
    arr = np.array(img)
    clip = ImageClip(arr).with_duration(duration)
    return clip


# ═════════════════════════════════════════════════════════
# 스와이프 전환
# ═════════════════════════════════════════════════════════

def _swipe_transition(clip1, clip2, trans_dur: float = TRANSITION_DUR):
    """좌→우 스와이프 전환 클립 생성 (trans_dur 초)."""
    w = W

    def pos1(t):
        """clip1: 왼쪽으로 밀려나감."""
        progress = t / trans_dur
        return (-w * progress, 0)

    def pos2(t):
        """clip2: 오른쪽에서 들어옴."""
        progress = t / trans_dur
        return (w - w * progress, 0)

    c1 = clip1.with_duration(trans_dur).with_position(pos1)
    c2 = clip2.with_duration(trans_dur).with_position(pos2)

    return CompositeVideoClip([c1, c2], size=(W, H)).with_duration(trans_dur)


# ═════════════════════════════════════════════════════════
# 메인 합성
# ═════════════════════════════════════════════════════════

def compose_reel(
    frame_images: list[bytes],
    narration_paths: list[str],
    output_path: str,
    include_intro: bool = True,
    include_bumper: bool = True,
    progress_callback=None,
) -> str:
    """릴스 영상 합성.

    Args:
        frame_images: 슬라이드별 PNG bytes 리스트
        narration_paths: 슬라이드별 나레이션 MP3 경로 리스트 (""이면 나레이션 없음)
        output_path: 출력 MP4 경로
        include_intro: INTRO.mp4 포함 여부
        include_bumper: BUMPER.mov 포함 여부
        progress_callback: (step, total, message) 콜백

    Returns: 출력 파일 경로
    """

    def _progress(step, total, msg):
        if progress_callback:
            progress_callback(step, total, msg)
        logger.info(f"[{step}/{total}] {msg}")

    total_steps = len(frame_images) + 3  # slides + intro + bumper + export
    current_step = 0

    # ── 1) 슬라이드 클립 생성 ────────────────────────────
    slide_clips = []
    slide_audios = []  # (audio_clip, start_time) 튜플

    cumulative_time = 0.0

    # INTRO
    if include_intro and os.path.exists(INTRO_PATH):
        current_step += 1
        _progress(current_step, total_steps, "인트로 영상 로드 중...")
        intro_clip = VideoFileClip(INTRO_PATH)
        intro_clip = _fit_clip_to_reel(intro_clip)
        slide_clips.append(intro_clip)
        cumulative_time += intro_clip.duration
    else:
        current_step += 1

    # 슬라이드
    for i, img_bytes in enumerate(frame_images):
        current_step += 1
        _progress(current_step, total_steps, f"슬라이드 {i + 1}/{len(frame_images)} 합성 중...")

        # 나레이션 길이 기반 duration 결정
        narr_path = narration_paths[i] if i < len(narration_paths) else ""
        narr_dur = get_audio_duration(narr_path)
        slide_dur = narr_dur + SLIDE_PADDING

        # 이미지 → 클립
        img_clip = _image_bytes_to_clip(img_bytes, slide_dur)
        slide_clips.append(img_clip)

        # 나레이션 오디오 트랙
        if narr_path and os.path.exists(narr_path):
            audio = AudioFileClip(narr_path)
            slide_audios.append((audio, cumulative_time))

        cumulative_time += slide_dur

    # BUMPER
    if include_bumper and os.path.exists(BUMPER_PATH):
        current_step += 1
        _progress(current_step, total_steps, "범퍼 영상 로드 중...")
        bumper_clip = VideoFileClip(BUMPER_PATH)
        if bumper_clip.size != [W, H]:
            bumper_clip = _fit_clip_to_reel(bumper_clip)
        slide_clips.append(bumper_clip)
    else:
        current_step += 1

    # ── 2) 클립 연결 (스와이프 전환) ─────────────────────
    if len(slide_clips) == 0:
        raise ValueError("합성할 슬라이드가 없습니다.")

    if len(slide_clips) == 1:
        final_video = slide_clips[0]
    else:
        # 전환 효과 적용: 각 클립 사이에 스와이프
        segments = []
        for i, clip in enumerate(slide_clips):
            if i == 0:
                # 첫 클립: 전환 없이 전체 재생
                segments.append(clip)
            else:
                # 이전 클립과 현재 클립 사이에 스와이프 전환
                # 이전 클립 끝부분 잘라서 전환 소스로 사용
                prev = slide_clips[i - 1]
                trans = _swipe_transition(prev, clip, TRANSITION_DUR)
                segments.append(trans)
                # 현재 클립에서 전환 시간 제외한 나머지
                remaining_dur = clip.duration - TRANSITION_DUR
                if remaining_dur > 0:
                    remaining = clip.subclipped(TRANSITION_DUR)
                    segments.append(remaining)

        final_video = concatenate_videoclips(segments, method="compose")

    # ── 3) 오디오 합성 ───────────────────────────────────
    if slide_audios:
        # 각 나레이션을 올바른 시작 시간에 배치
        audio_clips = []
        for audio, start in slide_audios:
            audio_clips.append(audio.with_start(start))

        combined_audio = CompositeAudioClip(audio_clips)

        # 원본 비디오 오디오(INTRO/BUMPER)와 합성
        if final_video.audio is not None:
            combined_audio = CompositeAudioClip([final_video.audio, combined_audio])

        final_video = final_video.with_audio(combined_audio)

    # ── 4) 내보내기 ──────────────────────────────────────
    current_step = total_steps
    _progress(current_step, total_steps, "MP4 내보내기 중...")

    final_video.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        preset="medium",
        logger=None,  # MoviePy 내부 로그 비활성화
    )

    # 리소스 정리
    final_video.close()
    for clip in slide_clips:
        try:
            clip.close()
        except Exception:
            pass
    for audio, _ in slide_audios:
        try:
            audio.close()
        except Exception:
            pass

    logger.info(f"릴스 영상 생성 완료: {output_path}")
    return output_path


# ═════════════════════════════════════════════════════════
# 통합 파이프라인
# ═════════════════════════════════════════════════════════

def create_reel(
    slides: list[dict],
    frame_images: list[bytes],
    output_dir: str | None = None,
    voice: str = DEFAULT_VOICE,
    include_intro: bool = True,
    include_bumper: bool = True,
    progress_callback=None,
) -> dict:
    """릴스 생성 통합 파이프라인.

    Args:
        slides: 스크립트 슬라이드 리스트 [{type, narration, display_text, ...}]
        frame_images: 렌더링된 프레임 PNG bytes 리스트
        output_dir: 출력 디렉토리 (None이면 임시 디렉토리)
        voice: TTS 음성 ID
        include_intro: 인트로 포함 여부
        include_bumper: 범퍼 포함 여부
        progress_callback: (step, total, message) 콜백

    Returns: {
        "video_path": str,
        "video_bytes": bytes,
        "narration_paths": list[str],
        "duration": float,
    }
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="reel_")
    os.makedirs(output_dir, exist_ok=True)

    total_phases = 3
    phase = 0

    def _phase_progress(step, total, msg):
        if progress_callback:
            overall = (phase / total_phases) + (step / total) * (1 / total_phases)
            progress_callback(overall, msg)

    # Phase 1: 나레이션 생성
    phase = 0
    if progress_callback:
        progress_callback(0.0, "나레이션 생성 중...")
    narr_dir = os.path.join(output_dir, "narrations")
    os.makedirs(narr_dir, exist_ok=True)
    narration_paths = generate_narrations(slides, narr_dir, voice)

    # Phase 2: 영상 합성
    phase = 1
    if progress_callback:
        progress_callback(0.33, "영상 합성 중...")
    video_path = os.path.join(output_dir, "reel.mp4")
    compose_reel(
        frame_images=frame_images,
        narration_paths=narration_paths,
        output_path=video_path,
        include_intro=include_intro,
        include_bumper=include_bumper,
        progress_callback=lambda s, t, m: _phase_progress(s, t, m),
    )

    # Phase 3: 결과 수집
    phase = 2
    if progress_callback:
        progress_callback(0.95, "마무리 중...")

    video_bytes = Path(video_path).read_bytes()

    # 영상 길이 확인
    try:
        vc = VideoFileClip(video_path)
        duration = vc.duration
        vc.close()
    except Exception:
        duration = 0.0

    if progress_callback:
        progress_callback(1.0, "완료!")

    return {
        "video_path": video_path,
        "video_bytes": video_bytes,
        "narration_paths": narration_paths,
        "duration": duration,
    }
