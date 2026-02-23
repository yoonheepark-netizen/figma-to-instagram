import random
import re
import logging

logger = logging.getLogger(__name__)

# ── 수壽 계정 고정 요소 ──

_SIGNATURE = (
    "- \n\n"
    "더 오래, 더 건강하게.\n"
    "한의사가 만든 한의 브랜드, 수壽\n"
    "@thesoo_official \n"
    "-"
)

_CTAS = [
    "지금 바로 프로필 링크를 클릭하고\n공식 홈페이지를 확인하세요.",
    "지금 프로필링크 클릭하고\n공식 홈페이지를 살펴보세요",
    "지금 바로 프로필 링크를 클릭하고\n수壽를 만나보세요.",
]

_BRAND_HASHTAGS = ["수한약", "공진단수", "경옥고수"]
_EXTRA_HASHTAGS = ["이정재공진단", "이정재경옥고"]

# ── 실제 게시물에서 추출한 헤드라인 패턴 ──
# 구조: 짧은 한 줄, 제품/기술/가치를 함축적으로 표현

_HOOKS = {
    "정보성": [
        "자신있게 권할 수 있는 퀄리티",
        "hGMP 인증으로 더욱 안전하게",
        "일반 분말보다 15배 더 미세하게",
        "자연 그대로, 깨끗하게",
        "전통과 현대의 융합",
        "800년이라는 시간 그 이상의 가치",
        "{kw}의 차이를 만드는 기술력",
        "{kw}, 안전함이 기본입니다",
        "한 번 더 검증하는 품질 관리",
        "{kw}의 핵심은 원료에 있습니다",
    ],
    "감성": [
        "당신의 건강을 지키는 한 알의 정성",
        "오래도록 건강하게, 수壽와 함께",
        "소중한 분에게 전하는 건강",
        "{kw}로 시작하는 건강한 일상",
        "매일의 작은 실천이 큰 변화를 만듭니다",
        "건강한 내일을 위한 오늘의 선택",
        "가족의 건강을 생각하는 마음",
        "{kw}, 진심을 담아 만듭니다",
    ],
    "유머": [
        "{kw} 안 하면 손해인 거 아시죠?",
        "이거 보고도 {kw} 안 하실 건가요?",
        "{kw}, 한 번 시작하면 멈출 수 없습니다",
        "친구한테 알려주면 고마워할 {kw} 이야기",
        "아직도 {kw}의 진가를 모르신다면",
    ],
    "전문적": [
        "근거 기반 {kw} 가이드",
        "데이터로 증명된 {kw}의 실질적 효과",
        "{kw}: 최신 연구가 말하는 효과와 방법",
        "임상 데이터로 보는 {kw}의 진실",
        "{kw}에 대한 전문가 분석",
    ],
}

# ── 실제 게시물에서 추출·확장한 본문 템플릿 ──
# 구조: 짧은 줄 + 빈 줄로 단락 구분, 2~3 단락

