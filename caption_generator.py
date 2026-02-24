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

# ── 주제 감지 매핑 ──
# OCR 텍스트의 키워드 → 주제 카테고리 매핑
_TOPIC_KEYWORDS = {
    "뇌건강": ["뇌", "BDNF", "강글리오사이드", "신경", "뇌세포", "인지", "기억력", "집중력", "두뇌", "치매"],
    "녹용": ["녹용", "녹각", "사슴", "뿔", "성장인자", "IGF"],
    "공진단": ["공진단", "사향", "당귀", "산수유", "녹용", "왕실", "보약", "기력"],
    "경옥고": ["경옥고", "생지황", "인삼", "백복령", "꿀", "폐", "호흡기", "면역"],
    "면역력": ["면역", "면역력", "항체", "백혈구", "감기", "바이러스", "방어"],
    "활력": ["활력", "피로", "에너지", "기력", "체력", "원기", "보양", "기운"],
    "품질": ["hGMP", "GMP", "인증", "검사", "품질", "살균", "세척", "위생", "안전"],
    "원료": ["원료", "약재", "한약재", "산지", "지리산", "구례", "의성", "엄선"],
    "기술력": ["초미립자", "분쇄", "입자", "흡수율", "기술", "균일", "미세"],
    "전통": ["동의보감", "전통", "처방", "역사", "왕", "조선", "1196년", "800년"],
    "수면": ["수면", "숙면", "불면", "잠", "밤", "멜라토닌", "수면질"],
    "혈액순환": ["혈액", "순환", "혈관", "혈류", "혈행", "어혈"],
    "소화": ["소화", "위장", "장", "소화기", "위", "장건강"],
    "피부": ["피부", "콜라겐", "노화", "안티에이징", "탄력", "윤기"],
    "다이어트": ["다이어트", "체중", "체지방", "대사", "신진대사"],
}

# 주제별 보조 본문 (OCR 텍스트가 부족할 때 보충)
_TOPIC_SUPPLEMENTS = {
    "뇌건강": "뇌 건강은 예방이 가장 중요합니다.\n수壽는 과학적 근거에 기반한 원료로\n두뇌 활력을 지원합니다.",
    "녹용": "녹용은 예로부터\n기력 회복과 성장에 도움을 주는\n대표적인 보양 약재입니다.",
    "공진단": "공진단은 800년 역사의 왕실 대표 보약으로\n기력 보충과 면역력 강화에\n탁월한 효과가 있습니다.",
    "경옥고": "경옥고는 폐와 호흡기 건강을 돕고\n면역력을 강화하는\n전통 명약입니다.",
    "면역력": "면역력은 건강의 가장 기본적인 토대입니다.\n수壽는 자연의 원료로\n몸의 방어력을 높입니다.",
    "활력": "하루의 활력은\n좋은 원료에서 시작됩니다.\n수壽와 함께 활기찬 일상을 만들어보세요.",
    "품질": "수壽는 hGMP 인증 시설에서\n엄격한 품질 관리 하에\n안전한 한약을 조제합니다.",
    "원료": "전국 각지에서 엄선된 최상급 한약재로\n한의사가 직접 관리하며\n믿을 수 있는 한약을 만듭니다.",
    "기술력": "초미립자 균일 분쇄 기술로\n체내 흡수율을 극대화하고\n부드러운 목 넘김을 실현했습니다.",
    "전통": "동의보감에 기록된 전통 처방을 바탕으로\n현대 과학 기술을 접목하여\n최고의 효과를 추구합니다.",
}

# 주제별 헤드라인 (OCR에서 적절한 헤드라인을 못 찾았을 때)
_TOPIC_HOOKS = {
    "뇌건강": ["두뇌 활력의 비밀", "뇌 건강, 지금부터 준비하세요", "건강한 뇌를 위한 선택"],
    "녹용": ["자연이 선물한 최고의 보양", "녹용의 진가를 경험하세요", "녹용, 그 특별한 효능"],
    "공진단": ["800년 역사가 증명하는 효능", "왕실의 보약, 공진단", "기력 충전의 시작"],
    "경옥고": ["호흡기 건강의 든든한 파트너", "면역력의 기본, 경옥고", "자연의 면역 강화제"],
    "면역력": ["면역력이 곧 건강입니다", "몸의 방어력을 높이는 방법", "건강의 첫 번째 조건"],
    "활력": ["오늘의 활력을 위한 선택", "지치지 않는 하루의 비결", "활력 넘치는 일상을 위해"],
    "품질": ["자신있게 권할 수 있는 퀄리티", "안전함이 기본입니다", "한 번 더 검증하는 품질 관리"],
    "원료": ["원료의 차이가 결과의 차이", "자연 그대로, 깨끗하게", "엄선된 약재의 힘"],
    "기술력": ["일반 분말보다 15배 더 미세하게", "기술력이 만드는 차이", "과학이 증명하는 효과"],
    "전통": ["전통과 현대의 융합", "800년이라는 시간 그 이상의 가치", "동의보감의 지혜를 잇다"],
}

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


