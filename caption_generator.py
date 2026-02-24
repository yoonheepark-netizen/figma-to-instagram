import json
import os
import random
import re
import hashlib
import logging

import requests as _requests

logger = logging.getLogger(__name__)

# ── AI API (동적 캡션 생성) ──

try:
    import anthropic
    _anthropic_available = True
except ImportError:
    _anthropic_available = False

# 세션 내 캐시: 동일 이미지 텍스트 → 동일 캡션 (API 재호출 방지)
_caption_cache = {}


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

# ── 주제 감지 매핑 ──
_TOPIC_KEYWORDS = {
    "뇌건강": ["뇌", "BDNF", "강글리오사이드", "신경", "뇌세포", "인지", "기억력", "집중력", "두뇌", "치매"],
    "녹용": ["녹용", "녹각", "사슴", "뿔", "성장인자", "IGF"],
    "공진단": ["공진단", "사향", "당귀", "산수유", "왕실", "보약"],
    "경옥고": ["경옥고", "생지황", "인삼", "백복령", "꿀"],
    "면역력": ["면역", "면역력", "항체", "백혈구", "감기", "바이러스", "방어", "감염", "열"],
    "활력": ["활력", "피로", "에너지", "기력", "체력", "원기", "보양", "기운", "후유증"],
    "품질": ["hGMP", "GMP", "인증", "검사", "품질", "살균", "세척", "위생", "안전"],
    "원료": ["원료", "약재", "한약재", "산지", "지리산", "구례", "의성", "엄선"],
    "기술력": ["초미립자", "분쇄", "입자", "흡수율", "기술", "균일", "미세"],
    "전통": ["동의보감", "전통", "처방", "역사", "왕", "조선", "1196년", "800년"],
    "수면": ["수면", "숙면", "불면", "잠", "밤", "멜라토닌", "수면질"],
    "혈액순환": ["혈액", "순환", "혈관", "혈류", "혈행", "어혈"],
    "소화": ["소화", "위장", "장", "소화기", "장건강"],
    "피부": ["피부", "콜라겐", "노화", "안티에이징", "탄력", "윤기"],
    "다이어트": ["다이어트", "체중", "체지방", "대사", "신진대사"],
    "호흡기": ["호흡기", "폐", "기관지", "코", "축농증", "비염", "기침", "가래"],
    "명절건강": ["명절", "설날", "추석", "연휴", "후유증", "과식", "소화불량"],
    "환절기": ["환절기", "일교차", "계절", "겨울", "봄", "가을"],
    "걷기": ["걷기", "보폭", "보행", "걸음", "산책", "걷는", "걸을", "자세", "무릎", "관절", "굳은살", "발바닥", "밑창", "스트레칭"],
    "운동": ["운동", "스쿼트", "플랭크", "근력", "유산소", "헬스", "트레이닝", "스트레칭", "요가", "필라테스"],
    "우황청심원": ["우황청심원", "우황", "청심원", "송나라", "역수입", "사신", "명나라", "황제"],
}

# 해시태그 매핑
_TOPIC_HASHTAG_MAP = {
    "뇌건강": "뇌건강", "녹용": "녹용", "공진단": "공진단",
    "경옥고": "경옥고", "면역력": "면역력", "활력": "활력충전",
    "품질": "한약품질", "기술력": "한약기술", "전통": "전통한약",
    "호흡기": "호흡기건강", "명절건강": "명절후유증", "환절기": "환절기건강",
    "수면": "숙면", "혈액순환": "혈액순환", "소화": "소화건강",
    "피부": "피부건강", "걷기": "걷기운동", "운동": "운동건강",
    "다이어트": "다이어트", "우황청심원": "우황청심원",
}

# ── 정적 라이브러리 (API 폴백용) ──

