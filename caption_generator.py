import random
import logging

logger = logging.getLogger(__name__)

# 톤별 후킹 문장 템플릿 ({kw}에 키워드 삽입)
_HOOKS = {
    "정보성": [
        "알고 계셨나요? {kw}의 놀라운 효과!",
        "{kw}, 이것만 알면 됩니다.",
        "전문가가 알려주는 {kw}의 핵심 포인트",
        "{kw}에 대해 꼭 알아야 할 3가지",
        "많은 분들이 모르는 {kw}의 비밀",
    ],
    "감성": [
        "오늘도 {kw}와 함께하는 하루",
        "{kw}이(가) 당신의 일상을 바꿔줄 거예요",
        "작은 변화가 큰 차이를 만듭니다, {kw}",
        "당신을 위한 특별한 {kw} 이야기",
        "{kw}, 마음까지 건강해지는 시간",
    ],
    "유머": [
        "{kw} 안 하면 손해인 거 아시죠?",
        "친구한테 알려주면 고마워할 {kw} 꿀팁",
        "{kw} 시작하면 멈출 수가 없음 ㅋㅋ",
        "이거 보고도 {kw} 안 하실 건가요?",
        "{kw}의 세계에 오신 것을 환영합니다",
    ],
    "전문적": [
        "근거 기반 {kw} 가이드",
        "{kw}: 최신 연구가 말하는 효과와 방법",
        "데이터로 증명된 {kw}의 실질적 효과",
        "{kw}에 대한 전문가 분석 리포트",
        "임상 데이터로 보는 {kw}의 진실",
    ],
}

# 톤별 CTA (행동 유도)
_CTAS = {
    "정보성": [
        "더 궁금하시면 댓글로 질문해주세요!",
        "도움이 되셨다면 저장해두세요.",
        "주변에 도움이 될 분에게 공유해주세요!",
        "팔로우하고 건강 정보 놓치지 마세요.",
    ],
    "감성": [
        "여러분의 이야기도 들려주세요.",
        "공감되셨다면 좋아요 눌러주세요.",
        "소중한 사람에게 공유해보세요.",
        "오늘도 건강한 하루 보내세요.",
    ],
    "유머": [
        "공감되면 친구 태그 고고!",
        "저장 안 하면 나중에 후회합니다.",
        "좋아요 누르고 가세요~ 안 누르면 섭합니다.",
        "이 정보 퍼뜨려야 합니다, 공유 필수!",
    ],
    "전문적": [
        "전문 상담이 필요하시면 DM 주세요.",
        "더 자세한 정보는 프로필 링크에서 확인하세요.",
        "관련 질문은 댓글로 남겨주세요, 답변드리겠습니다.",
        "팔로우하시면 전문 건강 정보를 받아보실 수 있습니다.",
    ],
}

# 본문 연결 템플릿
_BODY_TEMPLATES = [
    "{kw1}과(와) {kw2}의 조합은 생각보다 강력합니다.\n\n매일 조금씩 실천하면 확실한 변화를 느낄 수 있어요.",
    "건강한 라이프스타일의 시작은 {kw1}부터!\n\n{kw2}까지 함께하면 시너지 효과가 배가 됩니다.",
    "{kw1} 하나만으로도 달라질 수 있어요.\n\n여기에 {kw2}을(를) 더하면 완벽한 조합이 완성됩니다.",
    "많은 분들이 {kw1}의 중요성을 간과하고 있어요.\n\n오늘 이 게시물에서 {kw2}과(와) 함께 핵심을 짚어드릴게요.",
]


def generate_caption(
    image_urls=None,
    account_name="",
    past_top_captions=None,
    top_keywords=None,
    top_hashtags=None,
    tone="정보성",
):
    """인사이트 데이터 기반으로 Instagram 캡션과 해시태그를 생성합니다.

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
        keywords = ["건강", "웰빙", "라이프스타일"]
    random.shuffle(keywords)
    kw1, kw2 = keywords[0], keywords[1 % len(keywords)]

    # 1) 후킹 문장
    hooks = _HOOKS.get(tone, _HOOKS["정보성"])
    hook = random.choice(hooks).format(kw=kw1)

    # 2) 본문
    body = random.choice(_BODY_TEMPLATES).format(kw1=kw1, kw2=kw2)

    # 3) CTA
    ctas = _CTAS.get(tone, _CTAS["정보성"])
    cta = random.choice(ctas)

    caption = f"{hook}\n\n{body}\n\n{cta}"

    # 해시태그 생성
    tags = set()
    if top_hashtags:
        for t in top_hashtags[:8]:
            tags.add(t.lstrip("#"))
    # 키워드 기반 해시태그 추가
    for kw in keywords[:5]:
        tags.add(kw)
    # 기본 해시태그 보충
    defaults = ["건강", "건강관리", "건강정보", "헬스케어", "웰빙",
                 "건강습관", "데일리", "일상", "꿀팁", "추천"]
    for d in defaults:
        if len(tags) >= 12:
            break
        tags.add(d)

    hashtags = " ".join(f"#{t}" for t in list(tags)[:15])

    full = f"{caption}\n\n{hashtags}"
    return {"caption": caption, "hashtags": hashtags, "full": full}