def _is_complete_sentence(line):
    """문장이 완결된 형태인지 판단합니다. 미완성 문장을 걸러냅니다."""
    line = line.strip()

    # ── 앞부분이 잘린 미완성 문장 감지 ──
    # 어미/조사로 시작하는 문장 (예: "요할까요?", "는 것입니다", "을 수 있습니다")
    if re.match(r"^(요|죠|고|며|면서|지만|거나|든지|라서|니까|으니|ㄹ까|할까|할지)", line):
        return False
    # 연결어미로 시작 (앞 문장에서 잘림)
    if re.match(r"^(하는|되는|있는|없는|같은|라는|라고|다고|으로|에서|부터|까지|처럼|만큼)", line) and len(line) < 15:
        return False

    # ── 뒷부분이 잘린 미완성 문장 감지 ──
    # 따옴표/괄호가 열리고 닫히지 않음 (예: "돌리는 '골든")
    open_quotes = line.count("'") + line.count("'") + line.count('"') + line.count('"')
    close_quotes = line.count("'") + line.count("'") + line.count('"') + line.count('"')
    if line.count("'") % 2 != 0 and not line.endswith((".", "!", "?", "다", "요")):
        return False
    if line.count("(") > line.count(")"):
        return False

    # 관형형/체언으로 끝나는 짧은 문장 (뒷부분 잘림, 예: "돌리는", "만드는", "골든")
    if re.search(r"[는은인된할든른]$", line) and len(line) < 15:
        return False

    # 짧은 문장인데 문장 종결이 아닌 경우 (미완성 가능성 높음)
    if len(line) < 15 and not re.search(r"[.다요!?세죠음임]$", line):
        return False

    # ── 무의미한 노이즈 패턴 ──
    # 숫자+짧은 단어 조합 (예: "그 030 그저", "에 12 다")
    words = line.split()
    if len(words) <= 3:
        num_noise = sum(1 for w in words if re.match(r"^\d+$", w) or len(w) <= 1)
        if num_noise >= 2:
            return False

    # 한 글자 단어가 과반수인 경우 (OCR 파편)
    if len(words) >= 2:
        single_char_words = sum(1 for w in words if len(w) == 1)
        if single_char_words / len(words) > 0.5:
            return False

    # 의미 없는 반복 패턴 (예: "ㅡㅡㅡ", "......")
    if re.match(r"^[.\-ㅡ~·_=]{3,}", line):
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
    """OCR 원본 텍스트를 정제하여 의미 있는 완결된 문장만 반환합니다."""
    cleaned = []
    seen = set()

    # 시그니처/CTA와 유사한 텍스트 필터 패턴
    signature_patterns = [
        "thesoo", "@thesoo", "수壽", "수수",
        "공식 홈페이지", "프로필 링크", "프로필링크",
        "더 오래", "더 건강하게", "한의사가", "하의사가",
        "한의 브랜드", "official",
    ]

    for raw in raw_texts:
        # 줄 단위로 분리하여 처리
        for line in raw.split("\n"):
            line = _clean_ocr_line(line)
            if not _is_valid_line(line):
                continue
            # 미완성 문장 필터
            if not _is_complete_sentence(line):
                continue
            # URL, 브랜드명, 시그니처/CTA 중복 제외
            lower = line.lower()
            if any(skip in lower for skip in signature_patterns):
                continue
            if line not in seen:
                seen.add(line)
                cleaned.append(line)

    return cleaned


def _detect_topics(texts):
    """텍스트에서 주제를 감지합니다. [(주제, 매칭수)] 리스트를 반환합니다."""
    all_text = " ".join(texts).lower()
    topic_scores = {}
    for topic, keywords in _TOPIC_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw.lower() in all_text)
        if count > 0:
            topic_scores[topic] = count
    # 매칭수 기준 내림차순 정렬
    return sorted(topic_scores.items(), key=lambda x: -x[1])