_STATIC_HOOKS = {
    "면역력": ["면역력이 곧 건강입니다", "몸의 방어력을 높이는 방법", "감기 이기는 몸을 만드는 법"],
    "뇌건강": ["두뇌 활력의 비밀", "뇌 건강, 지금부터 준비하세요"],
    "녹용": ["자연이 선물한 최고의 보양", "녹용의 진가를 경험하세요"],
    "공진단": ["800년 역사가 증명하는 효능", "왕실의 보약, 공진단"],
    "경옥고": ["호흡기 건강의 든든한 파트너", "면역력의 기본, 경옥고"],
    "활력": ["오늘의 활력을 위한 선택", "지치지 않는 하루의 비결"],
    "품질": ["자신있게 권할 수 있는 퀄리티", "한 번 더 검증하는 품질 관리"],
    "원료": ["원료의 차이가 결과의 차이", "엄선된 약재의 힘"],
    "기술력": ["일반 분말보다 15배 더 미세하게", "기술력이 만드는 차이"],
    "전통": ["전통과 현대의 융합", "동의보감의 지혜를 잇다"],
    "호흡기": ["호흡기 건강, 예방이 최선입니다", "숨 쉬는 것이 편안해지는 방법"],
    "명절건강": ["명절 후 몸이 보내는 신호", "명절 피로, 방치하지 마세요"],
    "환절기": ["환절기, 면역력이 답입니다", "일교차가 클수록 건강 관리가 중요합니다"],
    "걷기": ["당신의 보폭은 건강을 말해줍니다", "올바른 보행이 건강의 시작입니다"],
    "운동": ["운동, 꾸준함이 최고의 보약입니다", "매일 30분, 몸이 달라집니다"],
    "수면": ["깊은 잠이 건강의 시작입니다", "잠이 보약이라는 말, 사실입니다"],
    "혈액순환": ["혈액순환이 건강의 기본입니다"],
    "소화": ["편안한 소화가 건강의 시작입니다"],
    "피부": ["안에서부터 빛나는 피부"],
    "우황청심원": ["중국 황제가 조선에서만 가져간 것?", "600년 역사가 증명한 원료의 힘"],
}

