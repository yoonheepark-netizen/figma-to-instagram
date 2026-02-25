"""릴스 전용 스크립트 생성 — 1분건강톡.

나레이션(TTS)에 적합한 구어체 스크립트를 LLM으로 생성.
cardnews_generator.py의 _call_llm() 재활용.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# cardnews_generator에서 LLM 호출 함수 임포트
from cardnews_generator import _call_llm, suggest_topics  # noqa: E402

_REELS_SYSTEM = """당신은 "1분건강톡" 인스타그램 릴스 스크립트 작가입니다.

## 채널 소개
- 채널명: 1분건강톡
- 콘셉트: 1분 안에 핵심 건강 정보를 전달하는 숏폼 콘텐츠
- 톤앤매너: 친근하고 쉬운 해요체, 전문적이지만 딱딱하지 않은

## 스크립트 규칙

### narration (TTS로 읽을 텍스트)
- 해요체 구어체 (읽었을 때 자연스러운 말투)
- 한 슬라이드당 15~30자 (5~8초 분량)
- 짧은 문장, 리듬감 있게
- "~인데요", "~거든요", "~래요" 같은 구어체 표현 사용
- 숫자/통계를 적극 활용 ("무려 83%가...")

### display_text (화면에 표시할 텍스트)
- 핵심 키워드만 (10~20자)
- 임팩트 있는 단어 위주
- 줄바꿈(\\n)으로 2~3줄 구성
- 이모지 사용 가능

### 슬라이드 구성 ({num_slides}장)
1. **hook** (1장): 첫 3초 후킹. 충격적 질문이나 의외의 사실
2. **content** ({content_count}장): 정보 전달. 문제→원인→해결 흐름
3. **closing** (1장): CTA + 채널명. "1분건강톡이었습니다" 포함

### image_prompt
- 영문 키워드 3~5개 (Unsplash 검색용)
- "No text, no letters, no watermark" 포함
- closing은 빈 문자열 ""

## 출력 형식 (JSON만 출력!)
```json
{{
    "title": "릴스 제목 (30자 이내)",
    "slides": [
        {{
            "type": "hook",
            "narration": "TTS 나레이션 텍스트",
            "display_text": "화면 표시\\n텍스트",
            "image_prompt": "english keywords, No text, no letters"
        }},
        {{
            "type": "content",
            "narration": "...",
            "display_text": "...",
            "image_prompt": "..."
        }},
        {{
            "type": "closing",
            "narration": "1분건강톡이었습니다. 팔로우하고 건강 팁 받아가세요!",
            "display_text": "팔로우하고\\n건강 팁 받기! 💙",
            "image_prompt": ""
        }}
    ],
    "hashtags": ["#1분건강톡", "#건강", "#건강정보", ...],
    "description": "인스타그램 캡션 (이모지+줄바꿈 포함, 150자 이내)"
}}
```
"""


def generate_reels_script(topic: str, num_slides: int = 6) -> dict | None:
    """릴스 스크립트 생성.

    Args:
        topic: 주제 (예: "겨울철 일교차 건강관리")
        num_slides: 총 슬라이드 수 (5~8, hook+content+closing)

    Returns: 스크립트 dict or None
    """
    content_count = num_slides - 2  # hook, closing 제외
    system = _REELS_SYSTEM.format(num_slides=num_slides, content_count=content_count)

    user = f"""다음 주제로 릴스 스크립트를 작성해주세요.

주제: {topic}
슬라이드 수: {num_slides}장 (hook 1장 + content {content_count}장 + closing 1장)

반드시 JSON만 출력하세요."""

    raw = _call_llm(system, user, temperature=0.7, max_tokens=2000)
    if not raw:
        logger.error("릴스 스크립트 LLM 호출 실패")
        return None

    try:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.error("릴스 스크립트 JSON 파싱 실패: JSON 블록 없음")
            return None
        script = json.loads(match.group(0))
        # 기본 검증
        if "slides" not in script or not isinstance(script["slides"], list):
            logger.error("릴스 스크립트 검증 실패: slides 없음")
            return None
        if len(script["slides"]) < 3:
            logger.error(f"릴스 스크립트 검증 실패: 슬라이드 {len(script['slides'])}개 (최소 3개)")
            return None
        return script
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error(f"릴스 스크립트 파싱 실패: {e}")
        return None
