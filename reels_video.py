"""릴스 영상 합성 모듈 — 1분건강톡.

GIF/영상/이미지 배경 + 텍스트 오버레이 + edge-tts 나레이션.
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
import numpy as np
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
TRANSITION_DUR = 0.4
SLIDE_PADDING = 0.5

# ── temp 파일 추적 (MoviePy 렌더링 완료 후 정리) ────────
_temp_files: list[str] = []


def _track_temp(path: str) -> str:
    """temp 파일 경로를 추적 리스트에 추가."""
    _temp_files.append(path)
    return path


def _cleanup_temp_files():
    """추적된 temp 파일 일괄 정리."""
    for path in _temp_files:
        try:
            os.unlink(path)
        except Exception:
            pass
    _temp_files.clear()

# ── 음성 프리셋 ──────────────────────────────────────────
VOICES = {
    "여성 (선히)": "ko-KR-SunHiNeural",
    "남성 (현수)": "ko-KR-HyunsuMultilingualNeural",
    "남성 (인준)": "ko-KR-InJoonNeural",
}
DEFAULT_VOICE = "ko-KR-HyunsuMultilingualNeural"

# 음성별 최적 rate/pitch 설정 (자연스러운 말투)
_VOICE_PRESETS = {
    "ko-KR-SunHiNeural": {"rate": "-8%", "pitch": "+5Hz"},
    "ko-KR-HyunsuMultilingualNeural": {"rate": "-5%", "pitch": "+0Hz"},
    "ko-KR-InJoonNeural": {"rate": "-5%", "pitch": "+0Hz"},
}


# ═════════════════════════════════════════════════════════
# 나레이션 생성 (edge-tts)
# ═════════════════════════════════════════════════════════

def _preprocess_narration(text: str) -> str:
    """나레이션 텍스트 전처리 — 자연스러운 TTS를 위한 보정.

    - 이모지 제거 (TTS가 읽으면 어색)
    - ㅋㅋ 등 웃음 표현 제거
    - 마침표 뒤 쉼표 추가 (자연스러운 호흡)
    """
    import re
    # 이모지 제거
    text = re.sub(r'[\U0001F600-\U0001F9FF\U00002702-\U000027B0'
                  r'\U0001F1E0-\U0001F1FF\U00002600-\U000026FF'
                  r'\U0000FE00-\U0000FE0F\U0001FA00-\U0001FAFF]+', '', text)
    # ㅋㅋ, ㄷㄷ 등 제거
    text = re.sub(r'[ㅋㅎㄷㅠㅜ]{2,}', '', text)
    # 연속 공백 정리
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def _generate_narration_async(text: str, output_path: str,
                                     voice: str = DEFAULT_VOICE) -> str:
    text = _preprocess_narration(text)
    preset = _VOICE_PRESETS.get(voice, {})
    communicate = edge_tts.Communicate(
        text, voice,
        rate=preset.get("rate", "-5%"),
        pitch=preset.get("pitch", "+0Hz"),
    )
    await communicate.save(output_path)
    return output_path


def generate_narration(text: str, output_path: str,
                       voice: str = DEFAULT_VOICE) -> str:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
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
    if not audio_path or not os.path.exists(audio_path):
        return 3.0
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

def _fit_clip_to_reel(clip):
    """영상/이미지 클립을 1080×1920에 맞게 리사이즈+크롭."""
    cw, ch = clip.size
    target_ratio = W / H

    if cw / ch > target_ratio:
        new_h = H
        new_w = int(cw * (H / ch))
    else:
        new_w = W
        new_h = int(ch * (W / cw))

    resized = clip.resized((new_w, new_h))
    x_center = new_w / 2
    y_center = new_h / 2
    cropped = resized.cropped(
        x1=x_center - W / 2, y1=y_center - H / 2,
        x2=x_center + W / 2, y2=y_center + H / 2,
    )
    return cropped


def _letterbox_landscape(clip, bg_color=(43, 91, 224)):
    """가로 영상을 세로 프레임 안에 레터박스로 배치 (원본 비율 유지).

    가로 1920×1080 → 세로 1080×1920 안에서:
      - 영상을 가로폭 1080에 맞게 축소 (1080×607)
      - 상하 브랜드 블루 배경으로 패딩
    """
    cw, ch = clip.size

    # 가로폭 = W에 맞추고, 세로는 비율 유지
    scale = W / cw
    new_w = W
    new_h = int(ch * scale)
    resized = clip.resized((new_w, new_h))

    # 브랜드 블루 배경
    bg_arr = np.full((H, W, 3), bg_color, dtype=np.uint8)
    bg_clip = ImageClip(bg_arr).with_duration(clip.duration)

    # 세로 중앙 배치
    y_offset = (H - new_h) // 2
    final = CompositeVideoClip(
        [bg_clip, resized.with_position(("center", y_offset))],
        size=(W, H),
    ).with_duration(clip.duration)

    # 원본 오디오 유지
    if clip.audio is not None:
        final = final.with_audio(clip.audio)

    return final


def _image_bytes_to_clip(img_bytes: bytes, duration: float) -> ImageClip:
    """PNG bytes → MoviePy ImageClip (RGB)."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    arr = np.array(img)
    return ImageClip(arr).with_duration(duration)