_STATIC_BODIES = {
    "면역력": "면역력은 우리 몸의 방어 체계입니다.\n외부 바이러스와 세균으로부터\n몸을 보호하는 가장 기본적인 힘입니다.\n\n면역력이 약해지면 감기에 걸리기 쉽고\n회복 속도도 느려집니다.\n\n수壽는 면역력 강화에 도움이 되는\n검증된 한약재로 몸의 방어력을 높입니다.",
    "뇌건강": "뇌세포는 한 번 손상되면\n회복이 어렵기 때문에\n예방이 무엇보다 중요합니다.\n\n녹용에 함유된 강글리오사이드와\n뇌유래신경영양인자(BDNF)는\n뇌세포 보호와 인지 기능 유지에\n도움을 주는 것으로 알려져 있습니다.\n\n수壽는 최상급 녹용을 엄선하여\n두뇌 건강을 위한 최선의 선택을 제공합니다.",
    "녹용": "녹용은 예로부터\n기력 회복과 성장에 도움을 주는\n대표적인 보양 약재입니다.\n\n녹용에는 콘드로이틴, 콜라겐,\n강글리오사이드 등 유효 성분이 풍부하여\n면역력 강화와 체력 증진에 탁월합니다.\n\n수壽는 뉴질랜드산 최상급 녹용만을\n엄선하여 사용합니다.",
    "공진단": "1196년부터 시작된 공진단의 역사,\n단순한 한약을 넘어 조선의 왕들이 아끼고 사랑했던\n'왕실 대표 보약'입니다.\n\n사향, 녹용, 당귀, 산수유 등\n귀한 약재의 조합이\n기력 회복과 면역력 강화에 탁월합니다.\n\n수壽가 그 전통을 이어갑니다.",
    "경옥고": "경옥고는 생지황, 인삼, 백복령, 꿀로\n구성된 전통 보양 처방입니다.\n\n폐와 호흡기 건강을 돕고\n면역력을 강화하는 데\n탁월한 효과가 있습니다.\n\n수壽의 경옥고는 전통 처방 그대로\n정성을 다해 조제합니다.",
    "활력": "하루의 활력은\n좋은 원료에서 시작됩니다.\n\n만성 피로와 무기력함은\n단순한 휴식만으로는 해결되지 않습니다.\n근본적인 기력 보충이 필요합니다.\n\n수壽와 함께 활기찬 일상을 만들어보세요.",
    "걷기": "걷기는 누구나 할 수 있는\n가장 기본적인 운동이지만\n잘못된 보행 습관은 오히려\n무릎과 허리에 부담을 줄 수 있습니다.\n\n신발 밑창이 한쪽으로 닳거나\n걷고 난 후 통증이 있다면\n보행 자세를 점검해 볼 필요가 있습니다.\n\n올바른 걷기 습관과 함께\n관절과 근골격 건강을 챙기세요.\n수壽가 건강한 걸음을 응원합니다.",
    "운동": "운동은 면역력을 높이고\n체력을 유지하는 가장 확실한 방법입니다.\n\n무리한 운동보다는\n자신에게 맞는 강도와 빈도로\n꾸준히 실천하는 것이 중요합니다.\n\n운동 후 회복이 느리다면\n한약으로 기력을 보충해 보세요.\n수壽가 건강한 운동 생활을 함께합니다.",
    "우황청심원": "조선을 방문한 명나라 사신들에게는\n반드시 챙겨 가는 목록이 있었습니다.\n바로 우황청심원이었습니다.\n\n조선왕조실록에는\n「사신이 우황청심원을 요청했다」는\n기록이 여러 차례 등장합니다.\n왕공부터 귀인까지 앞다투어 구하려 했죠.\n\n✔️ 우황청심원의 원조는 1107년 송나라 처방\n✔️ 조선의 우황 품질이 압도적이어서 역수입\n✔️ 같은 처방이라도 원료에 따라 약의 수준이 완전히 달라짐\n\n600년의 시간이 증명한 가치,\n수壽가 변함없이 이어가고 있습니다.\n\n🔍내용출처 | 조선왕조실록, 승정원일기",
}

# 키워드 폴백용 (주제 감지 실패 시)
_FALLBACK_HOOKS = {
    "정보성": ["{kw}의 차이를 만드는 기술력", "{kw}, 안전함이 기본입니다", "{kw}의 핵심은 원료에 있습니다"],
    "감성": ["당신의 건강을 지키는 한 알의 정성", "{kw}로 시작하는 건강한 일상", "건강한 내일을 위한 오늘의 선택"],
    "유머": ["{kw} 안 하면 손해인 거 아시죠?", "아직도 {kw}의 진가를 모르신다면"],
    "전문적": ["근거 기반 {kw} 가이드", "{kw}: 최신 연구가 말하는 효과와 방법"],
}
_FALLBACK_BODIES = [
    "{kw1}은(는) 예로부터\n기력 보충과 면역력 강화에\n탁월한 효과가 있는 것으로 알려져 있습니다.\n\n수壽는 최상급 원료만을 엄선하여\n그 효과를 더욱 높였습니다.",
    "바쁜 일상 속에서도\n건강을 놓치지 않는 방법.\n\n{kw1}으로 하루를 시작하면\n몸이 먼저 변화를 느낍니다.\n\n수壽가 그 첫걸음을 함께합니다.",
]


# ── 주제 감지 ──

def _detect_topics_from_raw(raw_texts):
    """OCR 원본 텍스트에서 주제를 감지합니다."""
    all_text = " ".join(raw_texts).lower()
    all_text_clean = re.sub(r"[^가-힣a-zA-Z0-9\s]", " ", all_text)

    topic_scores = {}
    for topic, keywords in _TOPIC_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw.lower() in all_text_clean)
        if count > 0:
            topic_scores[topic] = count
    return sorted(topic_scores.items(), key=lambda x: -x[1])


