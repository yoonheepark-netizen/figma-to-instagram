"""카드뉴스 이미지 생성기 — v7.3 피그마 100% 정밀 레이아웃.

Figma 2025 원본 좌표 기반 (1080x1350):
  - 뱃지: x=24, y=24, 86x86
  - 텍스트: x=68, y=937 (커버/콘텐츠 동일 시작점)
  - 워터마크: center x, y=1268 (하단 40px)
  - 그라디언트: y=482→1350 (h=868), 2겹

제품 이미지: content에서만 사용 (네이티브 콘텐츠 느낌).
Unsplash: 영어 키워드 자동 변환 후 검색.
"""
import io
import os
import random
import urllib.request

from PIL import Image, ImageDraw, ImageFont

# ── 브랜드 컬러 (Figma 2025 원본) ────────────────────────

BRAND = {
    "dark_red": (102, 16, 16),       # #661010
    "gold": (201, 177, 123),         # #C9B17B
    "gradient_dark": (14, 19, 19),   # #0E1313
    "white": (255, 255, 255),
}

# ── Figma 정확한 레이아웃 수치 (1080x1350 기준) ──────────

LAYOUT = {
    "badge_x": 24, "badge_y": 24, "badge_size": 86,
    "text_x": 68,
    "text_y": 937,            # 커버/콘텐츠 텍스트 시작 y
    "cover_text_w": 932,      # 커버 타이틀 너비
    "content_text_w": 942,    # 콘텐츠 본문 너비
    "watermark_y": 1268,      # 워터마크 y (하단 40px)
    "gradient_y": 482,        # 그라디언트 시작 y
    "gradient_h": 868,        # 그라디언트 높이
}

# ── Figma 텍스트 스타일 정의 ─────────────────────────────

TEXT_STYLES = {
    "cover_title": {
        "font": "bold", "size": 90,
        "letter_spacing": -3.6, "line_height_px": 99,
    },
    "content_heading": {
        "font": "bold", "size": 62,
        "letter_spacing": -2.48, "line_height_px": 74,
    },
    "content_body": {
        "font": "semibold", "size": 42,
        "letter_spacing": -1.68, "line_height_px": 56,
    },
    "checklist_title": {
        "font": "bold", "size": 62,
        "letter_spacing": -1.24, "line_height_px": 80.6,
    },
    "checklist_item": {
        "font": "semibold", "size": 48,
        "letter_spacing": -1.44, "line_height_px": 96,
    },
    "watermark": {
        "font": "didot", "size": 32,
        "letter_spacing": 0, "line_height_px": 41.6,
    },
}

