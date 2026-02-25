"""릴스 프레임 이미지 렌더러 — 1분건강톡 브랜딩.

1080×1920 (9:16 세로) 프레임을 PIL로 렌더링.
투명 텍스트 오버레이 모드 지원 (GIF/영상 배경용).
"""
from __future__ import annotations

import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── 브랜드 컬러 ──────────────────────────────────────────
BRAND = {
    "blue": (43, 91, 224),        # #2B5BE0
    "red": (255, 71, 87),         # #FF4757
    "white": (255, 255, 255),
    "dark": (20, 20, 30),
    "yellow": (255, 214, 0),      # 강조 하이라이트
}

W, H = 1080, 1920  # 9:16

# ── 에셋 경로 ────────────────────────────────────────────
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "1min_health")

# ── 폰트 ─────────────────────────────────────────────────
_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_FONT_PATHS = {
    "bold": [
        os.path.expanduser("~/Library/Fonts/Pretendard-Bold.otf"),
        os.path.join(_FONT_DIR, "Pretendard-Bold.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ],
    "semibold": [
        os.path.expanduser("~/Library/Fonts/Pretendard-SemiBold.otf"),
        os.path.join(_FONT_DIR, "Pretendard-SemiBold.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ],
    "regular": [
        os.path.expanduser("~/Library/Fonts/Pretendard-Regular.otf"),
        os.path.join(_FONT_DIR, "Pretendard-Regular.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ],
}
_font_cache: dict = {}


def _load_font(role: str, size: int) -> ImageFont.FreeTypeFont:
    key = (role, size)
    if key in _font_cache:
        return _font_cache[key]
    for path in _FONT_PATHS.get(role, _FONT_PATHS["regular"]):
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[key] = font
                return font
            except Exception:
                continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ── 유틸리티 ─────────────────────────────────────────────

def _fit_cover(photo: Image.Image, w: int, h: int) -> Image.Image:
    pw, ph = photo.size
    target_ratio = w / h
    photo_ratio = pw / ph
    if photo_ratio > target_ratio:
        new_w = int(ph * target_ratio)
        left = (pw - new_w) // 2
        photo = photo.crop((left, 0, left + new_w, ph))
    else:
        new_h = int(pw / target_ratio)
        top = (ph - new_h) // 2
        photo = photo.crop((0, top, pw, top + new_h))
    return photo.resize((w, h), Image.LANCZOS)


def _open_image(source) -> Image.Image | None:
    if source is None:
        return None
    if isinstance(source, Image.Image):
        return source.convert("RGBA")
    if isinstance(source, (bytes, bytearray)):
        return Image.open(io.BytesIO(source)).convert("RGBA")
    return None


_asset_cache: dict = {}


def _load_asset(name: str) -> Image.Image | None:
    if name in _asset_cache:
        return _asset_cache[name]
    path = os.path.join(_ASSETS_DIR, name)
    if os.path.exists(path):
        try:
            img = Image.open(path).convert("RGBA")
            _asset_cache[name] = img
            return img
        except Exception:
            pass
    _asset_cache[name] = None
    return None


def _draw_gradient(draw: ImageDraw.ImageDraw, y_start: int, y_end: int,
                   color_top: tuple, color_bot: tuple, alpha_top: int = 0, alpha_bot: int = 220):
    h = y_end - y_start
    for i in range(h):
        ratio = i / max(h - 1, 1)
        r = int(color_top[0] + (color_bot[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bot[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bot[2] - color_top[2]) * ratio)
        a = int(alpha_top + (alpha_bot - alpha_top) * ratio)
        draw.line([(0, y_start + i), (W - 1, y_start + i)], fill=(r, g, b, a))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines = []
    for raw_line in text.split("\n"):
        if not raw_line.strip():
            lines.append("")
            continue
        words = list(raw_line)
        current = ""
        for ch in words:
            test = current + ch
            bbox = font.getbbox(test)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
    return lines


def _draw_rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple):
    """둥근 모서리 사각형."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


# ═════════════════════════════════════════════════════════
# ReelsRenderer
# ═════════════════════════════════════════════════════════

class ReelsRenderer:
    """1분건강톡 릴스 프레임 렌더러 (1080×1920)."""

    def __init__(self):
        self.logo_badge = _load_asset("logo_2.png")
        self.logo_full = _load_asset("logo_1.png")

    # ── 투명 텍스트 오버레이 (GIF/영상 배경용) ────────────

    def render_text_overlay(self, display_text: str, slide_type: str = "content",
                            slide_num: int | None = None, total: int | None = None) -> bytes:
        """GIF/영상 위에 합성할 투명 배경 텍스트 오버레이 PNG.

        하단에 반투명 브랜드 블루 박스 + 큰 흰색 텍스트.
        상단에 로고 배지 + 슬라이드 번호.
        """
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        if slide_type == "hook":
            # Hook: 전체 어두운 오버레이 + 중앙 큰 텍스트
            dark_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 100))
            canvas = Image.alpha_composite(canvas, dark_overlay)
            draw = ImageDraw.Draw(canvas)

            # 중앙 텍스트 박스
            font = _load_font("bold", 78)
            lines = _wrap_text(display_text, font, W - 140)
            line_h = 100
            total_h = len(lines) * line_h + 60  # 패딩 포함
            box_y = (H - total_h) // 2 - 30
            # 반투명 블루 박스
            box = Image.new("RGBA", (W - 60, total_h), (*BRAND["blue"], 200))
            canvas.paste(box, (30, box_y), box)
            draw = ImageDraw.Draw(canvas)

            y = box_y + 30
            for line in lines:
                bbox = font.getbbox(line)
                tw = bbox[2] - bbox[0]
                x = (W - tw) // 2
                # 그림자
                draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 180))
                draw.text((x, y), line, font=font, fill=BRAND["white"])
                y += line_h

        else:
            # Content: 하단 35% 반투명 블루 박스
            box_h = int(H * 0.35)
            box_y = H - box_h

            # 그라데이션 오버레이 (블루 → 투명)
            grad_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            _draw_gradient(ImageDraw.Draw(grad_overlay),
                           box_y - 120, box_y,
                           BRAND["blue"], BRAND["blue"],
                           alpha_top=0, alpha_bot=210)
            canvas = Image.alpha_composite(canvas, grad_overlay)

            # 블루 박스
            blue_box = Image.new("RGBA", (W, box_h), (*BRAND["blue"], 210))
            canvas.paste(blue_box, (0, box_y), blue_box)
            draw = ImageDraw.Draw(canvas)

            # 텍스트
            font = _load_font("bold", 62)
            lines = _wrap_text(display_text, font, W - 120)
            line_h = 82
            text_total = len(lines) * line_h
            y = box_y + (box_h - text_total) // 2 - 20
            for line in lines:
                bbox = font.getbbox(line)
                tw = bbox[2] - bbox[0]
                x = (W - tw) // 2
                draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 120))
                draw.text((x, y), line, font=font, fill=BRAND["white"])
                y += line_h

        # 로고 배지
        self._draw_badge(canvas, draw)

        # 슬라이드 번호
        if slide_num and total:
            num_font = _load_font("bold", 38)
            num_text = f"{slide_num}/{total}"
            # 반투명 배경 원
            pill_w = 90
            pill_h = 48
            pill_x = W - pill_w - 30
            pill_y = 38
            pill = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 140))
            canvas.paste(pill, (pill_x, pill_y), pill)
            draw = ImageDraw.Draw(canvas)
            bbox = num_font.getbbox(num_text)
            tw = bbox[2] - bbox[0]
            draw.text((pill_x + (pill_w - tw) // 2, pill_y + 4),
                       num_text, font=num_font, fill=BRAND["white"])

        # 하단 계정명
        self._draw_account(draw)

        return self._export_rgba(canvas)

    # ── Hook 슬라이드 (정적 배경용) ───────────────────────
    def render_hook(self, display_text: str, bg_image=None) -> bytes:
        canvas = Image.new("RGBA", (W, H), BRAND["blue"])
        bg = _open_image(bg_image)
        if bg:
            fitted = _fit_cover(bg, W, H)
            canvas.paste(fitted, (0, 0))
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            _draw_gradient(ImageDraw.Draw(overlay), 0, H,
                           (0, 0, 0), (0, 0, 0), alpha_top=80, alpha_bot=200)
            canvas = Image.alpha_composite(canvas, overlay)
        draw = ImageDraw.Draw(canvas)
        self._draw_badge(canvas, draw)
        font = _load_font("bold", 72)
        lines = _wrap_text(display_text, font, W - 120)
        line_h = 90
        total_h = len(lines) * line_h
        y = (H - total_h) // 2
        for line in lines:
            bbox = font.getbbox(line)
            tw = bbox[2] - bbox[0]
            x = (W - tw) // 2
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 150))
            draw.text((x, y), line, font=font, fill=BRAND["white"])
            y += line_h
        self._draw_account(draw)
        return self._export(canvas)

    # ── Content 슬라이드 (정적 배경용) ────────────────────
    def render_content(self, display_text: str, bg_image=None,
                       slide_num: int | None = None, total: int | None = None) -> bytes:
        canvas = Image.new("RGBA", (W, H), BRAND["blue"])
        img_h = int(H * 0.58)
        bg = _open_image(bg_image)
        if bg:
            fitted = _fit_cover(bg, W, img_h)
            canvas.paste(fitted, (0, 0))
        else:
            overlay = Image.new("RGBA", (W, img_h), (0, 0, 0, 0))
            _draw_gradient(ImageDraw.Draw(overlay), 0, img_h,
                           (60, 120, 240), BRAND["blue"],
                           alpha_top=255, alpha_bot=255)
            canvas.paste(overlay, (0, 0))
        blend_h = 120
        blend_overlay = Image.new("RGBA", (W, blend_h), (0, 0, 0, 0))
        _draw_gradient(ImageDraw.Draw(blend_overlay), 0, blend_h,
                       BRAND["blue"], BRAND["blue"],
                       alpha_top=0, alpha_bot=255)
        canvas.alpha_composite(blend_overlay, (0, img_h - blend_h))
        draw = ImageDraw.Draw(canvas)
        self._draw_badge(canvas, draw)
        if slide_num and total:
            num_font = _load_font("bold", 36)
            num_text = f"{slide_num}/{total}"
            draw.text((W - 100, 40), num_text, font=num_font, fill=BRAND["white"])
        text_y_start = img_h + 40
        font = _load_font("bold", 58)
        lines = _wrap_text(display_text, font, W - 120)
        line_h = 76
        y = text_y_start
        for line in lines:
            bbox = font.getbbox(line)
            tw = bbox[2] - bbox[0]
            x = (W - tw) // 2
            draw.text((x, y), line, font=font, fill=BRAND["white"])
            y += line_h
        self._draw_account(draw)
        return self._export(canvas)

    # ── Closing 슬라이드 ─────────────────────────────────
    def render_closing(self, display_text: str = "팔로우하고\n건강 팁 받기!") -> bytes:
        canvas = Image.new("RGBA", (W, H), BRAND["blue"])
        draw = ImageDraw.Draw(canvas)
        logo = self.logo_full
        if logo:
            logo_size = 400
            resized = logo.resize((logo_size, logo_size), Image.LANCZOS)
            x = (W - logo_size) // 2
            y = (H - logo_size) // 2 - 150
            canvas.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)
        font = _load_font("bold", 52)
        lines = display_text.split("\n")
        line_h = 70
        y = H // 2 + 120
        for line in lines:
            bbox = font.getbbox(line)
            tw = bbox[2] - bbox[0]
            x = (W - tw) // 2
            draw.text((x, y), line, font=font, fill=BRAND["white"])
            y += line_h
        self._draw_account(draw)
        return self._export(canvas)

    # ── 공통 헬퍼 ────────────────────────────────────────

    def _draw_badge(self, canvas: Image.Image, draw: ImageDraw.ImageDraw):
        badge = self.logo_badge
        if badge:
            badge_size = 72
            resized = badge.resize((badge_size, badge_size), Image.LANCZOS)
            canvas.paste(resized, (30, 40), resized if resized.mode == "RGBA" else None)

    def _draw_account(self, draw: ImageDraw.ImageDraw):
        font = _load_font("semibold", 30)
        text = "@1분건강톡"
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, H - 80), text, font=font, fill=(255, 255, 255, 180))

    def _export(self, canvas: Image.Image) -> bytes:
        """RGBA → RGB 변환 후 PNG bytes."""
        rgb = Image.new("RGB", canvas.size, BRAND["blue"])
        rgb.paste(canvas, mask=canvas.split()[3] if canvas.mode == "RGBA" else None)
        buf = io.BytesIO()
        rgb.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def _export_rgba(self, canvas: Image.Image) -> bytes:
        """RGBA 그대로 PNG bytes (투명 배경 유지)."""
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()

    # ── 배치 렌더링 ──────────────────────────────────────

    def render_all(self, slides: list[dict], bg_images: dict[str, bytes | None] = None) -> list[bytes]:
        """전체 슬라이드 렌더링 (정적 배경 모드)."""
        if bg_images is None:
            bg_images = {}
        results = []
        content_idx = 0
        total_content = sum(1 for s in slides if s["type"] == "content")
        for i, slide in enumerate(slides):
            bg = bg_images.get(f"slide_{i}")
            stype = slide["type"]
            text = slide.get("display_text", "")
            if stype == "hook":
                results.append(self.render_hook(text, bg_image=bg))
            elif stype == "content":
                content_idx += 1
                results.append(self.render_content(
                    text, bg_image=bg,
                    slide_num=content_idx, total=total_content,
                ))
            elif stype == "closing":
                results.append(self.render_closing(text))
            else:
                results.append(self.render_content(text, bg_image=bg))
        return results

    def render_overlays(self, slides: list[dict]) -> list[bytes]:
        """전체 슬라이드의 투명 텍스트 오버레이 PNG 생성 (GIF/영상 배경용)."""
        results = []
        content_idx = 0
        total_content = sum(1 for s in slides if s["type"] == "content")
        for slide in slides:
            stype = slide["type"]
            text = slide.get("display_text", "")
            if stype == "closing":
                # closing은 정적 렌더링 (로고 포함)
                results.append(self.render_closing(text))
            else:
                if stype == "content":
                    content_idx += 1
                results.append(self.render_text_overlay(
                    text, slide_type=stype,
                    slide_num=content_idx if stype == "content" else None,
                    total=total_content if stype == "content" else None,
                ))
        return results