def _overlay_png_to_clip(png_bytes: bytes, duration: float) -> ImageClip:
    """투명 PNG bytes → MoviePy ImageClip (RGBA 마스크 지원)."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    arr = np.array(img)
    # RGBA에서 RGB + mask 분리
    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3] / 255.0  # 0~1 범위
    clip = ImageClip(rgb).with_duration(duration)
    mask = ImageClip(alpha, is_mask=True).with_duration(duration)
    clip = clip.with_mask(mask)
    return clip


# ═════════════════════════════════════════════════════════
# GIF/영상 → 배경 클립 변환
# ═════════════════════════════════════════════════════════

def _media_to_bg_clip(media_bytes: bytes, media_info: dict, duration: float):
    """미디어 → 1080×1920 배경 클립.

    GIF(mp4) → 루핑 비디오
    Video → 크롭 비디오
    Image → Ken Burns 효과
    """
    media_type = media_info.get("type", "image")

    if media_type == "gif":
        return _gif_bytes_to_bg(media_bytes, media_info, duration)
    elif media_type == "video":
        return _video_bytes_to_bg(media_bytes, duration)
    else:
        return _image_bytes_to_ken_burns(media_bytes, duration)


def _gif_bytes_to_bg(gif_bytes: bytes, media_info: dict, duration: float):
    """GIF(mp4) → 루핑 배경 클립 (1080×1920).

    주의: temp 파일은 MoviePy 렌더링이 끝날 때까지 유지해야 함!
    (VideoFileClip은 프레임을 lazy하게 읽으므로 파일 삭제 시 정적 이미지가 됨)
    """
    is_mp4 = media_info.get("mp4_url", "").endswith(".mp4") or b"ftyp" in gif_bytes[:20]
    suffix = ".mp4" if is_mp4 else ".gif"

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(gif_bytes)
    tmp.close()
    tmp_path = _track_temp(tmp.name)  # 렌더링 후 정리

    try:
        clip = VideoFileClip(tmp_path)
        # 루핑
        if clip.duration < duration:
            n_loops = int(duration / clip.duration) + 1
            looped = concatenate_videoclips([clip] * n_loops)
            clip = looped.subclipped(0, duration)
        else:
            clip = clip.subclipped(0, min(clip.duration, duration))
        # 9:16 크롭
        clip = _fit_clip_to_reel(clip)
        return clip
    except Exception as e:
        logger.warning(f"GIF 클립 변환 실패: {e}")
        return _solid_color_clip(duration)


def _video_bytes_to_bg(video_bytes: bytes, duration: float):
    """영상 → 1080×1920 배경 클립."""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.write(video_bytes)
    tmp.close()
    tmp_path = _track_temp(tmp.name)  # 렌더링 후 정리

    try:
        clip = VideoFileClip(tmp_path)
        if clip.duration > duration:
            clip = clip.subclipped(0, duration)
        elif clip.duration < duration:
            n = int(duration / clip.duration) + 1
            clip = concatenate_videoclips([clip] * n).subclipped(0, duration)
        clip = _fit_clip_to_reel(clip)
        return clip
    except Exception as e:
        logger.warning(f"영상 클립 변환 실패: {e}")
        return _solid_color_clip(duration)


def _image_bytes_to_ken_burns(img_bytes: bytes, duration: float):
    """정적 이미지 → Ken Burns 효과 (줌인) 클립."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    zoom_factor = 1.15
    big_w = int(W * zoom_factor)
    big_h = int(H * zoom_factor)

    # 크게 리사이즈
    pw, ph = img.size
    target_ratio = big_w / big_h
    photo_ratio = pw / ph
    if photo_ratio > target_ratio:
        new_w = int(ph * target_ratio)
        left = (pw - new_w) // 2
        img = img.crop((left, 0, left + new_w, ph))
    else:
        new_h = int(pw / target_ratio)
        top = (ph - new_h) // 2
        img = img.crop((0, top, pw, top + new_h))
    img = img.resize((big_w, big_h), Image.LANCZOS)

    arr = np.array(img)
    base_clip = ImageClip(arr).with_duration(duration)

    def make_frame(get_frame, t):
        progress = t / max(duration, 0.01)
        current_zoom = zoom_factor - (zoom_factor - 1.0) * progress
        crop_w = int(W * current_zoom)
        crop_h = int(H * current_zoom)
        x = (big_w - crop_w) // 2
        y = (big_h - crop_h) // 2
        frame = get_frame(t)
        cropped = frame[y:y + crop_h, x:x + crop_w]
        pil = Image.fromarray(cropped).resize((W, H), Image.LANCZOS)
        return np.array(pil)

    return base_clip.transform(make_frame)


