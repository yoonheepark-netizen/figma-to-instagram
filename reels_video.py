"""릴스 영상 합성 모듈 — 1분건강톡.

참고 영상 스타일: 상단 55% GIF/이미지 + 하단 45% 브랜드 블루 텍스트.
GIF는 꽉 채우지 않고 상단 영역에 배치. 오프닝 없이 본론부터 시작.
나레이션(edge-tts) 기반 동적 씬 구성 → BUMPER.mov 연결.
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

from sfx import generate_sfx

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
MEDIA_RATIO = 0.55  # GIF/이미지가 차지하는 상단 비율 (참고 영상 기준)
MEDIA_H = int(H * MEDIA_RATIO)  # ~1056px
BRAND_BLUE = (43, 91, 224)

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
# GIF/영상 → 상단 55% 씬 클립 (참고 영상 스타일)
# ═════════════════════════════════════════════════════════

def _fit_to_area(clip, target_w: int, target_h: int):
    """클립을 target_w × target_h 영역에 맞게 리사이즈+크롭."""
    cw, ch = clip.size
    target_ratio = target_w / target_h

    if cw / ch > target_ratio:
        new_h = target_h
        new_w = int(cw * (target_h / ch))
    else:
        new_w = target_w
        new_h = int(ch * (target_w / cw))

    resized = clip.resized((new_w, new_h))
    x_center = new_w / 2
    y_center = new_h / 2
    return resized.cropped(
        x1=x_center - target_w / 2, y1=y_center - target_h / 2,
        x2=x_center + target_w / 2, y2=y_center + target_h / 2,
    )


def _load_video_clip(media_bytes: bytes, media_info: dict, duration: float):
    """미디어 bytes → 루핑 VideoFileClip (temp 파일 추적).

    주의: temp 파일은 MoviePy 렌더링이 끝날 때까지 유지!
    """
    is_mp4 = media_info.get("mp4_url", "").endswith(".mp4") or b"ftyp" in media_bytes[:20]
    suffix = ".mp4" if is_mp4 else ".gif"

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(media_bytes)
    tmp.close()
    _track_temp(tmp.name)

    clip = VideoFileClip(tmp.name)
    # 루핑 (최대 3회 — 메모리 절약)
    if clip.duration < duration:
        n_loops = min(int(duration / clip.duration) + 1, 3)
        clip = concatenate_videoclips([clip] * n_loops).subclipped(0, duration)
    else:
        clip = clip.subclipped(0, min(clip.duration, duration))
    return clip


def _media_to_scene_clip(media_bytes: bytes, media_info: dict,
                          overlay_png: bytes | None, duration: float):
    """미디어 + 오버레이 → 1분건강톡 스타일 씬 클립.

    레이아웃 (참고 영상 기준):
    ┌─────────────────┐
    │ [🔴톡]   n/N    │  ← 오버레이 (로고, 번호)
    │                 │
    │  GIF/이미지     │  ← 상단 55% (MEDIA_H px)
    │  (원본비율크롭)  │
    │                 │
    ├─ 그라데이션 ────┤
    │  ■ 블루 ■■■■■  │  ← 하단 45% (브랜드 블루)
    │  display_text   │  ← 오버레이 텍스트
    │  @1분건강톡     │
    └─────────────────┘
    """
    media_type = media_info.get("type", "image") if media_info else "none"

    # 1. 브랜드 블루 배경 (전체)
    bg_arr = np.full((H, W, 3), BRAND_BLUE, dtype=np.uint8)
    bg_clip = ImageClip(bg_arr).with_duration(duration)

    layers = [bg_clip]

    # 2. 미디어 클립 → 상단 MEDIA_H 영역에 배치
    media_clip = None
    try:
        if media_type in ("gif", "video") and media_bytes:
            raw_clip = _load_video_clip(media_bytes, media_info, duration)
            media_clip = _fit_to_area(raw_clip, W, MEDIA_H)
        elif media_type == "image" and media_bytes:
            img = Image.open(io.BytesIO(media_bytes)).convert("RGB")
            img = _fit_cover_pil(img, W, MEDIA_H)
            media_clip = ImageClip(np.array(img)).with_duration(duration)
    except Exception as e:
        logger.warning(f"미디어 클립 생성 실패: {e}")

    if media_clip is not None:
        layers.append(media_clip.with_position((0, 0)))

    # 3. 텍스트 오버레이
    if overlay_png:
        overlay_clip = _overlay_png_to_clip(overlay_png, duration)
        layers.append(overlay_clip)

    scene = CompositeVideoClip(layers, size=(W, H)).with_duration(duration)
    return scene


def _fit_cover_pil(img: Image.Image, w: int, h: int) -> Image.Image:
    """PIL 이미지를 w×h에 맞게 커버 크롭."""
    pw, ph = img.size
    target_ratio = w / h
    if pw / ph > target_ratio:
        new_w = int(ph * target_ratio)
        left = (pw - new_w) // 2
        img = img.crop((left, 0, left + new_w, ph))
    else:
        new_h = int(pw / target_ratio)
        top = (ph - new_h) // 2
        img = img.crop((0, top, pw, top + new_h))
    return img.resize((w, h), Image.LANCZOS)


def _solid_color_clip(duration: float, color=None):
    """단색 배경 클립 (폴백용)."""
    c = color or BRAND_BLUE
    arr = np.full((H, W, 3), c, dtype=np.uint8)
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
    scene_clips: list,
    narration_paths: list[str],
    output_path: str,
    include_intro: bool = False,
    include_bumper: bool = True,
    progress_callback=None,
) -> str:
    """릴스 영상 합성.

    Args:
        scene_clips: 완성된 씬 클립 리스트 (GIF+블루바+텍스트 합성 완료)
        narration_paths: 나레이션 MP3 경로 리스트
        output_path: 출력 MP4 경로
        include_intro: 인트로 포함 여부 (기본: False — 본론부터 시작)
    """

    def _progress(step, total, msg):
        if progress_callback:
            progress_callback(step, total, msg)
        logger.info(f"[{step}/{total}] {msg}")

    total_steps = len(scene_clips) + 3
    current_step = 0

    composed_slides = []
    slide_audios = []
    cumulative_time = 0.0

    # INTRO (기본 비활성 — 본론부터 시작)
    if include_intro and os.path.exists(INTRO_PATH):
        current_step += 1
        _progress(current_step, total_steps, "인트로 영상 로드 중...")
        intro_clip = VideoFileClip(INTRO_PATH)
        iw, ih = intro_clip.size
        if iw > ih:
            intro_clip = _letterbox_landscape(intro_clip)
        elif (iw, ih) != (W, H):
            intro_clip = _fit_clip_to_reel(intro_clip)
        composed_slides.append(intro_clip)
        cumulative_time += intro_clip.duration
    else:
        current_step += 1

    # 씬 클립 배치 + 나레이션 동기화
    for i, scene_clip in enumerate(scene_clips):
        current_step += 1
        _progress(current_step, total_steps, f"씬 {i + 1}/{len(scene_clips)} 배치 중...")

        narr_path = narration_paths[i] if i < len(narration_paths) else ""
        narr_dur = get_audio_duration(narr_path)
        slide_dur = narr_dur + SLIDE_PADDING

        # 씬 duration 조정
        if hasattr(scene_clip, 'duration') and scene_clip.duration and scene_clip.duration >= slide_dur:
            final_scene = scene_clip.subclipped(0, slide_dur)
        else:
            final_scene = scene_clip.with_duration(slide_dur)

        composed_slides.append(final_scene)

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

    # 클립 연결 (단순 연결 — 메모리 절약)
    if len(composed_slides) == 0:
        raise ValueError("합성할 슬라이드가 없습니다.")

    if len(composed_slides) == 1:
        final_video = composed_slides[0]
    else:
        final_video = concatenate_videoclips(composed_slides, method="chain")

    # 오디오 합성 (나레이션만 — SFX 생략으로 메모리 절약)
    if slide_audios:
        all_audio_parts = [audio.with_start(start) for audio, start in slide_audios]
        combined_audio = CompositeAudioClip(all_audio_parts)
        if final_video.audio is not None:
            combined_audio = CompositeAudioClip([final_video.audio, combined_audio])
        final_video = final_video.with_audio(combined_audio)

    # 내보내기 (메모리 절약: threads=1, ultrafast)
    current_step = total_steps
    _progress(current_step, total_steps, "MP4 내보내기 중...")

    final_video.write_videofile(
        output_path, fps=FPS, codec="libx264", audio_codec="aac",
        threads=1, preset="ultrafast", logger=None,
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

    # 메모리 해제
    import gc
    gc.collect()

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
    include_intro: bool = False,
    include_bumper: bool = True,
    progress_callback=None,
) -> dict:
    """릴스 생성 통합 파이프라인 (GIF 상단 55% + 블루바 하단 45%).

    Args:
        slides: 스크립트 슬라이드 리스트
        media_data: [(bytes, metadata), ...] 슬라이드별 미디어 데이터
        overlay_images: 텍스트 오버레이 PNG bytes
        output_dir: 출력 디렉토리
        voice: TTS 음성 ID
        include_intro: 인트로 포함 여부 (기본 False — 본론부터 시작)
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

    # Phase 2: 씬 클립 생성 (GIF 상단 55% + 블루바 하단 45% + 오버레이)
    if progress_callback:
        progress_callback(0.25, "씬 클립 생성 중...")
    scene_clips = []
    for i, (m_bytes, m_info) in enumerate(media_data):
        narr_path = narration_paths[i] if i < len(narration_paths) else ""
        narr_dur = get_audio_duration(narr_path)
        slide_dur = narr_dur + SLIDE_PADDING
        overlay = overlay_images[i] if i < len(overlay_images) else None

        try:
            scene = _media_to_scene_clip(m_bytes, m_info, overlay, slide_dur)
            scene_clips.append(scene)
            src = f"{m_info['type']}/{m_info.get('source', '?')}" if m_info else "브랜드 배경"
            logger.info(f"씬 클립: slide_{i} ({src})")
        except Exception as e:
            logger.warning(f"씬 클립 실패 slide_{i}: {e}")
            # 폴백: 오버레이만 있으면 블루 배경 + 오버레이
            scene = _media_to_scene_clip(None, None, overlay, slide_dur)
            scene_clips.append(scene)

        if progress_callback:
            progress_callback(0.25 + (i / len(media_data)) * 0.15,
                              f"씬 {i + 1}/{len(media_data)} 생성 완료")

    # Phase 3: 영상 합성
    if progress_callback:
        progress_callback(0.40, "영상 합성 중...")
    video_path = os.path.join(output_dir, "reel.mp4")
    compose_reel(
        scene_clips=scene_clips,
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
    include_intro: bool = False,
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

    scene_clips = []
    for i, img_bytes in enumerate(frame_images):
        narr_path = narration_paths[i] if i < len(narration_paths) else ""
        narr_dur = get_audio_duration(narr_path)
        slide_dur = narr_dur + SLIDE_PADDING
        scene_clips.append(_image_bytes_to_clip(img_bytes, slide_dur))

    video_path = os.path.join(output_dir, "reel.mp4")
    compose_reel(
        scene_clips=scene_clips,
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
