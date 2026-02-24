"""10-에이전트 경쟁 카드뉴스 스크립트 생성 모듈

5개 전문 에이전트가 각 2개 아이디어 = 10개 아이디어 생성 후
5개 기준 경쟁 평가 → Top 2 선정 → 풀 스크립트 + 이미지 프롬프트 + Description Mention 생성
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests as _requests

logger = logging.getLogger(__name__)

# ── 히스토리 파일 ──
HISTORY_FILE = Path(__file__).parent / "cardnews_history.json"

# ── 에이전트 정의 ──
AGENTS = [
    {
        "id": "season",
        "name": "계절건강 에이전트",
        "domain": "계절·절기·기후 변화에 따른 건강 관리",
        "search_hint": "계절 건강 트렌드, 절기별 양생법, 기후 변화와 건강",
    },
    {
        "id": "social",
        "name": "소셜트렌드 에이전트",
        "domain": "SNS 건강 트렌드, 바이럴 이슈, MZ세대 건강 관심사",
        "search_hint": "건강 바이럴 트렌드, MZ 건강 SNS, 건강 밈",
    },
    {
        "id": "history",
        "name": "역사건강 에이전트",
        "domain": "역사 인물·사건과 한의학 연결, 동의보감, 궁중 비방",
        "search_hint": "한의학 역사, 동의보감 SNS 인기, 조선 건강 이야기",
    },
    {
        "id": "women",
        "name": "여성건강 에이전트",
        "domain": "여성 건강, 호르몬, 이너뷰티, 갱년기, 산후 관리",
        "search_hint": "여성 건강 트렌드, 이너뷰티, 갱년기 관리",
    },
    {
        "id": "worker",
        "name": "직장인건강 에이전트",
        "domain": "직장인 피로, 번아웃, 수면 부족, 사무직 건강 문제",
        "search_hint": "직장인 건강 트렌드, 번아웃 수면, 사무직 건강",
    },
]

# ── 카테고리 ──
CATEGORIES = [
    {"id": "korean_medicine", "name": "한의학 지식", "desc": "전통 한의학 이론, 처방, 경락, 체질"},
    {"id": "historical_story", "name": "역사 스토리텔링", "desc": "역사 인물 에피소드, 궁중 비방"},
    {"id": "health_tips", "name": "건강 상식", "desc": "현대인 실용 건강 정보"},
    {"id": "seasonal_health", "name": "계절 건강", "desc": "24절기, 계절별 건강 관리"},
    {"id": "food_medicine", "name": "식품 정보", "desc": "약식동원, 건강 식재료"},
]

# ── 패턴 ──
PATTERNS = [
    {"id": "question", "name": "질문형", "template": "[의문사] + [구체적 상황]?", "tone": "호기심 유발"},
    {"id": "surprise", "name": "놀라움형", "template": "[친숙한 소재] + [충격적 수치]!", "tone": "충격, 반전"},
    {"id": "historical", "name": "역사형", "template": "[역사 인물/시대] + [건강 이야기]", "tone": "권위, 스토리텔링"},
    {"id": "fear", "name": "공포형", "template": "[현재 증상] + [미래 위험]을 부른다?", "tone": "경각심"},
    {"id": "practical", "name": "실용형", "template": "[상황] + [실행 방법]!", "tone": "친절, 실용성"},
    {"id": "doubt", "name": "의문형", "template": "[통념] + 사실은 [진실]?", "tone": "호기심, 반전"},
    {"id": "plan", "name": "계획형", "template": "[기간] + [건강 목표] 프로젝트", "tone": "동기부여"},
    {"id": "statistics", "name": "통계형", "template": "[대상] [%]가 겪는 + [이슈]", "tone": "신뢰, 객관성"},
]

# ── 계절/절기 ──
SEASONS = {
    "spring": {"months": [3, 4, 5], "kr": "봄", "theme": "해독과 활력"},
    "summer": {"months": [6, 7, 8], "kr": "여름", "theme": "보양과 수분"},
    "autumn": {"months": [9, 10, 11], "kr": "가을", "theme": "면역과 건조 대비"},
    "winter": {"months": [12, 1, 2], "kr": "겨울", "theme": "보온과 혈액순환"},
}

SOLAR_TERMS = [
    ("02-04", "입춘"), ("02-19", "우수"), ("03-06", "경칩"), ("03-21", "춘분"),
    ("04-05", "청명"), ("04-20", "곡우"), ("05-06", "입하"), ("05-21", "소만"),
    ("06-06", "망종"), ("06-21", "하지"), ("07-07", "소서"), ("07-23", "대서"),
    ("08-08", "입추"), ("08-23", "처서"), ("09-08", "백로"), ("09-23", "추분"),
    ("10-08", "한로"), ("10-24", "상강"), ("11-07", "입동"), ("11-22", "소설"),
    ("12-07", "대설"), ("12-22", "동지"), ("01-06", "소한"), ("01-20", "대한"),
]

# ── 식약처 규제 블랙리스트 ──
REGULATORY_BLACKLIST = [
    "치료", "완치", "특효약", "만병통치", "기적의",
    "암 예방", "암 치료", "당뇨 치료", "고혈압 치료",
    "100% 효과", "부작용 없는", "FDA 승인",
    "약효", "처방전", "진단", "수술 대신",
]

# ── 브랜드 클로징 (고정) ──
BRAND_CLOSING = "더 오래, 더 건강하게. 한의사가 만드는 한의 브랜드"


# ═══════════════════════════════════════════════════════════
# 계절/절기 감지
# ═══════════════════════════════════════════════════════════

def detect_season():
    """현재 날짜 기반 계절 + 절기 감지"""
    now = datetime.now()
    month = now.month
    md = now.strftime("%m-%d")

    season_id = "winter"
    for sid, info in SEASONS.items():
        if month in info["months"]:
            season_id = sid
            break

    solar_term = None
    for date_str, term in SOLAR_TERMS:
        if md >= date_str:
            solar_term = term
        else:
            break

    return {
        "season": season_id,
        "season_kr": SEASONS[season_id]["kr"],
        "theme": SEASONS[season_id]["theme"],
        "solar_term": solar_term,
    }


# ═══════════════════════════════════════════════════════════
# 히스토리 관리
# ═══════════════════════════════════════════════════════════

def load_history():
    """히스토리 파일 로드"""
    if not HISTORY_FILE.exists():
        return {"selected_ideas": []}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"selected_ideas": []}


def save_history(idea: dict):
    """선정 아이디어를 히스토리에 추가"""
    history = load_history()
    history["selected_ideas"].append(idea)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _build_blacklist_text(history: dict) -> str:
    """히스토리에서 사용 금지 소재 텍스트 생성"""
    items = history.get("selected_ideas", [])
    if not items:
        return ""
    lines = ["## 사용 금지 소재 (이전 선정작과 중복 방지)", "다음 소재/키워드는 이미 사용되었으므로 절대 사용하지 마세요:"]
    for item in items:
        kws = ", ".join(item.get("keywords", []))
        pattern = item.get("pattern", "")
        lines.append(f"- {item.get('title', '')} ({pattern}) [{kws}]")
    lines.append("위 소재와 겹치지 않는 완전히 새로운 아이디어를 제안하세요.")
    return "\n".join(lines)


def check_duplicate(idea: dict, history: dict) -> tuple[bool, str]:
    """아이디어가 히스토리와 중복인지 판정

    Returns: (is_duplicate, reason)
    """
    for past in history.get("selected_ideas", []):
        # 동일 역사 인물
        past_kws = set(past.get("keywords", []))
        idea_kws = set(idea.get("keywords", []))
        overlap = past_kws & idea_kws

        # 키워드 3개 이상 겹침
        if len(overlap) >= 3:
            return True, f"키워드 3개 이상 겹침: {overlap}"

        # 동일 제품 + 동일 패턴
        if (idea.get("product") == past.get("product")
                and idea.get("pattern") == past.get("pattern")):
            return True, f"동일 제품+패턴: {idea.get('product')}+{idea.get('pattern')}"

        # 헤드라인 유사도 70% 이상
        sim = SequenceMatcher(
            None,
            idea.get("headline", ""),
            past.get("headline", ""),
        ).ratio()
        if sim >= 0.7:
            return True, f"헤드라인 유사도 {sim:.0%}"

    return False, ""


# ═══════════════════════════════════════════════════════════
# Groq API 호출 (Llama 3.3 70B)
# ═══════════════════════════════════════════════════════════

def _call_groq(system_prompt: str, user_prompt: str, temperature=0.7, max_tokens=2000) -> str | None:
    """Groq API 호출 → 텍스트 응답 반환"""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
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
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"Groq API 호출 실패: {e}")
        return None


def _call_anthropic(system_prompt: str, user_prompt: str, max_tokens=2000) -> str | None:
    """Anthropic Claude API 호출 (폴백)"""
    try:
        import anthropic
    except ImportError:
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    except Exception as e:
        logger.warning(f"Claude API 호출 실패: {e}")
        return None


def _call_llm(system_prompt: str, user_prompt: str, temperature=0.7, max_tokens=2000) -> str | None:
    """Groq → Anthropic 폴백 체인"""
    result = _call_groq(system_prompt, user_prompt, temperature, max_tokens)
    if result:
        return result
    return _call_anthropic(system_prompt, user_prompt, max_tokens)


# ═══════════════════════════════════════════════════════════
# 에이전트 시스템 프롬프트
# ═══════════════════════════════════════════════════════════

_AGENT_SYSTEM = """당신은 '{agent_name}'입니다.