_BODY_TEMPLATES = [
    # 품질 (실제 게시물 #1 기반)
    (
        "한약의 퀄리티를 결정하는 것은 좋은 약재, \n"
        "깨끗하고 엄격한 조제 과정입니다. \n\n"
        "한의사와 한약사의 관리 아래 \n"
        "엄선된 한약재를 깨끗이 세척하고\n"
        "모든 공정에 살균 처리 과정을 도입했습니다."
    ),
    # hGMP 인증 (실제 게시물 #2 기반)
    (
        "식품의약품안전처의 \n"
        "hGMP 인증을 받은 한약재만을 사용합니다. \n\n"
        "자체 성분 유전자 검사를 통과한 \n"
        "엄선된 재료로 만들고 \n\n"
        "사향과 침향은 \n"
        "국제거래협약(CITES)에 따라 \n"
        "허가받은 정품만을 사용합니다."
    ),
    # 기술력 (실제 게시물 #3 기반)
    (
        "초미립자 균일 분쇄 기술을 통해 \n"
        "일반 분말보다 최대 15배 더 미세한 입자를 구현했습니다. \n\n"
        "입자가 작아질수록 체내 흡수율은 높아지고, \n"
        "입안에 남는 거친 느낌 없이 목 넘김은 더욱 부드러워집니다."
    ),
    # 원료 (실제 게시물 #4 기반)
    (
        "동의보감에 기록된 전통 처방을 바탕으로 \n"
        "구례, 의성 지리산 등 \n"
        "전국 각지에서 엄선된 한약재로 \n"
        "믿을 수 있는 한약을 조제합니다."
    ),
    # 전통+현대 (실제 게시물 #5 기반)
    (
        "수는 전통의 지혜와 현대의 기술을 \n"
        "조화롭게 결합하는 것이 \n"
        "중요하다고 믿습니다. \n\n"
        "조제과정에서 효과적인 부분을 선별하고, \n"
        "최신 기술을 접목하여 최고의 효과를 내면서도 \n"
        "안전한 한약을 제공합니다. \n\n"
        "고유의 노하우와 기술이 결합된 \n"
        "현대 한약을 경험해 보세요."
    ),
    # 역사 (실제 게시물 #6 기반)
    (
        "1196년부터 시작된 공진단의 역사,\n"
        "단순한 한약을 넘어 조선의 왕들이 아끼고 사랑했던 \n"
        "'왕실 대표 보약'입니다.\n\n"
        "경종, 정조, 순조 등 수많은 왕들이 선택했던 이유를\n"
        "여러분의 일상에서 직접 경험해 보세요.\n\n"
        "한약의 새로운 시대, 수壽가\n"
        "그 전통을 이어갑니다."
    ),
    # 브랜드 철학 (실제 게시물 #9 기반)
    (
        "더 믿을 수 있는 한약을\n"
        "더 새로워질 수 있는 한약을\n\n"
        "전 국민의 건강을 책임질\n"
        "한약의 새로운 시대"
    ),
    # 키워드 활용 - 제품 효능
    (
        "{kw1}은(는) 예로부터\n"
        "기력 보충과 면역력 강화에\n"
        "탁월한 효과가 있는 것으로 알려져 있습니다.\n\n"
        "수壽는 최상급 원료만을 엄선하여\n"
        "그 효과를 더욱 높였습니다."
    ),
    # 키워드 활용 - 차별점
    (
        "{kw1}의 품질은 원료에서 결정됩니다.\n\n"
        "수壽는 원료 선별부터 완성까지\n"
        "한의사가 직접 관리하며\n"
        "모든 과정을 투명하게 공개합니다."
    ),
    # 키워드 활용 - 일상 연결
    (
        "바쁜 일상 속에서도\n"
        "건강을 놓치지 않는 방법.\n\n"
        "{kw1}으로 하루를 시작하면\n"
        "몸이 먼저 변화를 느낍니다.\n\n"
        "수壽가 그 첫걸음을 함께합니다."
    ),
]


def _is_valid_line(line):
    """OCR 라인이 의미 있는 텍스트인지 판단합니다."""
    line = line.strip()
    if not line:
        return False
    # 5자 미만은 대부분 노이즈 (조사/어미 파편)
    if len(line) < 5:
        return False
    # 한글이 2자 이상 포함되어야 의미 있는 한국어 문장
    korean_chars = len(re.findall(r"[가-힣]", line))
    alpha_chars = len(re.findall(r"[a-zA-Z]", line))
    if korean_chars < 2 and alpha_chars < 4:
        return False
    # 특수문자 비율이 40% 이상이면 노이즈
    special = len(re.findall(r"[^가-힣a-zA-Z0-9\s.,!?~ㅡ·\'\"()『』:;]", line))
    if len(line) > 0 and special / len(line) > 0.4:
        return False
    # 완전한 문장이 아닌 파편 (조사/어미로만 시작하는 것) 필터
    if re.match(r"^[은는이가을를의에서로도와과만]", line) and korean_chars < 5:
        return False
    return True


def _clean_ocr_line(line):
    """OCR 라인의 노이즈를 정리합니다."""
    # 줄 앞뒤 특수문자 제거
    line = re.sub(r"^[\|\[\]「」{}\s\-ㅡ]+", "", line)
    line = re.sub(r"[\|\[\]「」{}\s\-]+$", "", line)
    # OCR에서 흔한 오인식 교정
    line = line.replace("\\바", "억")  # 80\바 → 80억
    line = line.replace("!", "!").replace("?", "?")
    return line.strip()


def _clean_ocr_texts(raw_texts):
    """OCR 원본 텍스트를 정제하여 의미 있는 문장만 반환합니다."""
    cleaned = []
    seen = set()

    for raw in raw_texts:
        # 줄 단위로 분리하여 처리
        for line in raw.split("\n"):
            line = _clean_ocr_line(line)
            if not _is_valid_line(line):
                continue
            # URL, 브랜드명 등은 캡션 본문에서 제외 (시그니처에 이미 포함)
            lower = line.lower()
            if any(skip in lower for skip in ["thesoo", "@thesoo", "수壽", "수수", "공식 홈페이지"]):
                continue
            if line not in seen:
                seen.add(line)
                cleaned.append(line)

    return cleaned