def _score_sentence(line):
    """문장의 캡션 적합도를 점수로 평가합니다 (0~100)."""
    score = 50  # 기본 점수

    # 길이 보정: 너무 짧거나 너무 긴 문장 감점
    length = len(line)
    if length < 8:
        score -= 20
    elif length < 15:
        score -= 5
    elif 15 <= length <= 50:
        score += 10  # 적절한 길이
    elif length > 80:
        score -= 10

    # 완결성: 마침표/물음표/느낌표로 끝나면 가산
    if re.search(r"[.다요!?]$", line):
        score += 15

    # 숫자/데이터 포함: 구체적인 정보 (예: "15배", "800년")
    if re.search(r"\d+", line):
        score += 10

    # 한글 비율 높으면 가산
    korean_ratio = len(re.findall(r"[가-힣]", line)) / max(len(line), 1)
    if korean_ratio > 0.6:
        score += 10
    elif korean_ratio < 0.3:
        score -= 15

    # 의미 있는 서술어 포함
    if re.search(r"(합니다|됩니다|있습니다|입니다|드립니다|보세요|하세요|습니다)", line):
        score += 10

    # 질문형 문장은 헤드라인 후보로 가치 높음
    if "?" in line or line.endswith("까요") or line.endswith("나요"):
        score += 15

    # 핵심 키워드 포함 시 가산
    health_keywords = ["건강", "효과", "면역", "활력", "기력", "보양", "원료", "품질", "안전", "전통"]
    for kw in health_keywords:
        if kw in line:
            score += 5
            break

    # OCR 노이즈 잔재 감점
    if re.search(r"[ㅋㅎㅠㅜㅡ]{2,}", line):
        score -= 30
    if re.search(r"[A-Z]{5,}", line) and not re.search(r"(BDNF|IGF|GMP|hGMP|CITES)", line):
        score -= 20

    # 미완성 문장 추가 감점
    # 주어 없이 서술어로만 시작하는 짧은 문장
    if re.match(r"^(요|죠|고|며|라서|니까)", line):
        score -= 40
    # 명사/관형형으로 끝나는 짧은 문장 (뒷부분 잘림)
    if len(line) < 15 and re.search(r"[는은인된할의]$", line):
        score -= 20
    # 의미 없는 짧은 단어 나열
    words = line.split()
    if len(words) >= 2 and all(len(w) <= 2 for w in words):
        score -= 30

    return max(0, min(100, score))


def _select_hook(sentences, topics):
    """가장 적합한 헤드라인을 선택합니다."""
    if not sentences:
        return None

    # 1순위: 짧고 임팩트 있는 문장 (10~30자, 질문 또는 강한 서술)
    hook_candidates = []
    for s in sentences:
        score = _score_sentence(s)
        length = len(s)
        # 헤드라인 적합도 보정
        if 8 <= length <= 35:
            score += 20  # 헤드라인에 적합한 길이
        if s.endswith(("?", "까요", "나요")):
            score += 15  # 질문형은 후킹 효과 높음
        if re.search(r"\d+배|\d+년|\d+%", s):
            score += 10  # 숫자가 있으면 임팩트
        # 너무 긴 문장은 헤드라인 부적합
        if length > 40:
            score -= 25
        hook_candidates.append((s, score))

    hook_candidates.sort(key=lambda x: -x[1])

    # 상위 후보 중 랜덤 선택 (다양성 확보)
    top = hook_candidates[:3]
    return top[0][0] if top else sentences[0]


def _build_body(sentences, hook, topics):
    """헤드라인을 제외한 문장들로 본문을 구성합니다."""
    # 헤드라인으로 사용된 문장 제외
    body_candidates = [s for s in sentences if s != hook]

    if not body_candidates:
        # OCR 문장이 부족하면 주제 기반 보조 본문 사용
        if topics:
            main_topic = topics[0][0]
            supplement = _TOPIC_SUPPLEMENTS.get(main_topic)
            if supplement:
                return supplement
        return None

    # 문장 스코어 기준으로 정렬, 상위 6개까지 선택
    scored = [(s, _score_sentence(s)) for s in body_candidates]
    scored.sort(key=lambda x: -x[1])
    selected = [s for s, sc in scored[:6] if sc >= 30]

    if not selected:
        selected = body_candidates[:4]

    # 단락 구성: 2~3 문장씩 묶어서 단락 구분
    paragraphs = []
    current = []
    for line in selected:
        current.append(line)
        # 문장 종결 또는 2~3줄 모이면 단락 구분
        if (re.search(r"[.다요!?]$", line) and len(current) >= 2) or len(current) >= 3:
            paragraphs.append("\n".join(current))
            current = []
    if current:
        paragraphs.append("\n".join(current))

    body = "\n\n".join(paragraphs)

    # 본문이 너무 짧으면 주제 보조 본문 추가
    if len(body) < 30 and topics:
        main_topic = topics[0][0]
        supplement = _TOPIC_SUPPLEMENTS.get(main_topic)
        if supplement:
            body = f"{body}\n\n{supplement}"

    return body