# ── 폰트 ──────────────────────────────────────────────────

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_FONT_PATHS = {
    "bold": [
        os.path.expanduser("~/Library/Fonts/Pretendard-Bold.otf"),
        os.path.join(_FONT_DIR, "Pretendard-Bold.otf"),
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        # Linux (Streamlit Cloud) — fonts-noto-cjk 패키지
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
    "didot": [
        os.path.join(_FONT_DIR, "GFSDidot-Regular.ttf"),
        os.path.expanduser("~/Library/Fonts/GFSDidot.ttf"),
        "/System/Library/Fonts/Supplemental/Didot.ttc",
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
                logger.info(f"폰트 로딩 성공: {role}/{size} → {path}")
                return font
            except Exception as e:
                logger.warning(f"폰트 로딩 실패: {path} → {e}")
                continue
        else:
            logger.debug(f"폰트 없음: {path}")
    logger.error(f"폰트 폴백: {role}/{size} → 기본 폰트 (텍스트가 매우 작게 표시됩니다)")
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# ── 유틸리티 ──────────────────────────────────────────────

def _fit_cover(photo, w, h):
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


def _open_image(source):
    if source is None:
        return None
    if isinstance(source, Image.Image):
        return source.convert("RGBA")
    if isinstance(source, (bytes, bytearray)):
        return Image.open(io.BytesIO(source)).convert("RGBA")
    return None


# ── 에셋 ─────────────────────────────────────────────────

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
_asset_cache = {}


def _load_asset(name):
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


# ── 제품 키워드 → 배경 이미지 매핑 ─────────────────────────

PRODUCT_IMAGES = {
    "경옥고": [
        "products/bg_gyeongokgo_1.png",
        "products/bg_gyeongokgo_2.png",
        "products/bg_gyeongokgo_3.png",
        "products/bg_gyeongokgo_4.png",
        "products/bg_gyeongokgo_5.png",
        "products/bg_gyeongokgo_6.png",
    ],
    "공진단": [
        "products/bg_gongjindan_1.png",
    ],
    "우황청심원": [],
    "녹용": [],
    "녹용한약": [],
}


def _find_product_bg(text):
    """텍스트에서 제품 키워드를 감지하고 매칭 배경 이미지 반환."""
    if not text:
        return None
    for keyword, images in PRODUCT_IMAGES.items():
        if keyword in text and images:
            path = random.choice(images)
            return _load_asset(path)
    return None


# ── Unsplash 배경 이미지 ─────────────────────────────────

_KEYWORD_TO_ENGLISH = {
    # 건강/질병
    "면역력": "warm sunlight cozy nature winter morning",
    "면역": "warm sunlight cozy nature winter",
    "감기": "winter scarf snowy street warm",
    "독감": "winter snow frost morning cold",
    "혈압": "calm ocean sunrise peaceful nature",
    "혈관": "morning walk nature forest trail",
    "체온": "warm blanket cozy fireplace winter",
    "당뇨": "healthy green salad nature fresh",
    "관절": "morning stretching exercise park nature",
    "소화": "fresh herbal tea warm cozy",
    "알레르기": "spring flower blooming garden nature",
    "두통": "calm peaceful lake mountain nature",
    # 라이프스타일
    "수면": "peaceful bedroom soft light night",
    "운동": "morning jog nature park sunrise",
    "피로": "relaxation nature calm peaceful lake",
    "스트레스": "meditation calm forest zen nature",
    "다이어트": "fresh salad bright kitchen healthy",
    "식습관": "fresh vegetables bright kitchen food",
    "노화": "elderly couple walking park nature",
    # 자연/계절
    "겨울": "snowy landscape winter cozy warm",
    "봄": "spring cherry blossom pink garden",
    "여름": "blue ocean beach sunny clear",
    "가을": "autumn leaves golden forest warm",
    "환절기": "autumn leaves golden light nature",
    "일교차": "winter morning frost cold sunrise",
    "미세먼지": "clear blue sky mountains fresh",
    "건조": "warm humidifier cozy room winter",
    "호흡기": "fresh mountain air forest nature",
    # 영양/식품
    "비타민": "fresh citrus orange lemon fruit",
    "한방": "zen garden calm nature peaceful",
    "보약": "warm tea cup cozy winter",
    "건강": "winter cozy warm morning light",
    "약선": "traditional food warm wooden table",
    "홍삼": "warm tea cozy autumn morning",
    "인삼": "natural herb garden green morning",
    "차": "warm tea cup cozy rainy window",
}


def _extract_search_query(text):
    """한글 텍스트에서 Unsplash 검색용 영문 키워드 추출. 긴 키워드 우선 매칭."""
    if not text:
        return "nature wellness cozy"
    # 긴 키워드 먼저 매칭 (더 구체적인 키워드 우선)
    sorted_kw = sorted(_KEYWORD_TO_ENGLISH.items(), key=lambda x: len(x[0]), reverse=True)
    for kor, eng in sorted_kw:
        if kor in text:
            return eng
    return "nature wellness cozy morning"


_FALLBACK_QUERIES = [
    "nature landscape morning light",
    "cozy warm winter peaceful",
    "forest mountain sunrise calm",
]


def _fetch_unsplash_bg(query):
    """Unsplash에서 영어 키워드로 배경 이미지 검색. 실패 시 대체 쿼리 재시도."""
    api_key = os.getenv("UNSPLASH_ACCESS_KEY", "")
    if not api_key:
        return None
    import json
    queries = [query] + _FALLBACK_QUERIES if query else _FALLBACK_QUERIES
    for q in queries:
        if not q:
            continue
        try:
            url = (
                f"https://api.unsplash.com/search/photos"
                f"?query={urllib.request.quote(q)}"
                f"&per_page=5&orientation=portrait&content_filter=high"
            )
            req = urllib.request.Request(url, headers={
                "Authorization": f"Client-ID {api_key}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            results = data.get("results", [])
            if not results:
                continue
            top = results[:min(3, len(results))]
            photo = random.choice(top)
            img_url = photo["urls"]["regular"]
            with urllib.request.urlopen(img_url, timeout=15) as img_resp:
                img_data = img_resp.read()
            return Image.open(io.BytesIO(img_data)).convert("RGBA")
        except Exception:
            continue
    return None


# ── 호환성 TEMPLATES dict ────────────────────────────────

TEMPLATES = {
    "수壽 브랜드": {
        "overlay_color": (14, 19, 19),
        "overlay_alpha": 0.85,
        "text_white": True,
        "card_bg": "#661010",
        "accent": "#661010",
        "heading_text": "#FFFFFF",
        "body_text": "#FFFFFF",
    },
}


# ── 렌더러 ────────────────────────────────────────────────

class CardNewsRenderer:
    """Figma 2025 원본 스타일 카드뉴스 생성기 (1080x1350)."""

    def __init__(self, template_name="수壽 브랜드", size=(1080, 1350)):
        self.w, self.h = size
        self.s = self.w / 1080

    # ── 자간(letter-spacing) 적용 텍스트 유틸 ──

    def _char_w(self, draw, ch, font):
        b = draw.textbbox((0, 0), ch, font=font)
        return b[2] - b[0]

    def _text_w_ls(self, draw, text, font, ls):
        if not text:
            return 0
        total = 0
        for i, ch in enumerate(text):
            total += self._char_w(draw, ch, font)
            if i < len(text) - 1:
                total += ls
        return total

    def _draw_text_ls(self, draw, x, y, text, font, fill, ls):
        if ls == 0:
            draw.text((x, y), text, font=font, fill=fill)
            return
        cur_x = x
        for ch in text:
            draw.text((cur_x, y), ch, font=font, fill=fill)
            cur_x += self._char_w(draw, ch, font) + ls

    def _wrap_ls(self, draw, text, font, max_w, ls):
        lines = []
        for para in text.split("\n"):
            if not para.strip():
                lines.append("")
                continue
            words = para.split()
            if not words:
                lines.append("")
                continue
            cur = words[0]
            for w in words[1:]:
                test = cur + " " + w
                if self._text_w_ls(draw, test, font, ls) <= max_w:
                    cur = test
                else:
                    lines.append(cur)
                    cur = w
            if self._text_w_ls(draw, cur, font, ls) > max_w:
                lines.extend(self._wrap_chars_ls(draw, cur, font, max_w, ls))
            else:
                lines.append(cur)
        return lines

    def _wrap_chars_ls(self, draw, text, font, max_w, ls):
        lines, cur = [], ""
        for ch in text:
            test = cur + ch
            if self._text_w_ls(draw, test, font, ls) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines

    def _to_bytes(self, img):
        if img.mode == "RGBA":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # ── 공통 요소 (Figma 정확한 좌표) ──

    def _draw_gradient(self, img):
        """Figma 원본 그라디언트 2겹: y=482→1350."""
        s = self.s
        grad_y = int(LAYOUT["gradient_y"] * s)
        grad_h = int(LAYOUT["gradient_h"] * s)
        r, g, b = BRAND["gradient_dark"]
        overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)
        for y in range(grad_y, min(grad_y + grad_h, self.h)):
            progress = (y - grad_y) / grad_h
            alpha = int(255 * progress)
            draw_ov.line([(0, y), (self.w, y)], fill=(r, g, b, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay)
        img = Image.alpha_composite(img, overlay)  # 2겹
        return img

    def _place_badge(self, img):
        """뱃지: x=24, y=24, 86x86."""
        badge = _load_asset("logo_badge.png")
        if not badge:
            return img
        s = self.s
        size = int(LAYOUT["badge_size"] * s)
        badge_r = badge.resize((size, size), Image.LANCZOS)
        x = int(LAYOUT["badge_x"] * s)
        y = int(LAYOUT["badge_y"] * s)
        img_rgba = img.convert("RGBA") if img.mode != "RGBA" else img
        img_rgba.paste(badge_r, (x, y), badge_r)
        return img_rgba

    def _draw_watermark(self, img):
        """워터마크: GFS Didot 400, 32pt, center, y=1268.
        3x 고해상도 렌더링 후 다운스케일로 안티앨리어싱 적용.
        Figma 스펙: w=133, h=42(line-height box), bottom_margin=40px."""
        s = self.s
        st = TEXT_STYLES["watermark"]
        text = "thesoo.co"
        # 3x 고해상도로 렌더링
        _sc = 3
        font_big = _load_font(st["font"], int(st["size"] * s * _sc))
        tmp = Image.new("RGBA", (self.w * _sc, int(60 * s * _sc)), (0, 0, 0, 0))
        tmp_draw = ImageDraw.Draw(tmp)
        bbox = tmp_draw.textbbox((0, 0), text, font=font_big)
        tw = bbox[2] - bbox[0]
        # Figma line-height box 기준 중앙 정렬
        lh_box = int(st["line_height_px"] * s * _sc)
        glyph_h = bbox[3] - bbox[1]
        text_y = (lh_box - glyph_h) // 2 - bbox[1]
        text_x = (self.w * _sc - tw) // 2 - bbox[0]
        tmp_draw.text((text_x, text_y), text, font=font_big, fill=BRAND["white"])
        # 다운스케일
        final_w = self.w
        final_h = int(60 * s)
        wm_strip = tmp.resize((final_w, final_h), Image.LANCZOS)
        paste_y = int(LAYOUT["watermark_y"] * s)
        img.paste(wm_strip, (0, paste_y), wm_strip)

    # ── 텍스트 블록 렌더링 ──

    def _render_text_block(self, draw, x, y, text, style_name, max_w,
                           color, align="left"):
        s = self.s
        st = TEXT_STYLES[style_name]
        font = _load_font(st["font"], int(st["size"] * s))
        ls = st["letter_spacing"] * s
        lh = st["line_height_px"] * s
        lines = self._wrap_ls(draw, text, font, max_w, ls)
        for ln in lines:
            if align == "center":
                tw = self._text_w_ls(draw, ln, font, ls)
                lx = x + (max_w - tw) // 2
            else:
                lx = x
            self._draw_text_ls(draw, lx, y, ln, font, color, ls)
            y += lh
        return y, len(lines)

    def _calc_block_height(self, draw, text, style_name, max_w):
        s = self.s
        st = TEXT_STYLES[style_name]
        font = _load_font(st["font"], int(st["size"] * s))
        ls = st["letter_spacing"] * s
        lh = st["line_height_px"] * s
        lines = self._wrap_ls(draw, text, font, max_w, ls)
        return lh * len(lines), len(lines)

    # ═══════════════════════════════════════════════════════
    # Type 1: COVER — Unsplash 배경 + 그라디언트 + 90pt 제목
    # (제품 이미지 사용 안 함 — 네이티브 콘텐츠 느낌)
    # ═══════════════════════════════════════════════════════

    def render_cover(self, title, subtitle="", bg_image=None,
                     badge_text="", title_size=None):
        s = self.s
        photo = _open_image(bg_image)
        if not photo:
            all_text = title + " " + (subtitle or "")
            photo = _fetch_unsplash_bg(_extract_search_query(all_text))

        if photo:
            img = _fit_cover(photo.convert("RGB"), self.w, self.h).convert("RGBA")
        else:
            img = Image.new("RGBA", (self.w, self.h), (*BRAND["dark_red"], 255))

        img = self._draw_gradient(img)
        draw = ImageDraw.Draw(img)

        # Figma: x=68, y=937, w=932
        tx = int(LAYOUT["text_x"] * s)
        max_w = int(LAYOUT["cover_text_w"] * s)
        wm_y = int(LAYOUT["watermark_y"] * s)

        # 텍스트 높이 계산 → 워터마크 위에 배치
        block_h, _ = self._calc_block_height(draw, title, "cover_title", max_w)
        gap_to_wm = int(125 * s)  # Figma: 텍스트 하단 → 워터마크 상단 125px
        y_start = wm_y - gap_to_wm - block_h

        self._render_text_block(draw, tx, y_start, title, "cover_title",
                                max_w, BRAND["white"])
        self._draw_watermark(img)
        img = self._place_badge(img)
        return self._to_bytes(img)

    # ═══════════════════════════════════════════════════════
    # Type 2: CONTENT — 제품/Unsplash 배경 + 그라디언트 + 52pt 본문
    # ═══════════════════════════════════════════════════════

    def render_content(self, heading, body="", slide_num=None,
                       total_slides=None, bg_image=None):
        s = self.s
        all_text = (heading or "") + " " + (body or "")
        photo = _open_image(bg_image)
        if not photo:
            photo = _find_product_bg(all_text)
        if not photo:
            photo = _fetch_unsplash_bg(_extract_search_query(all_text))

        if photo:
            img = _fit_cover(photo.convert("RGB"), self.w, self.h).convert("RGBA")
        else:
            img = Image.new("RGBA", (self.w, self.h), (*BRAND["dark_red"], 255))

        img = self._draw_gradient(img)
        draw = ImageDraw.Draw(img)

        # Figma: x=68, y=937, w=942
        tx = int(LAYOUT["text_x"] * s)
        max_w = int(LAYOUT["content_text_w"] * s)
        wm_y = int(LAYOUT["watermark_y"] * s)

        # heading/body 분리 렌더링 (heading 큰 폰트 + body 작은 폰트)
        clean_heading = (heading or "").strip()
        clean_body = (body or "").replace("- ", "").strip()
        gap_between = int(20 * s)  # heading↔body 간격
        gap_to_wm = int(111 * s)

        # heading 높이 계산
        h_block_h = 0
        if clean_heading:
            h_block_h, _ = self._calc_block_height(
                draw, clean_heading, "content_heading", max_w)

        # body 높이 계산
        b_block_h = 0
        if clean_body:
            b_block_h, _ = self._calc_block_height(
                draw, clean_body, "content_body", max_w)

        total_h = h_block_h + (gap_between if clean_body and clean_heading else 0) + b_block_h
        y_start = wm_y - gap_to_wm - total_h

        # heading 렌더링
        if clean_heading:
            y_end, _ = self._render_text_block(
                draw, tx, y_start, clean_heading, "content_heading",
                max_w, BRAND["white"])
            y_start = y_end + gap_between

        # body 렌더링
        if clean_body:
            self._render_text_block(
                draw, tx, y_start, clean_body, "content_body",
                max_w, BRAND["white"])
        self._draw_watermark(img)
        img = self._place_badge(img)
        return self._to_bytes(img)

    # ═══════════════════════════════════════════════════════
    # Type 3: CHECKLIST — 다크레드 배경 + 구분선 + 체크리스트
    # ═══════════════════════════════════════════════════════

    def render_checklist(self, title, items, bg_image=None):
        s = self.s
        img = Image.new("RGBA", (self.w, self.h), (*BRAND["dark_red"], 255))

        photo = _open_image(bg_image)
        if photo:
            photo_fit = _fit_cover(photo.convert("RGB"), self.w, self.h)
            dark_overlay = Image.new("RGBA", (self.w, self.h), (*BRAND["dark_red"], 200))
            img = Image.alpha_composite(photo_fit.convert("RGBA"), dark_overlay)

        draw = ImageDraw.Draw(img)
        pad = int(84 * s)
        max_w = self.w - pad * 2

        # 상단 구분선
        line_y1 = int(140 * s)
        draw.line([(pad, line_y1), (self.w - pad, line_y1)],
                  fill=(*BRAND["white"], 80), width=1)

        # 제목
        y = line_y1 + int(60 * s)
        y, _ = self._render_text_block(draw, pad, y, title, "checklist_title",
                                       max_w, BRAND["white"], align="center")

        # 체크리스트 아이템
        y += int(40 * s)
        st_item = TEXT_STYLES["checklist_item"]
        font_item = _load_font(st_item["font"], int(st_item["size"] * s))
        ls_item = st_item["letter_spacing"] * s
        lh_item = st_item["line_height_px"] * s
        checkbox_size = int(46 * s)
        checkbox_gap = int(16 * s)

        # 체크박스 아이콘을 고해상도로 미리 렌더링 (안티앨리어싱)
        _cb_scale = 3
        _cb_big = checkbox_size * _cb_scale
        _cb_img = Image.new("RGBA", (_cb_big, _cb_big), (0, 0, 0, 0))
        _cb_draw = ImageDraw.Draw(_cb_img)
        _cb_r = int(8 * s * _cb_scale)
        _cb_draw.rounded_rectangle(
            [0, 0, _cb_big - 1, _cb_big - 1],
            radius=_cb_r, fill=(255, 255, 255, 220))
        # 체크마크
        _cp = int(10 * s * _cb_scale)
        _ccx, _ccy = _cp, _cb_big // 2
        _cb_draw.line(
            [(_ccx, _ccy),
             (_ccx + int(8 * s * _cb_scale), _ccy + int(10 * s * _cb_scale)),
             (_ccx + int(22 * s * _cb_scale), _ccy - int(8 * s * _cb_scale))],
            fill=BRAND["dark_red"], width=int(4 * s * _cb_scale))
        _cb_icon = _cb_img.resize((checkbox_size, checkbox_size), Image.LANCZOS)

        for item in items:
            item_text = item.strip()
            if not item_text:
                continue
            cb_x = pad + int(60 * s)
            font_h = draw.textbbox((0, 0), "가", font=font_item)
            glyph_h = font_h[3] - font_h[1]
            cb_y = int(y) + (glyph_h - checkbox_size) // 2
            img.paste(_cb_icon, (cb_x, cb_y), _cb_icon)
            text_x = cb_x + checkbox_size + checkbox_gap
            text_max_w = self.w - text_x - pad
            item_lines = self._wrap_ls(draw, item_text, font_item, text_max_w, ls_item)
            for ln in item_lines:
                self._draw_text_ls(draw, text_x, int(y), ln, font_item,
                                   BRAND["white"], ls_item)
                y += lh_item

        # 하단 구분선
        line_y2 = int(y) + int(20 * s)
        draw.line([(pad, line_y2), (self.w - pad, line_y2)],
                  fill=(*BRAND["white"], 80), width=1)

        self._draw_watermark(img)
        img = self._place_badge(img)
        return self._to_bytes(img)

    # ═══════════════════════════════════════════════════════
    # Type 4: CLOSING — 피그마 원본 이미지 고정
    # ═══════════════════════════════════════════════════════

    def render_closing(self, cta_text="", account_name="", bg_image=None):
        closing_img = _load_asset("closing_fixed.png")
        if closing_img:
            img = _fit_cover(closing_img.convert("RGB"), self.w, self.h)
            return self._to_bytes(img)
        img = Image.new("RGB", (self.w, self.h), BRAND["dark_red"])
        draw = ImageDraw.Draw(img)
        self._draw_watermark(img)
        return self._to_bytes(img)

    # ═══════════════════════════════════════════════════════
    # 일괄 렌더링
    # ═══════════════════════════════════════════════════════

    def render_all(self, slides_data):
        results = []
        content_idx = 0
        content_total = sum(1 for s in slides_data if s["type"] == "content")
        for slide in slides_data:
            st = slide["type"]
            bg = slide.get("bg_image")
            if st == "cover":
                results.append(self.render_cover(
                    slide.get("title", ""), slide.get("subtitle", ""), bg_image=bg))
            elif st == "content":
                content_idx += 1
                results.append(self.render_content(
                    slide.get("heading", ""), slide.get("body", ""),
                    slide_num=content_idx, total_slides=content_total, bg_image=bg))
            elif st == "checklist":
                results.append(self.render_checklist(
                    slide.get("title", ""), slide.get("items", []), bg_image=bg))
            elif st == "closing":
                results.append(self.render_closing(
                    slide.get("cta_text", ""), slide.get("account_name", ""), bg_image=bg))
        return results
