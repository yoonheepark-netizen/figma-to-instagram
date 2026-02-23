import logging
import os

logger = logging.getLogger(__name__)


def generate_caption(
    image_urls=None,
    account_name="",
    past_top_captions=None,
    top_keywords=None,
    top_hashtags=None,
    tone="정보성",
):
    """Google Gemini API를 사용하여 Instagram 캡션과 해시태그를 생성합니다.

    Args:
        image_urls: 이미지 URL 리스트 (비주얼 기반 캡션 생성용)
        account_name: 계정 이름
        past_top_captions: 과거 성과 좋은 캡션 리스트
        top_keywords: 성과 좋은 키워드 리스트
        top_hashtags: 성과 좋은 해시태그 리스트
        tone: 캡션 톤 ("정보성", "감성", "유머", "전문적")

    Returns:
        dict: {"caption": str, "hashtags": str, "full": str}
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY가 설정되지 않았습니다.")

    from google import genai

    client = genai.Client(api_key=api_key)

    tone_guide = {
        "정보성": "정보를 명확하게 전달하는 교육적 톤. 핵심 포인트를 간결하게 정리.",
        "감성": "공감과 감동을 주는 따뜻한 톤. 스토리텔링과 감정 표현 활용.",
        "유머": "재치있고 가벼운 톤. 밈이나 트렌드 표현 활용.",
        "전문적": "전문성과 신뢰감을 주는 톤. 데이터나 근거 기반 표현.",
    }

    # 프롬프트 구성
    parts = []
    parts.append(f"Instagram 계정 '{account_name}'에 올릴 게시물의 캡션과 해시태그를 작성해주세요.")
    parts.append(f"\n톤: {tone} — {tone_guide.get(tone, tone_guide['정보성'])}")

    if past_top_captions:
        caps = "\n".join(f"- {c[:150]}" for c in past_top_captions[:5])
        parts.append(f"\n이 계정에서 성과가 좋았던 캡션 스타일:\n{caps}")

    if top_keywords:
        parts.append(f"\n팔로워가 반응하는 키워드: {', '.join(top_keywords[:10])}")

    if top_hashtags:
        parts.append(f"\n성과 좋은 해시태그: {', '.join('#' + h for h in top_hashtags[:10])}")

    parts.append("\n요구사항:")
    parts.append("1. 캡션은 한국어로 작성 (150~300자)")
    parts.append("2. 첫 줄에 눈길을 끄는 후킹 문장")
    parts.append("3. 마지막에 행동 유도(CTA) 포함")
    parts.append("4. 해시태그는 10~15개, 관련성 높은 것 위주")
    parts.append("\n출력 형식:")
    parts.append("[캡션]\n(캡션 내용)\n\n[해시태그]\n(해시태그 나열)")

    prompt_text = "\n".join(parts)

    # 메시지 구성 (이미지가 있으면 vision 사용)
    contents = []
    if image_urls:
        import urllib.request
        for url in image_urls[:3]:
            try:
                resp = urllib.request.urlopen(url)
                img_bytes = resp.read()
                content_type = resp.headers.get("Content-Type", "image/png")
                contents.append(genai.types.Part.from_bytes(data=img_bytes, mime_type=content_type))
            except Exception:
                pass
        contents.append(prompt_text + "\n\n위 이미지의 내용을 반영하여 캡션을 작성해주세요.")
    else:
        contents.append(prompt_text)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
        )
        result_text = response.text

        # 파싱
        caption = ""
        hashtags = ""
        if "[캡션]" in result_text and "[해시태그]" in result_text:
            parts = result_text.split("[해시태그]")
            caption = parts[0].replace("[캡션]", "").strip()
            hashtags = parts[1].strip() if len(parts) > 1 else ""
        else:
            caption = result_text.strip()

        full = f"{caption}\n\n{hashtags}".strip() if hashtags else caption
        return {"caption": caption, "hashtags": hashtags, "full": full}

    except Exception as e:
        logger.error(f"캡션 생성 실패: {e}")
        raise RuntimeError(f"캡션 생성 실패: {e}")