## 임무
{domain} 분야에서 한의원 브랜드 '수(thesoo)'의 Instagram 카드뉴스 스크립트 아이디어 2개를 제안하세요.

## 브랜드 정보
- 브랜드명: 수(thesoo)
- 핵심 USP: 한의사 전문성
- 주요 제품: 공진단, 경옥고, 녹용한약, 우황청심원
- 타겟: 20~50대 건강 관심 고객 (여성 70%)

## 콘텐츠 톤 규칙 (필수)
- 이것은 '광고'가 아니라 '건강 교양 콘텐츠'입니다.
- 고객이 "이거 재밌네, 몰랐던 사실이네"라고 느끼는 것이 목표입니다.
- 내용1~4는 순수한 정보/스토리에 집중. 제품 판매 느낌 절대 금지.
- 내용5는 CTA(문의/상담/구매 유도)가 아닌, 여운을 남기는 마무리.
- 브랜드명은 내용5에 1회만 자연스럽게 등장 가능.
- 참고: "샤넬 No.5로 스타일을 완성하듯, 공진단으로 몸과 마음의 밸런스를 맞춰보세요."

## 말투 규칙 (해요체 필수)
- 사용 어미: ~이에요, ~거든요, ~대요, ~잖아요, ~있어요, ~달라져요
- 금지 어미: ~입니다, ~습니다, ~이다, ~했다
- 친구에게 재밌는 사실을 알려주듯 편하게 쓸 것