# ── AI 동적 캡션 생성 ──

_AI_PROMPT = """당신은 한의 브랜드 '수壽(thesoo.co)'의 전문 콘텐츠 마케터입니다.
카드뉴스 이미지 텍스트를 분석하고, 해당 주제에 대한 의학 논문·보도자료·전문 기사의 내용을 참고하여 인스타그램 캡션을 작성합니다.

## 수壽 브랜드 가이드
- 반드시 한국어(한글)로만 작성. 한자·일본어·중국어 절대 금지 (브랜드명 수壽 제외, 동의보감 인용문 내 한자는 허용)
- 수壽 제품: 공진단(기력 회복, 기억력, 수면, 간건강), 경옥고(호흡기·면역·피로회복), 녹용(뇌건강·보양)

## 캡션 작성 스타일 (실제 수壽 계정 분석 기반)

### 헤드라인(hook)
- 질문형 또는 흥미 유발 문장 (15~25자)
- 예: "알고 계셨나요?", "~하진 않나요?", "~무엇이었을까요?"
- 구체적인 숫자·사실을 활용하면 효과적 (예: "83세까지 장수한 영조가 251번이나 처방받은 약은?")

### 본문(body) 구조
1. **도입**: 독자의 공감을 끌어내는 대화체 질문 또는 상황 설명 (1~2문장, "~적이 있나요?", "~않으세요?")
2. **핵심 정보**: 주제에 대한 구체적 사실·효능 설명 (2~3문장)
3. **체크리스트**: ✔️ 기호로 핵심 포인트 3~4개 나열 (각 항목 1줄, 구체적 수치·효과 포함)
4. **마무리**: 수壽 제품과 자연스러운 연결 (1문장)
5. **출처**: "🔍내용출처 | [출처명]" 형식으로 논문·기관명 기재

### 어투·표현
- 친근하고 대화하는 듯한 존댓말 ("~인데요,", "~하죠.", "~알려드릴게요", "~해보세요")
- 문단 사이는 빈 줄(\\n\\n)로 구분
- 허용 기호: ✔️(체크리스트), 🔍(출처), ℹ️(정보)만 사용. 그 외 이모지 금지
- 구체적인 숫자, 연구 결과, 역사적 사실을 적극 활용
- 동의보감 인용 시 ⌜⌟ 괄호 사용 가능

### 필수 품질 규칙
- 모든 문장은 주어·서술어가 완전해야 함. 조사("부터", "에서" 등)로 시작하는 불완전 문장 절대 금지
- 체크리스트(✔️) 항목은 반드시 카드뉴스 이미지에 나온 구체적 내용을 기반으로 작성. 일반론 금지
- 맞춤법·띄어쓰기 정확하게. "송나라"를 "송나래"로 쓰는 등의 오타 절대 금지
- 이미지에 없는 효능이나 사실을 지어내지 말 것

## 실제 캡션 예시

### 예시 1 (제품/공진단)
hook: "기억력과 학습 능력을 2배로 높이는 비결"
body: "바쁜 현대 생활 속에서 집중력과 기억력이 떨어지는 걸 느낀 적이 있나요? 예로부터 건강과 활력을 위한 명약으로 알려진 공진단이, 2016년 과학적 연구를 통해 기억력 개선에도 탁월한 효과가 있다는 사실이 밝혀졌습니다. 그럼 공진단이 어떤 효과를 발휘하는지 함께 알아볼까요?\\n\\n✔️억제된 뇌의 학습과 기억력을 2배 이상 향상\\n✔️알츠하이머 치매약과 유사한 효능\\n✔️뇌 신경 영양인자 증가\\n\\n공진단으로 당신의 기억력을 한 단계 업그레이드하세요.\\n\\n🔍내용출처 | LEE, Jin-Seok, et al. PLoS One, 2016, 11.8: e0159823."

### 예시 2 (약재 스토리/역사)
hook: "83세까지 장수한 영조가 251번이나 처방받은 약은?"
body: "조선시대 임금들의 평균 수명보다 두 배 가까운 83세까지 장수한 영조. 그의 건강 비결은 무엇이었을까요?\\n\\n세계기록유산으로 등재된 승정원일기에는 무려 358번 등장하는 약이 있습니다. 그중 251번이 영조에게 처방된 바로 그 약, '경옥고'입니다.\\n\\n경옥고는 '아름다운 구슬 같은 고약'이라는 뜻으로, 동의보감에서 수명을 늘리는 명약으로 꼽히며 구암 허준이 중요하게 여긴 보약입니다.\\n\\n인삼, 생지황, 복령, 꿀 등의 재료로 조제되며, 현대에는 녹용을 더해 효능을 업그레이드 했습니다. 여러분도 영조의 건강 비결을 경험해 보세요."

### 예시 3 (건강 정보/체크리스트)
hook: "한약에 왜 금박을 입혀요?"
body: "공진단과 우황청심원은 왜 반짝이는 금박으로 싸여 있을까요? 단순히 보기 좋은 장식이 아니라, 그 이상의 비밀이 숨겨져 있습니다.\\n\\n한의학 의서에서는 금박이 혈액순환을 돕고, 정신적 안정을 촉진한다고 설명합니다. 그래서 신경 불안, 두통 등의 질환에 자주 사용되었죠.\\n\\n✔️ 동의보감에 따르면, 금박은 심화를 진정시켜 마음을 안정시키는 효과가 있다고 기록되어 있습니다.\\n✔️ 방약합편에서는 우황청심원처럼 신경을 안정시키는 처방에 금박을 사용하여 정신적 불안을 해소한다고 합니다.\\n✔️ 또한, 사향과 같은 방향성 약재의 효능을 오래 유지하는 역할도 합니다.\\n\\n공진단의 깊은 효능을 금박과 함께 더 오래, 더 건강하게 느껴보세요.\\n\\n📚참고문헌 | 동의보감, 방약합편"

## 출력 형식
반드시 아래 JSON만 출력하세요. 다른 텍스트 없이.
{"hook": "헤드라인 (질문형 또는 흥미 유발, 15~25자)", "body": "본문 (도입→핵심정보→체크리스트→마무리→출처, \\n\\n 문단구분)"}"""


