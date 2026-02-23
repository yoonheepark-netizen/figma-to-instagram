import random
import logging

logger = logging.getLogger(__name__)

# 수壽 계정 시그니처
_SIGNATURE = (
    "- \n\n"
    "더 오래, 더 건강하게.\n"
    "한의사가 만든 한의 브랜드, 수壽\n"
    "@thesoo_official \n"
    "-"
)

# CTA 문구 (실제 게시물에서 추출)
_CTAS = [
    "지금 바로 프로필 링크를 클릭하고\n공식 홈페이지를 확인하세요.",
    "지금 프로필링크 클릭하고\n공식 홈페이지를 살펴보세요",
]

# 후킹 헤드라인 (실제 패턴 기반)
_HOOKS = {
    "정보성": [
        "{kw}의 놀라운 효과",
        "알고 계셨나요? {kw}의 차이",
        "{kw}, 이것만 알면 됩니다",
        "{kw}에 대해 꼭 알아야 할 것",
        "많은 분들이 모르는 {kw}의 비밀",
    ],
    "감성": [
        "{kw}와 함께하는 건강한 일상",
        "당신을 위한 특별한 {kw}",
        "작은 변화가 큰 차이를 만듭니다",
        "{kw}, 마음까지 건강해지는 시간",
        "건강한 내일을 위한 선택, {kw}",
    ],
    "유머": [
        "{kw} 안 하면 손해인 거 아시죠?",
        "친구한테 알려주면 고마워할 {kw} 이야기",
        "이거 보고도 {kw} 안 하실 건가요?",
        "{kw}의 세계에 오신 것을 환영합니다",
        "{kw}, 한 번 시작하면 멈출 수 없습니다",
    ],
    "전문적": [
        "근거 기반 {kw} 가이드",
        "{kw}: 최신 연구가 말하는 효과와 방법",
        "데이터로 증명된 {kw}의 실질적 효과",
        "{kw}에 대한 전문가 분석",
        "임상 데이터로 보는 {kw}의 진실",
    ],
}

# 본문 템플릿 (수壽 스타일: 짧은 문단 + 줄바꿈)
_BODY_TEMPLATES = [
    (
        "한약의 퀄리티를 결정하는 것은\n"
        "좋은 약재와 깨끗하고 엄격한 조제 과정입니다.\n\n"
        "한의사와 한약사의 관리 아래\n"
        "엄선된 한약재로 만든\n"
        "{kw1}의 차이를 경험해 보세요."
    ),
    (
        "{kw1}과(와) {kw2}의 조합은\n"
        "생각보다 강력합니다.\n\n"
        "동의보감에 기록된 전통 처방을 바탕으로\n"
        "엄선된 한약재로 조제한\n"
        "믿을 수 있는 한약입니다."
    ),
    (
        "전통의 지혜와 현대의 기술을\n"
        "조화롭게 결합하는 것이 중요합니다.\n\n"
        "{kw1}의 효과를 극대화하면서\n"
        "안전한 한약을 제공합니다.\n\n"
        "고유의 노하우와 기술이 결합된\n"
        "현대 한약을 경험해 보세요."
    ),
    (
        "{kw1}의 중요성을\n"
        "간과하고 계시지는 않나요?\n\n"
        "식품의약품안전처 인증을 받은\n"
        "엄선된 재료로 만들어\n"
        "더욱 안심하고 드실 수 있습니다."
    ),
    (
        "더 믿을 수 있는 한약을\n"
        "더 새로워질 수 있는 한약을\n\n"
        "{kw1}부터 {kw2}까지\n"
        "전 국민의 건강을 책임질\n"
        "한약의 새로운 시대를 열어갑니다."
    ),
]

# 수壽 브랜드 해시태그 (실제 사용 태그)
_BRAND_HASHTAGS = ["수한약", "공진단수", "경옥고수"]
_EXTRA_HASHTAGS = ["이정재공진단", "이정재경옥고", "한의사", "한약", "공진단", "경옥고"]


def generate_caption(
    image_urls=None,
    account_name="",
    past_top_captions=None,
    top_keywords=None,
    top_hashtags=None,
    tone="정보성",
):
    """수壽 계정 스타일에 맞는 Instagram 캡션과 해시태그를 생성합니다.

    Args:
        image_urls: (미사용, 호환성 유지)
        account_name: 계정 이름
        past_top_captions: 과거 성과 좋은 캡션 리스트
        top_keywords: 성과 좋은 키워드 리스트
        top_hashtags: 성과 좋은 해시태그 리스트
        tone: 캡션 톤 ("정보성", "감성", "유머", "전문적")

    Returns:
        dict: {"caption": str, "hashtags": str, "full": str}
    """
    # 키워드 준비
    keywords = list(top_keywords) if top_keywords else []
    if not keywords:
        keywords = ["공진단", "경옥고", "한약", "면역력", "활력"]
    random.shuffle(keywords)
    kw1, kw2 = keywords[0], keywords[1 % len(keywords)]

    # 1) 후킹 헤드라인
    hooks = _HOOKS.get(tone, _HOOKS["정보성"])
    hook = random.choice(hooks).format(kw=kw1)

    # 2) 본문
    body = random.choice(_BODY_TEMPLATES).format(kw1=kw1, kw2=kw2)

    # 3) CTA
    cta = random.choice(_CTAS)

    # 4) 조합 (수壽 스타일: 헤드라인 → 본문 → CTA → 시그니처)
    caption = f"{hook}\n\n{body}\n\n{cta} \n\n{_SIGNATURE}"

    # 해시태그 생성 (수壽 스타일: 브랜드 태그 위주, 3~5개)
    tags = list(_BRAND_HASHTAGS)
    # 인사이트 기반 태그 추가
    if top_hashtags:
        for t in top_hashtags:
            tag = t.lstrip("#")
            if tag not in tags and len(tags) < 5:
                tags.append(tag)
    # 부족하면 보충
    for t in _EXTRA_HASHTAGS:
        if t not in tags and len(tags) < 5:
            tags.append(t)

    hashtags = " ".join(f"#{t}" for t in tags)

    full = f"{caption}\n\n{hashtags}"
    return {"caption": caption, "hashtags": hashtags, "full": full}