## 식약처 규제
- 금지 키워드: 치료, 완치, 특효약, 만병통치, 기적의, 약효, 처방전, 진단
- '~에 도움을 줄 수 있다' 형태로 표현

## 출력 형식 (JSON 배열로 정확히 2개 아이디어)
반드시 아래 JSON만 출력하세요. 다른 텍스트 없이 JSON만:

```json
[
  {{
    "title": "아이디어 제목",
    "source": "참고 트렌드/출처",
    "headline": "표지 후킹 헤드라인 15~30자",
    "content1": "내용1 도입 30~60자",
    "content2": "내용2 전개 30~60자",
    "content3": "내용3 심화 - 동의보감/원전 인용 40~80자",
    "content4": "내용4 핵심 메시지 30~60자",
    "content5": "내용5 여운 마무리 30~60자",
    "product": "연결 제품명 (공진단/경옥고/녹용한약/우황청심원 중 하나)",
    "pattern": "패턴명",
    "keywords": ["핵심키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
    "hashtags": ["#태그1", "#태그2", "#태그3", "#태그4", "#태그5"],
    "reaction": "상/중/하",
    "reaction_reason": "예상 반응도 근거",
    "extra_info": "캡션용 부연 정보 2~3줄"
  }},
  {{ ... }}
]
```"""


# ═══════════════════════════════════════════════════════════
# 아이디어 생성 (5 에이전트 동시)
# ═══════════════════════════════════════════════════════════

def _run_single_agent(agent: dict, user_prompt: str) -> list[dict]:
    """단일 에이전트 실행 → 아이디어 2개 반환"""
    system = _AGENT_SYSTEM.format(
        agent_name=agent["name"],
        domain=agent["domain"],
    )
    raw = _call_llm(system, user_prompt, temperature=0.7, max_tokens=2000)
    if not raw:
        return []

    # JSON 파싱
    ideas = _parse_ideas_json(raw)
    for idea in ideas:
        idea["agent"] = agent["id"]
        idea["agent_name"] = agent["name"]
    return ideas


def _parse_ideas_json(text: str) -> list[dict]:
    """LLM 응답에서 JSON 배열 추출"""
    # ```json ... ``` 블록 추출
    match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if match:
        text = match.group(1)
    else:
        # [ ... ] 패턴 직접 찾기
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            text = match.group(0)

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data[:2]
    except json.JSONDecodeError:
        pass

    # 개별 JSON 객체 추출 시도
    objects = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text)
    results = []
    for obj_str in objects[:2]:
        try:
            results.append(json.loads(obj_str))
        except json.JSONDecodeError:
            continue
    return results


def generate_ideas(
    topic_hint: str = "",
    category: str = "",
    pattern: str = "",
    progress_callback=None,
) -> list[dict]:
    """5개 에이전트 동시 실행 → 10개 아이디어 반환

    Args:
        topic_hint: 주제 힌트 (빈 문자열이면 에이전트 자율)
        category: 카테고리 이름 (빈 문자열이면 자동)
        pattern: 패턴 이름 (빈 문자열이면 자동)
        progress_callback: fn(agent_name, status) 진행 콜백
    """
    season = detect_season()
    history = load_history()
    blacklist = _build_blacklist_text(history)

    # 유저 프롬프트 조합
    parts = []
    if category:
        parts.append(f"카테고리: {category}")
    if pattern:
        parts.append(f"패턴: {pattern}")
    parts.append(f"계절: {season['season_kr']} (테마: {season['theme']})")
    if season.get("solar_term"):
        parts.append(f"절기: {season['solar_term']}")
    if topic_hint:
        parts.append(f"주제 힌트: {topic_hint}")
    if blacklist:
        parts.append(f"\n{blacklist}")

    user_prompt = "\n".join(parts)

    all_ideas = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_run_single_agent, agent, user_prompt): agent
            for agent in AGENTS
        }
        for future in as_completed(futures):
            agent = futures[future]
            try:
                ideas = future.result()
                all_ideas.extend(ideas)
                if progress_callback:
                    progress_callback(agent["name"], f"{len(ideas)}개 완료")
            except Exception as e:
                logger.warning(f"{agent['name']} 실패: {e}")
                if progress_callback:
                    progress_callback(agent["name"], "실패")

    return all_ideas


# ═══════════════════════════════════════════════════════════
# 10개 아이디어 평가
# ═══════════════════════════════════════════════════════════

_EVAL_SYSTEM = """당신은 카드뉴스 스크립트 평가 전문가입니다.