def _make_cache_key(image_texts, tone):
    """이미지 텍스트 + 톤 조합의 캐시 키 생성."""
    raw = f"{tone}:{'|'.join(image_texts[:10])}"
    return hashlib.md5(raw.encode()).hexdigest()


def _build_user_prompt(image_texts, topics, tone):
    """AI에 전달할 사용자 프롬프트를 구성합니다."""
    ocr_summary = "\n".join(image_texts[:15])
    topic_str = ", ".join(t[0] for t in topics[:3]) if topics else "건강 일반"
    return (
        f"## 카드뉴스 이미지 텍스트 (OCR 추출, 노이즈 포함)\n"
        f"{ocr_summary}\n\n"
        f"## 감지된 주제\n{topic_str}\n\n"
        f"## 캡션 톤\n{tone}\n\n"
        f"위 이미지 텍스트의 핵심 내용을 파악하고, "
        f"해당 주제에 대한 의학 논문·보도자료·전문 기사 내용을 참고하여 "
        f"수壽 브랜드 스타일의 인스타그램 캡션을 작성하세요.\n\n"
        f"중요 규칙:\n"
        f"- 이미지에 나온 구체적인 내용(숫자, 사실, 스토리)을 캡션에 자연스럽게 녹여내세요.\n"
        f"- ✔️ 체크리스트 항목은 이미지의 핵심 정보를 요약해야 합니다. 일반론 금지.\n"
        f"- 모든 문장은 주어와 서술어가 완전해야 합니다. 불완전한 문장 금지.\n"
        f"- 맞춤법·띄어쓰기를 정확하게 작성하세요.\n"
        f"- OCR 노이즈(깨진 글자, 미완성 문장)는 절대 포함하지 마세요."
    )


