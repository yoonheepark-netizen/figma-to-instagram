"""카드뉴스 이미지 생성기 (Pillow 기반) — Premium Design v2."""
import io
import math
import os

from PIL import Image, ImageDraw, ImageFont

# ── 템플릿 정의 ───────────────────────────────────────────

TEMPLATES = {
    "깔끔한 화이트": {
        "bg": "#F5F5F7",
        "card_bg": "#FFFFFF",
        "title_color": "#1A1A1A",
        "body_color": "#444444",
        "accent": "#4A90D9",
        "accent2": "#2E6AB8",
        "muted": "#AAAAAA",
        "cover_bg1": "#4A90D9",
        "cover_bg2": "#2E6AB8",
        "cover_text": "#FFFFFF",
        "cover_sub": "#D0E4FF",
        "closing_bg1": "#4A90D9",
        "closing_bg2": "#2E6AB8",
        "closing_text": "#FFFFFF",
        "number_bg": "#4A90D9",
        "number_text": "#FFFFFF",
        "bullet": "#4A90D9",
        "divider": "#E8E8E8",
    },
    "다크 프리미엄": {
        "bg": "#0F0F1A",
        "card_bg": "#1A1A2E",
        "title_color": "#FFFFFF",
        "body_color": "#CCCCDD",
        "accent": "#E94560",
        "accent2": "#C62A42",
        "muted": "#555570",
        "cover_bg1": "#16213E",
        "cover_bg2": "#0F0F1A",
        "cover_text": "#FFFFFF",
        "cover_sub": "#A0B4D0",
        "closing_bg1": "#E94560",
        "closing_bg2": "#C62A42",
        "closing_text": "#FFFFFF",
        "number_bg": "#E94560",
        "number_text": "#FFFFFF",
        "bullet": "#E94560",
        "divider": "#2A2A40",
    },
    "수壽 브랜드": {
        "bg": "#F7F0E8",
        "card_bg": "#FFFFFF",
        "title_color": "#2D1810",
        "body_color": "#4A3728",
        "accent": "#C4956A",
        "accent2": "#A67B52",
        "muted": "#B0A090",
        "cover_bg1": "#2D1810",
        "cover_bg2": "#1A0E08",
        "cover_text": "#FFF8F0",
        "cover_sub": "#D4B896",
        "closing_bg1": "#C4956A",
        "closing_bg2": "#A67B52",
        "closing_text": "#FFFFFF",
        "number_bg": "#C4956A",
        "number_text": "#FFFFFF",
        "bullet": "#C4956A",
        "divider": "#E8DDD0",
    },
    "건강 그린": {
        "bg": "#ECF5F0",
        "card_bg": "#FFFFFF",
        "title_color": "#1B4332",
        "body_color": "#2D6A4F",
        "accent": "#40916C",
        "accent2": "#2D6A4F",
        "muted": "#88B0A0",
        "cover_bg1": "#1B4332",
        "cover_bg2": "#0D2818",
        "cover_text": "#FFFFFF",
        "cover_sub": "#95D5B2",
        "closing_bg1": "#40916C",
        "closing_bg2": "#2D6A4F",
        "closing_text": "#FFFFFF",
        "number_bg": "#40916C",
        "number_text": "#FFFFFF",
        "bullet": "#40916C",
        "divider": "#D0E8DD",
    },
}

# ── 폰트 로딩 ─────────────────────────────────────────────

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