def _solid_color_clip(duration: float, color=(43, 91, 224)):
    """단색 배경 클립 (폴백용)."""
    arr = np.full((H, W, 3), color, dtype=np.uint8)
    return ImageClip(arr).with_duration(duration)


# ═════════════════════════════════════════════════════════
# 스와이프 전환
# ═════════════════════════════════════════════════════════

def _swipe_transition(clip1, clip2, trans_dur: float = TRANSITION_DUR):
    w = W

    def pos1(t):
        progress = t / trans_dur
        return (-w * progress, 0)

    def pos2(t):
        progress = t / trans_dur
        return (w - w * progress, 0)

    c1 = clip1.with_duration(trans_dur).with_position(pos1)
    c2 = clip2.with_duration(trans_dur).with_position(pos2)
    return CompositeVideoClip([c1, c2], size=(W, H)).with_duration(trans_dur)


# ═════════════════════════════════════════════════════════
# 메인 합성 (GIF/영상 배경 + 텍스트 오버레이)
# ═════════════════════════════════════════════════════════

def compose_reel(
    slide_bg_clips: list,
    overlay_images: list[bytes | None],
    narration_paths: list[str],
    output_path: str,
    include_intro: bool = True,
    include_bumper: bool = True,
    progress_callback=None,
) -> str:
    """릴스 영상 합성.

    Args:
        slide_bg_clips: 배경 클립 리스트 (VideoClip/ImageClip)
        overlay_images: 텍스트 오버레이 PNG bytes 리스트 (None이면 오버레이 없음)
        narration_paths: 나레이션 MP3 경로 리스트
        output_path: 출력 MP4 경로
    """

    def _progress(step, total, msg):
        if progress_callback:
            progress_callback(step, total, msg)
        logger.info(f"[{step}/{total}] {msg}")

    total_steps = len(slide_bg_clips) + 3
    current_step = 0

    composed_slides = []
    slide_audios = []
    cumulative_time = 0.0

    # INTRO
    if include_intro and os.path.exists(INTRO_PATH):
        current_step += 1
        _progress(current_step, total_steps, "인트로 영상 로드 중...")
        intro_clip = VideoFileClip(INTRO_PATH)
        iw, ih = intro_clip.size
        if iw > ih:
            # 가로 영상 → 레터박스 (원본 애니메이션 보존)
            intro_clip = _letterbox_landscape(intro_clip)
            logger.info(f"INTRO 레터박스 처리: {iw}×{ih} → {W}×{H}")
        elif (iw, ih) != (W, H):
            intro_clip = _fit_clip_to_reel(intro_clip)
        composed_slides.append(intro_clip)
        cumulative_time += intro_clip.duration
    else:
        current_step += 1

    # 슬라이드: 배경 + 오버레이 합성
    for i, bg_clip in enumerate(slide_bg_clips):
        current_step += 1
        _progress(current_step, total_steps, f"슬라이드 {i + 1}/{len(slide_bg_clips)} 합성 중...")

        narr_path = narration_paths[i] if i < len(narration_paths) else ""
        narr_dur = get_audio_duration(narr_path)
        slide_dur = narr_dur + SLIDE_PADDING

        # 배경 duration 설정
        if hasattr(bg_clip, 'duration') and bg_clip.duration and bg_clip.duration >= slide_dur:
            bg_sized = bg_clip.subclipped(0, slide_dur)
        else:
            bg_sized = bg_clip.with_duration(slide_dur)

        # 텍스트 오버레이 합성
        overlay_data = overlay_images[i] if i < len(overlay_images) else None
        if overlay_data:
            overlay_clip = _overlay_png_to_clip(overlay_data, slide_dur)
            final_slide = CompositeVideoClip(
                [bg_sized, overlay_clip],
                size=(W, H)
            ).with_duration(slide_dur)
        else:
            final_slide = bg_sized

        composed_slides.append(final_slide)

        if narr_path and os.path.exists(narr_path):
            audio = AudioFileClip(narr_path)
            slide_audios.append((audio, cumulative_time))

        cumulative_time += slide_dur

    # BUMPER
    if include_bumper and os.path.exists(BUMPER_PATH):
        current_step += 1
        _progress(current_step, total_steps, "범퍼 영상 로드 중...")
        bumper_clip = VideoFileClip(BUMPER_PATH)
        bw, bh = bumper_clip.size
        if (bw, bh) != (W, H):
            if bw > bh:
                bumper_clip = _letterbox_landscape(bumper_clip)
            else:
                bumper_clip = _fit_clip_to_reel(bumper_clip)
        composed_slides.append(bumper_clip)
    else:
        current_step += 1

    # 클립 연결 (스와이프)
    if len(composed_slides) == 0:
        raise ValueError("합성할 슬라이드가 없습니다.")

    if len(composed_slides) == 1:
        final_video = composed_slides[0]
    else:
        segments = []
        for i, clip in enumerate(composed_slides):
            if i == 0:
                segments.append(clip)
            else:
                prev = composed_slides[i - 1]
                trans = _swipe_transition(prev, clip, TRANSITION_DUR)
                segments.append(trans)
                remaining_dur = clip.duration - TRANSITION_DUR
                if remaining_dur > 0:
                    remaining = clip.subclipped(TRANSITION_DUR)
                    segments.append(remaining)
        final_video = concatenate_videoclips(segments, method="compose")

    # 오디오 합성
    if slide_audios:
        audio_clips = [audio.with_start(start) for audio, start in slide_audios]
        combined_audio = CompositeAudioClip(audio_clips)
        if final_video.audio is not None:
            combined_audio = CompositeAudioClip([final_video.audio, combined_audio])
        final_video = final_video.with_audio(combined_audio)

    # 내보내기
    current_step = total_steps
    _progress(current_step, total_steps, "MP4 내보내기 중...")

    final_video.write_videofile(
        output_path, fps=FPS, codec="libx264", audio_codec="aac",
        threads=4, preset="medium", logger=None,
    )

    # 정리
    final_video.close()
    for clip in composed_slides:
        try:
            clip.close()
        except Exception:
            pass
    for audio, _ in slide_audios:
        try:
            audio.close()
        except Exception:
            pass

    # GIF/영상 temp 파일 정리 (렌더링 완료 후에만!)
    _cleanup_temp_files()

    logger.info(f"릴스 영상 생성 완료: {output_path}")
    return output_path