# 한자 → 한글 치환 맵 (LLM이 간혹 섞어 쓰는 한자)
_CJK_REPLACEMENTS = {
    "決定": "결정", "重要": "중요", "健康": "건강", "效果": "효과",
    "生活": "생활", "必要": "필요", "治療": "치료", "症狀": "증상",
    "運動": "운동", "免疫": "면역", "改善": "개선", "强化": "강화",
    "增進": "증진", "回復": "회복", "疲勞": "피로", "管理": "관리",
}


_TYPO_FIXES = {
    "송나래": "송나라", "고려래": "고려라", "조선래": "조선라",
    "처방입니 다": "처방입니다", "있습니 다": "있습니다",
}


def _sanitize_korean(text):
    """LLM 출력에서 한자·비한글 문자를 제거하고 흔한 오타를 수정합니다."""
    for cjk, kor in _CJK_REPLACEMENTS.items():
        text = text.replace(cjk, kor)
    # 남은 CJK 한자(壽 제외) 제거
    text = re.sub(r"(?<!수)[一-龥]", "", text)
    # 흔한 오타 수정
    for typo, fix in _TYPO_FIXES.items():
        text = text.replace(typo, fix)
    # 조사 없이 시작하는 불완전 문장 제거 (". 부터", ". 에서" 등)
    text = re.sub(r"(?<=\. )(부터|에서|까지|으로|와|과|의) ", "", text)
    return text


def _parse_ai_response(text):
    """AI 응답에서 hook/body JSON을 파싱합니다."""
    text = text.strip()
    if "```" in text:
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = text.replace("```", "").strip()

    data = json.loads(text)
    hook = _sanitize_korean(data.get("hook", "").strip())
    body = _sanitize_korean(data.get("body", "").strip())

    if not hook or not body:
        return None
    return {"hook": hook, "body": body}