_FONT_PATHS = {
    "bold": [
        os.path.expanduser("~/Library/Fonts/Pretendard-Bold.otf"),
        os.path.join(_FONT_DIR, "Pretendard-Bold.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ],
    "semibold": [
        os.path.expanduser("~/Library/Fonts/Pretendard-SemiBold.otf"),
        os.path.join(_FONT_DIR, "Pretendard-SemiBold.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ],
    "regular": [
        os.path.expanduser("~/Library/Fonts/Pretendard-Regular.otf"),
        os.path.join(_FONT_DIR, "Pretendard-Regular.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ],
    "medium": [
        os.path.expanduser("~/Library/Fonts/Pretendard-Medium.otf"),
        os.path.join(_FONT_DIR, "Pretendard-Medium.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ],
    "serif": [
        os.path.expanduser("~/Library/Fonts/MaruBuri-SemiBold.ttf"),
        os.path.join(_FONT_DIR, "MaruBuri-SemiBold.ttf"),
        os.path.expanduser("~/Library/Fonts/Pretendard-SemiBold.otf"),
    ],
}

_font_cache = {}


def _load_font(role, size):
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


# ── 유틸리티 ──────────────────────────────────────────────


def _hex(color):
    """Hex 컬러를 RGB 튜플로 변환."""
    c = color.lstrip("#")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def _hex_alpha(color, alpha):
    """Hex 컬러를 RGBA 튜플로 변환."""
    c = color.lstrip("#")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def _lerp_color(c1_hex, c2_hex, t):
    """두 색상 사이 보간. t=0이면 c1, t=1이면 c2."""
    r1, g1, b1 = _hex(c1_hex)
    r2, g2, b2 = _hex(c2_hex)
    return (
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    )


def _draw_gradient(img, color1_hex, color2_hex, direction="vertical"):
    """이미지에 그라디언트 배경 그리기."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    if direction == "vertical":
        for y in range(h):
            t = y / max(h - 1, 1)
            color = _lerp_color(color1_hex, color2_hex, t)
            draw.line([(0, y), (w, y)], fill=color)
    else:
        for x in range(w):
            t = x / max(w - 1, 1)
            color = _lerp_color(color1_hex, color2_hex, t)
            draw.line([(x, 0), (x, h)], fill=color)


def _draw_rounded_rect(draw, xy, fill, radius=20):
    """둥근 모서리 사각형 그리기."""
    x1, y1, x2, y2 = xy
    # 4개 원 + 중앙 사각형
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill)


def _draw_circle(draw, center, radius, fill):
    """원 그리기."""
    cx, cy = center
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=fill,
    )


# ── 렌더러 ────────────────────────────────────────────────


class CardNewsRenderer:
    """Pillow 기반 카드뉴스 이미지 생성기 — Premium Design."""

    def __init__(self, template_name, size=(1080, 1080)):
        if template_name not in TEMPLATES:
            raise ValueError(f"알 수 없는 템플릿: {template_name}")
        self.t = TEMPLATES[template_name]
        self.w, self.h = size
        self.pad = 80

    def _new_image(self, bg_hex):
        return Image.new("RGB", (self.w, self.h), _hex(bg_hex))

    def _new_gradient_image(self, color1_hex, color2_hex):
        img = Image.new("RGB", (self.w, self.h))
        _draw_gradient(img, color1_hex, color2_hex)
        return img

    def _wrap_text(self, draw, text, font, max_width):
        """텍스트를 max_width에 맞게 줄바꿈 (한글 글자 단위 지원)."""
        lines = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                lines.append("")
                continue
            # 한글은 단어 경계가 없으므로 글자 단위로도 줄바꿈
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            current = words[0]
            for word in words[1:]:
                test = current + " " + word
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    current = test
                else:
                    # 현재 단어가 너무 길면 글자 단위로 자르기
                    lines.append(current)
                    current = word
            lines.append(current)
        return lines

    def _text_height(self, draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    def _text_width(self, draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _to_bytes(self, img):
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # ── 장식 요소 ──

    def _draw_decorative_circles(self, img, color_hex, alpha=30):
        """배경에 장식용 반투명 원 그리기."""
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        r, g, b = _hex(color_hex)

        # 큰 원 (우상단)
        _draw_circle(od, (self.w + 60, -60), 280, (r, g, b, alpha))
        # 작은 원 (좌하단)
        _draw_circle(od, (-40, self.h + 40), 200, (r, g, b, alpha))
        # 중간 원 (우하단)
        _draw_circle(od, (self.w - 100, self.h - 120), 140, (r, g, b, int(alpha * 0.6)))

        img_rgba = img.convert("RGBA")
        img_rgba = Image.alpha_composite(img_rgba, overlay)
        return img_rgba.convert("RGB")

    def _draw_dot_pattern(self, img, color_hex, alpha=15, spacing=60):
        """배경에 도트 패턴 그리기."""
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        r, g, b = _hex(color_hex)

        for x in range(spacing // 2, self.w, spacing):
            for y in range(spacing // 2, self.h, spacing):
                _draw_circle(od, (x, y), 2, (r, g, b, alpha))

        img_rgba = img.convert("RGBA")
        img_rgba = Image.alpha_composite(img_rgba, overlay)
        return img_rgba.convert("RGB")

    # ── 표지 슬라이드 ──

    def render_cover(self, title, subtitle=""):
        img = self._new_gradient_image(self.t["cover_bg1"], self.t["cover_bg2"])

        # 장식 원
        img = self._draw_decorative_circles(img, self.t["cover_sub"], alpha=25)

        draw = ImageDraw.Draw(img)
        max_w = self.w - self.pad * 2

        # 상단 장식 라인
        line_y = 80
        line_w = 60
        draw.rectangle(
            [(self.w // 2 - line_w // 2, line_y),
             (self.w // 2 + line_w // 2, line_y + 4)],
            fill=_hex(self.t["cover_sub"]),
        )

        # 제목
        font_title = _load_font("bold", 68)
        title_lines = self._wrap_text(draw, title, font_title, max_w - 40)

        # 부제
        font_sub = _load_font("regular", 32)
        sub_lines = (
            self._wrap_text(draw, subtitle, font_sub, max_w - 40) if subtitle else []
        )

        # 높이 계산
        line_spacing_title = 16
        title_h = sum(
            self._text_height(draw, ln, font_title) for ln in title_lines
        ) + max(0, len(title_lines) - 1) * line_spacing_title

        gap = 60 if sub_lines else 0
        divider_h = 30 if sub_lines else 0

        sub_h = 0
        if sub_lines:
            sub_h = sum(
                self._text_height(draw, ln, font_sub) for ln in sub_lines
            ) + max(0, len(sub_lines) - 1) * 8

        total_h = title_h + gap + divider_h + sub_h
        y = (self.h - total_h) // 2

        # 제목 배경 하이라이트 (반투명 영역)
        highlight_pad = 30
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(
            [self.pad - highlight_pad,
             y - highlight_pad,
             self.w - self.pad + highlight_pad,
             y + title_h + highlight_pad],
            radius=16,
            fill=(0, 0, 0, 25),
        )
        img_rgba = img.convert("RGBA")
        img_rgba = Image.alpha_composite(img_rgba, overlay)
        img = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        # 제목 그리기
        for ln in title_lines:
            lw = self._text_width(draw, ln, font_title)
            lh = self._text_height(draw, ln, font_title)
            draw.text(
                ((self.w - lw) // 2, y),
                ln,
                font=font_title,
                fill=_hex(self.t["cover_text"]),
            )
            y += lh + line_spacing_title

        if sub_lines:
            y += 16

            # 장식 구분선 (다이아몬드 + 선)
            cx = self.w // 2
            line_half = 80
            diamond_size = 5

            # 왼쪽 선
            draw.line(
                [(cx - line_half, y + diamond_size), (cx - diamond_size - 4, y + diamond_size)],
                fill=_hex(self.t["cover_sub"]),
                width=1,
            )
            # 다이아몬드
            draw.polygon(
                [
                    (cx, y),
                    (cx + diamond_size, y + diamond_size),
                    (cx, y + diamond_size * 2),
                    (cx - diamond_size, y + diamond_size),
                ],
                fill=_hex(self.t["cover_sub"]),
            )
            # 오른쪽 선
            draw.line(
                [(cx + diamond_size + 4, y + diamond_size), (cx + line_half, y + diamond_size)],
                fill=_hex(self.t["cover_sub"]),
                width=1,
            )

            y += diamond_size * 2 + 24

            # 부제 그리기
            for ln in sub_lines:
                lw = self._text_width(draw, ln, font_sub)
                lh = self._text_height(draw, ln, font_sub)
                draw.text(
                    ((self.w - lw) // 2, y),
                    ln,
                    font=font_sub,
                    fill=_hex(self.t["cover_sub"]),
                )
                y += lh + 8

        # 하단 장식 라인
        bottom_y = self.h - 80
        draw.rectangle(
            [(self.w // 2 - line_w // 2, bottom_y),
             (self.w // 2 + line_w // 2, bottom_y + 4)],
            fill=_hex(self.t["cover_sub"]),
        )

        return self._to_bytes(img)

    # ── 본문 슬라이드 ──

    def render_content(self, heading, body, slide_num=None, total_slides=None):
        img = self._new_image(self.t["bg"])
        draw = ImageDraw.Draw(img)

        card_margin = 50
        card_x1 = card_margin
        card_y1 = card_margin
        card_x2 = self.w - card_margin
        card_y2 = self.h - card_margin
        card_radius = 24

        # 카드 그림자 (오프셋된 어두운 사각형)
        shadow_offset = 6
        shadow_color = _lerp_color(self.t["bg"], "#000000", 0.08)
        draw.rounded_rectangle(
            [card_x1 + shadow_offset, card_y1 + shadow_offset,
             card_x2 + shadow_offset, card_y2 + shadow_offset],
            radius=card_radius,
            fill=shadow_color,
        )

        # 카드 배경
        draw.rounded_rectangle(
            [card_x1, card_y1, card_x2, card_y2],
            radius=card_radius,
            fill=_hex(self.t["card_bg"]),
        )

        # 상단 액센트 바 (카드 내부 상단, 둥근 모서리에 맞춤)
        accent_bar_h = 6
        # 둥근 상단에 맞추기 위해 clip 영역 사용
        for ay in range(card_y1, card_y1 + accent_bar_h):
            draw.line(
                [(card_x1 + card_radius, ay), (card_x2 - card_radius, ay)],
                fill=_hex(self.t["accent"]),
            )
        # 상단 좌우 곡선 부분의 accent bar
        draw.rounded_rectangle(
            [card_x1, card_y1, card_x2, card_y1 + accent_bar_h + card_radius],
            radius=card_radius,
            fill=_hex(self.t["accent"]),
        )
        # 아래 부분을 카드 색으로 덮기
        draw.rectangle(
            [card_x1, card_y1 + accent_bar_h, card_x2, card_y1 + accent_bar_h + card_radius + 2],
            fill=_hex(self.t["card_bg"]),
        )

        inner_pad = 50
        content_x = card_x1 + inner_pad
        content_max_w = (card_x2 - card_x1) - inner_pad * 2

        y = card_y1 + accent_bar_h + 40

        # 넘버 뱃지
        if slide_num is not None:
            badge_radius = 24
            badge_cx = content_x + badge_radius
            badge_cy = y + badge_radius
            _draw_circle(draw, (badge_cx, badge_cy), badge_radius, _hex(self.t["number_bg"]))
            font_num = _load_font("bold", 26)
            num_text = str(slide_num)
            nw = self._text_width(draw, num_text, font_num)
            nh = self._text_height(draw, num_text, font_num)
            draw.text(
                (badge_cx - nw // 2, badge_cy - nh // 2),
                num_text,
                font=font_num,
                fill=_hex(self.t["number_text"]),
            )

            # 넘버 뱃지 옆에 소제목
            heading_x = badge_cx + badge_radius + 16
            heading_max_w = content_max_w - (badge_radius * 2 + 16)
        else:
            heading_x = content_x
            heading_max_w = content_max_w

        # 소제목
        font_heading = _load_font("bold", 40)
        heading_lines = self._wrap_text(draw, heading, font_heading, heading_max_w)

        heading_y = y
        if slide_num is not None:
            # 소제목을 넘버 뱃지 중앙에 맞춤
            first_lh = self._text_height(draw, heading_lines[0] if heading_lines else "가", font_heading)
            heading_y = badge_cy - first_lh // 2

        for ln in heading_lines:
            lh = self._text_height(draw, ln, font_heading)
            draw.text(
                (heading_x, heading_y),
                ln,
                font=font_heading,
                fill=_hex(self.t["title_color"]),
            )
            heading_y += lh + 10

        y = max(heading_y, y + (badge_radius * 2 if slide_num else 0)) + 24

        # 구분선
        draw.line(
            [(content_x, y), (content_x + content_max_w, y)],
            fill=_hex(self.t["divider"]),
            width=1,
        )
        y += 24

        # 본문
        font_body = _load_font("regular", 28)
        body_text = body.strip()
        body_paragraphs = body_text.split("\n")

        bullet_r = 4
        bullet_indent = 20
        text_after_bullet = bullet_indent + 16

        for para in body_paragraphs:
            para = para.strip()
            if not para:
                y += 14
                continue

            # 불렛 포인트 감지 (-, •, ·, 숫자.)
            is_bullet = False
            display_text = para
            if para.startswith(("-", "•", "·")):
                is_bullet = True
                display_text = para[1:].strip()
            elif len(para) > 2 and para[0].isdigit() and para[1] in (".", ")"):
                is_bullet = True
                display_text = para

            wrapped = self._wrap_text(draw, display_text, font_body, content_max_w - text_after_bullet if is_bullet else content_max_w)

            for j, ln in enumerate(wrapped):
                lh = self._text_height(draw, ln, font_body)
                if y + lh > card_y2 - inner_pad - 40:
                    draw.text(
                        (content_x + (text_after_bullet if is_bullet else 0), y),
                        "...",
                        font=font_body,
                        fill=_hex(self.t["muted"]),
                    )
                    y = card_y2  # 강제 종료
                    break

                if is_bullet and j == 0:
                    # 불렛 원
                    _draw_circle(
                        draw,
                        (content_x + bullet_indent, y + lh // 2),
                        bullet_r,
                        _hex(self.t["bullet"]),
                    )
                draw.text(
                    (content_x + (text_after_bullet if is_bullet else 0), y),
                    ln,
                    font=font_body,
                    fill=_hex(self.t["body_color"]),
                )
                y += lh + 10
            if y >= card_y2:
                break

        # 페이지 번호 (하단 우측)
        if slide_num and total_slides:
            font_page = _load_font("medium", 22)
            page_text = f"{slide_num} / {total_slides}"
            pw = self._text_width(draw, page_text, font_page)
            draw.text(
                (card_x2 - inner_pad - pw, card_y2 - inner_pad),
                page_text,
                font=font_page,
                fill=_hex(self.t["muted"]),
            )

        return self._to_bytes(img)

    # ── 마무리 슬라이드 ──

    def render_closing(self, cta_text, account_name=""):
        img = self._new_gradient_image(self.t["closing_bg1"], self.t["closing_bg2"])

        # 장식 원
        img = self._draw_decorative_circles(img, "#FFFFFF", alpha=20)
        # 도트 패턴
        img = self._draw_dot_pattern(img, "#FFFFFF", alpha=10, spacing=50)

        draw = ImageDraw.Draw(img)
        max_w = self.w - self.pad * 2

        font_cta = _load_font("serif", 46)
        font_acc = _load_font("medium", 28)

        cta_lines = self._wrap_text(draw, cta_text, font_cta, max_w - 40)

        cta_h = sum(
            self._text_height(draw, ln, font_cta) for ln in cta_lines
        ) + max(0, len(cta_lines) - 1) * 14

        acc_h = 0
        if account_name:
            acc_h = self._text_height(draw, account_name, font_acc)

        # 큰따옴표 아이콘 높이
        quote_h = 60

        gap = 60 if account_name else 0
        total_h = quote_h + 30 + cta_h + gap + acc_h
        y = (self.h - total_h) // 2

        # 큰따옴표 장식
        font_quote = _load_font("serif", 100)
        quote_char = "\u201C"  # "
        qw = self._text_width(draw, quote_char, font_quote)

        # 반투명 따옴표
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        r, g, b = _hex(self.t["closing_text"])
        od.text(
            ((self.w - qw) // 2, y - 20),
            quote_char,
            font=font_quote,
            fill=(r, g, b, 60),
        )
        img_rgba = img.convert("RGBA")
        img_rgba = Image.alpha_composite(img_rgba, overlay)
        img = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)

        y += quote_h + 30

        # CTA 텍스트
        for ln in cta_lines:
            lw = self._text_width(draw, ln, font_cta)
            lh = self._text_height(draw, ln, font_cta)
            draw.text(
                ((self.w - lw) // 2, y),
                ln,
                font=font_cta,
                fill=_hex(self.t["closing_text"]),
            )
            y += lh + 14

        # 구분선 + 계정명
        if account_name:
            y += 16

            # 장식 구분선 (다이아몬드)
            cx = self.w // 2
            diamond_size = 4
            line_half = 60
            ct = _hex(self.t["closing_text"])

            draw.line(
                [(cx - line_half, y + diamond_size), (cx - diamond_size - 4, y + diamond_size)],
                fill=ct,
                width=1,
            )
            draw.polygon(
                [
                    (cx, y),
                    (cx + diamond_size, y + diamond_size),
                    (cx, y + diamond_size * 2),
                    (cx - diamond_size, y + diamond_size),
                ],
                fill=ct,
            )
            draw.line(
                [(cx + diamond_size + 4, y + diamond_size), (cx + line_half, y + diamond_size)],
                fill=ct,
                width=1,
            )

            y += diamond_size * 2 + 20

            aw = self._text_width(draw, account_name, font_acc)
            draw.text(
                ((self.w - aw) // 2, y),
                account_name,
                font=font_acc,
                fill=_hex(self.t["closing_text"]),
            )

        return self._to_bytes(img)

    # ── 전체 렌더 ──

    def render_all(self, slides_data):
        """모든 슬라이드를 렌더링하여 PNG bytes 리스트로 반환.

        slides_data 예시:
        [
            {"type": "cover", "title": "...", "subtitle": "..."},
            {"type": "content", "heading": "...", "body": "..."},
            {"type": "closing", "cta_text": "...", "account_name": "..."},
        ]
        """
        results = []
        content_idx = 0
        content_total = sum(1 for s in slides_data if s["type"] == "content")

        for slide in slides_data:
            stype = slide["type"]
            if stype == "cover":
                results.append(
                    self.render_cover(slide.get("title", ""), slide.get("subtitle", ""))
                )
            elif stype == "content":
                content_idx += 1
                results.append(
                    self.render_content(
                        slide.get("heading", ""),
                        slide.get("body", ""),
                        slide_num=content_idx,
                        total_slides=content_total,
                    )
                )
            elif stype == "closing":
                results.append(
                    self.render_closing(
                        slide.get("cta_text", ""),
                        slide.get("account_name", ""),
                    )
                )
        return results