## 평가 기준 (각 10점 만점)
1. **후킹력**: 표지 헤드라인이 스크롤을 멈추게 하는가? (가중치 높음)
2. **스토리텔링**: 카드 간 내러티브 흐름이 자연스러운가? (가중치 높음)
3. **타겟공감도**: 20~50대 건강 관심 고객이 공감하는가? (가중치 높음)
4. **브랜드연결**: 수(thesoo) 제품과 자연스럽게 연결되는가? (가중치 중간)
5. **바이럴가능성**: 저장/공유/댓글을 유도하는가? (가중치 중간)

## 가산점
- 의외의 소재 → 한의학 연결 (예: 샤넬 No.5 → 사향 → 공진단): +3점
- "몰랐던 사실" 전달력 우수: +2점
- 역사적 인물/사실 → 제품 자연 연결: +2점

## 감점
- 스크립트가 "광고 카피"처럼 읽히면: -3점
- 브랜드 직접 홍보/CTA가 내용5 이전에 등장: -2점
- 내용1~4가 제품 소개 중심: -2점
- 해요체 미준수 (~입니다, ~습니다 사용): -1점

## 총점 계산
총점 = (후킹력×1.2 + 스토리텔링×1.2 + 타겟공감도×1.2 + 브랜드연결×0.9 + 바이럴가능성×0.9) + 가산점 - 감점