# ═════════════════════════════════════════════════════════
# 통합 파이프라인 (GIF/영상 배경)
# ═════════════════════════════════════════════════════════

def create_reel(
    slides: list[dict],
    media_data: list[tuple[bytes | None, dict | None]],
    overlay_images: list[bytes],
    output_dir: str | None = None,
    voice: str = DEFAULT_VOICE,
    include_intro: bool = True,
    include_bumper: bool = True,
    progress_callback=None,
) -> dict:
    """릴스 생성 통합 파이프라인 (GIF/영상 배경).

    Args:
        slides: 스크립트 슬라이드 리스트
        media_data: [(bytes, metadata), ...] 슬라이드별 미디어 데이터
        overlay_images: 텍스트 오버레이 PNG bytes
        output_dir: 출력 디렉토리
        voice: TTS 음성 ID
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="reel_")
    os.makedirs(output_dir, exist_ok=True)

    # Phase 1: 나레이션
    if progress_callback:
        progress_callback(0.0, "나레이션 생성 중...")
    narr_dir = os.path.join(output_dir, "narrations")
    os.makedirs(narr_dir, exist_ok=True)
    narration_paths = generate_narrations(slides, narr_dir, voice)

    # Phase 2: 배경 클립 생성
    if progress_callback:
        progress_callback(0.25, "배경 클립 준비 중...")
    bg_clips = []
    for i, (m_bytes, m_info) in enumerate(media_data):
        narr_path = narration_paths[i] if i < len(narration_paths) else ""
        narr_dur = get_audio_duration(narr_path)
        slide_dur = narr_dur + SLIDE_PADDING

        if m_bytes and m_info:
            try:
                clip = _media_to_bg_clip(m_bytes, m_info, slide_dur)
                bg_clips.append(clip)
                logger.info(f"배경 클립: slide_{i} ({m_info['type']}/{m_info.get('source', '?')})")
                continue
            except Exception as e:
                logger.warning(f"배경 클립 실패 slide_{i}: {e}")
        bg_clips.append(_solid_color_clip(slide_dur))

    # Phase 3: 영상 합성
    if progress_callback:
        progress_callback(0.40, "영상 합성 중...")
    video_path = os.path.join(output_dir, "reel.mp4")
    compose_reel(
        slide_bg_clips=bg_clips,
        overlay_images=overlay_images,
        narration_paths=narration_paths,
        output_path=video_path,
        include_intro=include_intro,
        include_bumper=include_bumper,
        progress_callback=lambda s, t, m: (
            progress_callback(0.40 + (s / t) * 0.50, m) if progress_callback else None
        ),
    )

    # Phase 4: 결과
    if progress_callback:
        progress_callback(0.95, "마무리 중...")

    video_bytes = Path(video_path).read_bytes()
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


# ═════════════════════════════════════════════════════════
# 레거시 호환 (정적 프레임 이미지)
# ═════════════════════════════════════════════════════════

def create_reel_legacy(
    slides: list[dict],
    frame_images: list[bytes],
    output_dir: str | None = None,
    voice: str = DEFAULT_VOICE,
    include_intro: bool = True,
    include_bumper: bool = True,
    progress_callback=None,
) -> dict:
    """레거시: 정적 프레임 이미지 기반 릴스 생성."""
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="reel_")
    os.makedirs(output_dir, exist_ok=True)

    if progress_callback:
        progress_callback(0.0, "나레이션 생성 중...")
    narr_dir = os.path.join(output_dir, "narrations")
    os.makedirs(narr_dir, exist_ok=True)
    narration_paths = generate_narrations(slides, narr_dir, voice)

    if progress_callback:
        progress_callback(0.33, "영상 합성 중...")

    bg_clips = []
    for i, img_bytes in enumerate(frame_images):
        narr_path = narration_paths[i] if i < len(narration_paths) else ""
        narr_dur = get_audio_duration(narr_path)
        slide_dur = narr_dur + SLIDE_PADDING
        bg_clips.append(_image_bytes_to_clip(img_bytes, slide_dur))

    video_path = os.path.join(output_dir, "reel.mp4")
    compose_reel(
        slide_bg_clips=bg_clips,
        overlay_images=[None] * len(frame_images),
        narration_paths=narration_paths,
        output_path=video_path,
        include_intro=include_intro,
        include_bumper=include_bumper,
        progress_callback=lambda s, t, m: (
            progress_callback(0.33 + (s / t) * 0.62, m) if progress_callback else None
        ),
    )

    if progress_callback:
        progress_callback(0.95, "마무리 중...")

    video_bytes = Path(video_path).read_bytes()
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