def _generate_with_groq(image_texts, topics, tone="정보성"):
    """Groq API (무료, Llama 3.3 70B)로 캡션을 생성합니다.
    추가 패키지 없이 requests만으로 호출합니다.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    cache_key = _make_cache_key(image_texts, tone)
    if cache_key in _caption_cache:
        logger.info("캡션 캐시 히트 (Groq)")
        return _caption_cache[cache_key]

    user_prompt = _build_user_prompt(image_texts, topics, tone)

    try:
        resp = _requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": _AI_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 1000,
                "temperature": 0.5,
            },
            timeout=20,
        )
        resp.raise_for_status()

        text = resp.json()["choices"][0]["message"]["content"]
        result = _parse_ai_response(text)
        if result:
            _caption_cache[cache_key] = result
            logger.info(f"Groq 캡션 생성 성공: {result['hook'][:20]}...")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Groq 응답 JSON 파싱 실패: {e}")
        return None
    except Exception as e:
        logger.warning(f"Groq API 호출 실패: {e}")
        return None


def _generate_with_claude(image_texts, topics, tone="정보성"):
    """Anthropic Claude API로 캡션을 생성합니다."""
    if not _anthropic_available:
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    cache_key = _make_cache_key(image_texts, tone)
    if cache_key in _caption_cache:
        logger.info("캡션 캐시 히트 (Claude)")
        return _caption_cache[cache_key]

    user_prompt = _build_user_prompt(image_texts, topics, tone)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=_AI_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        result = _parse_ai_response(response.content[0].text)
        if result:
            _caption_cache[cache_key] = result
            logger.info(f"Claude 캡션 생성 성공: {result['hook'][:20]}...")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Claude 응답 JSON 파싱 실패: {e}")
        return None
    except Exception as e:
        logger.warning(f"Claude API 호출 실패: {e}")
        return None


def _generate_with_ai(image_texts, topics, tone="정보성"):
    """AI API로 캡션을 생성합니다. Groq(무료) → Claude 순서로 시도."""
    # 1순위: Groq (무료, Llama 3.3 70B)
    result = _generate_with_groq(image_texts, topics, tone)
    if result:
        return result

    # 2순위: Claude
    result = _generate_with_claude(image_texts, topics, tone)
    if result:
        return result

    logger.info("AI API 사용 불가 → 정적 라이브러리 폴백")
    return None


# ── 캡션 조합 ──

def _build_hashtags(top_hashtags, topics=None):
    """주제와 인사이트를 기반으로 해시태그 리스트를 구성합니다."""
    tags = list(_BRAND_HASHTAGS)
    if topics:
        for topic, _ in topics[:2]:
            tag = _TOPIC_HASHTAG_MAP.get(topic)
            if tag and tag not in tags and len(tags) < 5:
                tags.append(tag)
    if top_hashtags:
        for t in top_hashtags:
            tag = t.lstrip("#")
            if tag not in tags and len(tags) < 5:
                tags.append(tag)
    for t in _EXTRA_HASHTAGS:
        if t not in tags and len(tags) < 5:
            tags.append(t)
    return tags


def _assemble_caption(hook, body, top_hashtags=None, topics=None):
    """수壽 스타일로 캡션을 조합합니다."""
    cta = random.choice(_CTAS)
    caption = f"{hook}\n\n{body} \n\n{cta} \n\n{_SIGNATURE}"
    tags = _build_hashtags(top_hashtags, topics)
    hashtags = " ".join(f"#{t}" for t in tags)
    full = f"{caption}\n\n{hashtags}"
    return {"caption": caption, "hashtags": hashtags, "full": full}


# ── 정적 라이브러리 폴백 ──

def _build_from_static_library(topics, top_hashtags=None):
    """정적 라이브러리에서 주제 매칭 캡션을 생성합니다 (API 폴백용)."""
    if not topics:
        return None

    main_topic = topics[0][0]
    hook_options = _STATIC_HOOKS.get(main_topic)
    body = _STATIC_BODIES.get(main_topic)

    if not hook_options or not body:
        return None

    hook = random.choice(hook_options)
    return _assemble_caption(hook, body, top_hashtags, topics)


# ── 메인 함수 ──

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

    캡션 생성 플로우:
    1. 이미지 텍스트(OCR) → 주제 감지
    2. AI API(Gemini/Claude)로 주제 관련 논문·기사 기반 캡션 생성
    3. API 실패 시 → 정적 라이브러리 폴백
    4. 라이브러리에도 없으면 → 키워드 템플릿 폴백
    """
    topics = []

    if image_texts:
        # Step 1: 이미지 내용에서 주제 감지
        topics = _detect_topics_from_raw(image_texts)
        logger.info(f"감지된 주제: {topics[:3]}")

        # Step 2: AI API로 논문·기사 기반 동적 캡션 생성 (Gemini → Claude)
        ai_result = _generate_with_ai(image_texts, topics, tone)
        if ai_result:
            return _assemble_caption(
                ai_result["hook"],
                ai_result["body"],
                top_hashtags,
                topics,
            )

        # Step 3: 정적 라이브러리 폴백
        static_result = _build_from_static_library(topics, top_hashtags)
        if static_result:
            logger.info("정적 라이브러리 폴백 사용")
            return static_result

    # Step 4: 키워드 템플릿 폴백 (이미지 텍스트 없거나 모든 단계 실패)
    keywords = list(top_keywords) if top_keywords else []
    if not keywords:
        keywords = ["공진단", "경옥고", "한약", "면역력", "활력"]
    random.shuffle(keywords)
    kw1 = keywords[0]

    hooks = _FALLBACK_HOOKS.get(tone, _FALLBACK_HOOKS["정보성"])
    hook = random.choice(hooks).format(kw=kw1)
    body = random.choice(_FALLBACK_BODIES).format(kw1=kw1)

    return _assemble_caption(hook, body, top_hashtags, topics)