## 출력 형식 (반드시 JSON 배열만 출력)
```json
[
  {
    "index": 0,
    "hook_score": 8,
    "story_score": 7,
    "empathy_score": 9,
    "brand_score": 6,
    "viral_score": 8,
    "bonus": 3,
    "penalty": 0,
    "total": 45.6,
    "comment": "평가 한줄 코멘트"
  },
  ...
]
```"""


def evaluate_ideas(ideas: list[dict]) -> list[dict]:
    """10개 아이디어를 5개 기준으로 채점하고 순위 매김"""
    history = load_history()

    # 중복 검사 먼저
    for idea in ideas:
        is_dup, reason = check_duplicate(idea, history)
        idea["is_duplicate"] = is_dup
        idea["dup_reason"] = reason

    # LLM 평가
    ideas_text = json.dumps(
        [
            {
                "index": i,
                "agent": idea.get("agent_name", ""),
                "title": idea.get("title", ""),
                "headline": idea.get("headline", ""),
                "content1": idea.get("content1", ""),
                "content2": idea.get("content2", ""),
                "content3": idea.get("content3", ""),
                "content4": idea.get("content4", ""),
                "content5": idea.get("content5", ""),
                "product": idea.get("product", ""),
                "pattern": idea.get("pattern", ""),
            }
            for i, idea in enumerate(ideas)
        ],
        ensure_ascii=False,
    )

    user_prompt = f"아래 {len(ideas)}개 아이디어를 평가해주세요:\n\n{ideas_text}"
    raw = _call_llm(_EVAL_SYSTEM, user_prompt, temperature=0.3, max_tokens=3000)

    scores = []
    if raw:
        scores = _parse_ideas_json(raw)

    # 점수 매핑
    score_map = {s.get("index", -1): s for s in scores}
    for i, idea in enumerate(ideas):
        s = score_map.get(i, {})
        idea["hook_score"] = s.get("hook_score", 5)
        idea["story_score"] = s.get("story_score", 5)
        idea["empathy_score"] = s.get("empathy_score", 5)
        idea["brand_score"] = s.get("brand_score", 5)
        idea["viral_score"] = s.get("viral_score", 5)
        idea["bonus"] = s.get("bonus", 0)
        idea["penalty"] = s.get("penalty", 0)
        idea["eval_comment"] = s.get("comment", "")

        # 총점 계산
        total = (
            idea["hook_score"] * 1.2
            + idea["story_score"] * 1.2
            + idea["empathy_score"] * 1.2
            + idea["brand_score"] * 0.9
            + idea["viral_score"] * 0.9
            + idea["bonus"]
            - idea["penalty"]
        )
        # 중복이면 0점
        if idea.get("is_duplicate"):
            total = 0
        idea["total_score"] = round(total, 1)

    # 총점 내림차순 정렬
    ideas.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    for rank, idea in enumerate(ideas, 1):
        idea["rank"] = rank

    return ideas


# ═══════════════════════════════════════════════════════════
# 풀 스크립트 생성
# ═══════════════════════════════════════════════════════════

_SCRIPT_SYSTEM = """당신은 건강 카드뉴스 스크립트 작가입니다.

## 카드뉴스 구조 (7장)
#1. 표지: 후킹 헤드라인 15~30자
#2. 내용1: 도입 - 흥미로운 연결/놀라운 사실 30~60자
#3. 내용2: 전개 - 핵심 소재/성분 소개 30~60자
#4. 내용3: 심화 - 동의보감/한의학 원전 인용 40~80자 (가장 긴 카드)
#5. 내용4: 핵심 메시지 - 실질적 가치 30~60자
#6. 내용5: 여운 마무리 - 비유/감성적 클로징 30~60자 (광고성 CTA 금지)
#7. 내용6: 더 오래, 더 건강하게. 한의사가 만드는 한의 브랜드 (고정)

## 말투: 해요체 필수
## 이모지: 카드뉴스 본문에서는 사용 금지
## 각 카드는 최대 4줄, 한 줄당 18~20자

## 이미지 프롬프트 규칙
- 반드시 영문으로 작성
- 끝에 필수 삽입: "No text, no letters, no numbers, no typography, no watermark, no logo. Vertical format 1080x1440px, 3:4 aspect ratio."
- 상단 35~40%는 빈 공간 (텍스트 오버레이 영역)
- 핵심 오브제는 하단 60%에 배치
- #7 클로징은 고정 이미지이므로 프롬프트 불필요

## 색상 톤 가이드
- 역사/궁중: 다크브라운, 앰버, 골드 라인
- 수면/밤: 딥네이비, 인디고, 실버
- 봄/절기: 소프트그린, 크림, 연분홍
- 여성건강: 웜베이지, 로즈, 라벤더
- 직장인: 슬레이트그레이, 화이트, 블루 포인트