def _build_from_image_texts(image_texts, top_hashtags=None, tone="정보성"):
    """이미지에서 추출한 텍스트를 정제 후 수壽 캡션 포맷으로 변환합니다."""
    cleaned = _clean_ocr_texts(image_texts)

    if len(cleaned) < 2:
        return None

    # 첫 번째 의미 있는 문장을 헤드라인으로
    hook = cleaned[0]

    # 나머지를 본문으로 구성 (짧은 줄은 줄바꿈, 단락 구분은 빈 줄)
    body_lines = cleaned[1:]
    body_parts = []
    current_group = []
    for line in body_lines:
        current_group.append(line)
        # 마침표/물음표/느낌표로 끝나거나 3줄 모이면 단락 구분
        if line.endswith((".", "요.", "요", "다.", "다", "!")) or len(current_group) >= 3:
            body_parts.append("\n".join(current_group))
            current_group = []
    if current_group:
        body_parts.append("\n".join(current_group))

    body = "\n\n".join(body_parts)

    # CTA
    cta = random.choice(_CTAS)

    # 조합 (수壽 스타일)
    caption = f"{hook}\n\n{body} \n\n{cta} \n\n{_SIGNATURE}"

    # 해시태그
    tags = list(_BRAND_HASHTAGS)
    if top_hashtags:
        for t in top_hashtags:
            tag = t.lstrip("#")
            if tag not in tags and len(tags) < 5:
                tags.append(tag)
    for t in _EXTRA_HASHTAGS:
        if t not in tags and len(tags) < 5:
            tags.append(t)
    hashtags = " ".join(f"#{t}" for t in tags)

    full = f"{caption}\n\n{hashtags}"
    return {"caption": caption, "hashtags": hashtags, "full": full}


def generate_caption(
    image_texts=None,
    account_name="",
    past_top_captions=None,
    top_keywords=None,
    top_hashtags=None,
    tone="정보성",
    **kwargs,
):
    """수壽 계정 스타일에 맞는 Instagram 캡션과 해시태그를 생성합니다.

    Args:
        image_texts: 이미지에서 추출한 텍스트 리스트
        account_name: 계정 이름
        past_top_captions: 과거 성과 좋은 캡션 리스트
        top_keywords: 성과 좋은 키워드 리스트
        top_hashtags: 성과 좋은 해시태그 리스트
        tone: 캡션 톤 ("정보성", "감성", "유머", "전문적")

    Returns:
        dict: {"caption": str, "hashtags": str, "full": str}
    """
    # 이미지 텍스트가 있으면 직접 활용하여 캡션 구성
    if image_texts:
        result = _build_from_image_texts(image_texts, top_hashtags, tone)
        if result:
            return result

    # 키워드 준비 (이미지 텍스트 없거나 실패 시 템플릿 모드)
    keywords = list(top_keywords) if top_keywords else []
    if not keywords:
        keywords = ["공진단", "경옥고", "한약", "면역력", "활력"]
    random.shuffle(keywords)
    kw1 = keywords[0]
    kw2 = keywords[1 % len(keywords)]

    # 1) 후킹 헤드라인
    hooks = _HOOKS.get(tone, _HOOKS["정보성"])
    hook = random.choice(hooks).format(kw=kw1)

    # 2) 본문
    body = random.choice(_BODY_TEMPLATES).format(kw1=kw1, kw2=kw2)

    # 3) CTA
    cta = random.choice(_CTAS)

    # 4) 조합 (수壽 스타일: 헤드라인 → 본문 → CTA → 시그니처)
    caption = f"{hook}\n\n{body} \n\n{cta} \n\n{_SIGNATURE}"

    # 해시태그 생성 (수壽 스타일: 브랜드 태그 위주, 3~5개)
    tags = list(_BRAND_HASHTAGS)
    if top_hashtags:
        for t in top_hashtags:
            tag = t.lstrip("#")
            if tag not in tags and len(tags) < 5:
                tags.append(tag)
    for t in _EXTRA_HASHTAGS:
        if t not in tags and len(tags) < 5:
            tags.append(t)

    hashtags = " ".join(f"#{t}" for t in tags)

    full = f"{caption}\n\n{hashtags}"
    return {"caption": caption, "hashtags": hashtags, "full": full}