def _build_from_image_texts(image_texts, top_hashtags=None, tone="정보성"):
    """이미지에서 추출한 텍스트를 분석·추론하여 수壽 스타일 캡션을 생성합니다.

    처리 흐름:
    1. OCR 텍스트 정제
    2. 주제 감지 (키워드 매핑)
    3. 문장 스코어링 (캡션 적합도 평가)
    4. 헤드라인 자동 선택 (임팩트 + 길이 최적화)
    5. 본문 재구성 (스코어 기반 문장 선별 + 단락 구성)
    6. 주제 기반 보충 (OCR 텍스트 부족 시)
    """
    cleaned = _clean_ocr_texts(image_texts)

    if len(cleaned) < 2:
        # OCR 문장이 1개 이하면 주제라도 감지하여 템플릿 모드로 전환
        if cleaned:
            topics = _detect_topics(cleaned)
            if topics:
                main_topic = topics[0][0]
                hook_options = _TOPIC_HOOKS.get(main_topic)
                supplement = _TOPIC_SUPPLEMENTS.get(main_topic)
                if hook_options and supplement:
                    hook = random.choice(hook_options)
                    body = f"{cleaned[0]}\n\n{supplement}"
                    cta = random.choice(_CTAS)
                    caption = f"{hook}\n\n{body} \n\n{cta} \n\n{_SIGNATURE}"
                    tags = _build_hashtags(top_hashtags, topics)
                    hashtags = " ".join(f"#{t}" for t in tags)
                    return {"caption": caption, "hashtags": hashtags, "full": f"{caption}\n\n{hashtags}"}
        return None

    # ① 주제 감지
    topics = _detect_topics(cleaned)
    main_topic = topics[0][0] if topics else None
    logger.info(f"감지된 주제: {topics[:3]}")

    # ② 헤드라인 선택
    hook = _select_hook(cleaned, topics)

    # 적합한 헤드라인이 없으면 주제 기반 헤드라인 사용
    if hook and _score_sentence(hook) < 40 and main_topic:
        topic_hooks = _TOPIC_HOOKS.get(main_topic)
        if topic_hooks:
            hook = random.choice(topic_hooks)

    # ③ 본문 구성
    body = _build_body(cleaned, hook, topics)

    if not body:
        return None

    # ④ CTA
    cta = random.choice(_CTAS)

    # ⑤ 조합 (수壽 스타일)
    caption = f"{hook}\n\n{body} \n\n{cta} \n\n{_SIGNATURE}"

    # ⑥ 해시태그 (주제 기반 확장)
    tags = _build_hashtags(top_hashtags, topics)
    hashtags = " ".join(f"#{t}" for t in tags)

    full = f"{caption}\n\n{hashtags}"
    return {"caption": caption, "hashtags": hashtags, "full": full}


def _build_hashtags(top_hashtags, topics=None):
    """주제와 인사이트를 기반으로 해시태그 리스트를 구성합니다."""
    tags = list(_BRAND_HASHTAGS)

    # 주제 기반 해시태그 추가
    topic_hashtag_map = {
        "뇌건강": "뇌건강",
        "녹용": "녹용",
        "공진단": "공진단",
        "경옥고": "경옥고",
        "면역력": "면역력",
        "활력": "활력충전",
        "품질": "한약품질",
        "기술력": "한약기술",
        "전통": "전통한약",
    }
    if topics:
        for topic, _ in topics[:2]:
            tag = topic_hashtag_map.get(topic)
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
    tags = _build_hashtags(top_hashtags)
    hashtags = " ".join(f"#{t}" for t in tags)

    full = f"{caption}\n\n{hashtags}"
    return {"caption": caption, "hashtags": hashtags, "full": full}