## 출력 형식 (반드시 JSON만 출력)
```json
{
  "cover": "표지 헤드라인",
  "content1": "내용1",
  "content2": "내용2",
  "content3": "내용3",
  "content4": "내용4",
  "content5": "내용5",
  "content6": "더 오래, 더 건강하게. 한의사가 만드는 한의 브랜드",
  "hashtags": ["#수한의원", "#thesoo", "#한의사", "#건강정보", "#주제태그"],
  "sources": ["출처1", "출처2"],
  "image_prompts": {
    "cover": "영문 이미지 프롬프트",
    "content1": "영문 이미지 프롬프트",
    "content2": "영문 이미지 프롬프트",
    "content3": "영문 이미지 프롬프트",
    "content4": "영문 이미지 프롬프트",
    "content5": "영문 이미지 프롬프트"
  }
}
```"""


def generate_full_script(idea: dict) -> dict | None:
    """선택된 아이디어의 풀 7장 스크립트 + 이미지 프롬프트 생성"""
    user_prompt = f"""아래 아이디어를 7장 카드뉴스 풀 스크립트로 완성해주세요.

아이디어 제목: {idea.get('title', '')}
표지 헤드라인: {idea.get('headline', '')}
내용1: {idea.get('content1', '')}
내용2: {idea.get('content2', '')}
내용3: {idea.get('content3', '')}
내용4: {idea.get('content4', '')}
내용5: {idea.get('content5', '')}
연결 제품: {idea.get('product', '')}
패턴: {idea.get('pattern', '')}
참고 출처: {idea.get('source', '')}
캡션용 부연: {idea.get('extra_info', '')}

이 내용을 다듬고, 각 카드별 이미지 프롬프트도 함께 작성해주세요."""

    raw = _call_llm(_SCRIPT_SYSTEM, user_prompt, temperature=0.5, max_tokens=3000)
    if not raw:
        return None

    # JSON 파싱
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if match:
        raw = match.group(1)
    else:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            raw = match.group(0)

    try:
        script = json.loads(raw)
        script["content6"] = BRAND_CLOSING
        return script
    except json.JSONDecodeError:
        logger.warning(f"스크립트 JSON 파싱 실패")
        return None


# ═══════════════════════════════════════════════════════════
# Instagram Description Mention 생성
# ═══════════════════════════════════════════════════════════

_DESC_SYSTEM = """당신은 인스타그램 캡션 작성 전문가입니다.

카드뉴스 스크립트를 기반으로 **인스타그램 피드에 함께 게시할 장문 캡션(Description Mention)**을 작성하세요.

## 톤앤매너 규칙
1. 해요체 (~하세요, ~있어요, ~거든요)
2. 카드뉴스보다 문장이 길고 친절한 부연 설명 포함
3. "알고 계셨나요?", "~하는 분들 많으시죠?" 같은 대화체 질문 활용

## 이모지 사용 (적극적)
- 섹션 구분: 1️⃣ 2️⃣ 3️⃣ 또는 🟡 🟢 🟤
- 포인트 강조: ✅ ☀️ 🍂 ❄️ 💤
- 출처: 📖

## 필수 구조
[도입 후킹] — 질문형 or 시의성 있는 첫 문장 (1~2줄)

[섹션별 상세 내용] — 카드뉴스 내용을 풀어쓴 본문
- 번호 이모지로 섹션 구분
- 각 섹션 하위에 - 불릿 2~4개
- 카드에 담지 못한 추가 정보/팁 보충

[마무리 메시지] — 행동 유도 or 공감 1~2줄

[푸터 블록]
📖 내용출처 | [출처명]
더 오래, 더 건강하게.
한의사가 만드는 한의 브랜드, 수홍
@thesoo_official

[해시태그] — 별도 줄에 5~8개 (고정: #수한의원 #thesoo #한의사 #건강정보 + 주제 3~4개)

## 길이: 800~1500자 (인스타그램 2,200자 이내)

## 출력
캡션 텍스트만 출력하세요. JSON이 아닌 그대로 붙여넣기할 수 있는 텍스트로."""


def generate_description(script: dict, idea: dict) -> str:
    """풀 스크립트 → Instagram Description Mention 생성"""
    user_prompt = f"""아래 카드뉴스 스크립트를 인스타그램 Description Mention으로 변환해주세요.

제목: {idea.get('title', '')}
표지: {script.get('cover', '')}
내용1: {script.get('content1', '')}
내용2: {script.get('content2', '')}
내용3: {script.get('content3', '')}
내용4: {script.get('content4', '')}
내용5: {script.get('content5', '')}
출처: {', '.join(script.get('sources', []))}
해시태그: {' '.join(script.get('hashtags', []))}
캡션용 부연 정보: {idea.get('extra_info', '')}
연결 제품: {idea.get('product', '')}"""

    result = _call_llm(_DESC_SYSTEM, user_prompt, temperature=0.6, max_tokens=2000)
    return result or ""
