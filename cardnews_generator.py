"""10-에이전트 경쟁 카드뉴스 스크립트 생성 모듈

5개 전문 에이전트가 각 2개 아이디어 = 10개 아이디어 생성 후
5개 기준 경쟁 평가 → Top 2 선정 → 풀 스크립트 + 이미지 프롬프트 + Description Mention 생성
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests as _requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ── 히스토리 파일 ──
HISTORY_FILE = Path(__file__).parent / "cardnews_history.json"

# ── 에이전트 정의 ──
AGENTS = [
    {
        "id": "health",
        "name": "건강정보 에이전트",
        "domain": "건강 상식, 영양, 수면, 면역, 운동, 질환 예방",
        "search_hint": "건강 트렌드, 영양 정보, 수면 면역",
    },
    {
        "id": "celeb",
        "name": "연예트렌드 에이전트",
        "domain": "셀럽 뷰티·다이어트·라이프스타일 트렌드, 방송 건강 이슈",
        "search_hint": "연예인 건강 다이어트 뷰티 트렌드",
    },
    {
        "id": "lifestyle",
        "name": "생활건강 에이전트",
        "domain": "일상 생활 속 건강 팁, 식품 정보, 계절 생활, 홈케어",
        "search_hint": "생활 건강 팁, 계절 음식, 홈케어",
    },
    {
        "id": "women",
        "name": "여성라이프 에이전트",
        "domain": "여성 건강, 호르몬, 이너뷰티, 갱년기, 피부 관리",
        "search_hint": "여성 건강 뷰티 호르몬 이너뷰티",
    },
    {
        "id": "worker",
        "name": "직장인생활 에이전트",
        "domain": "직장인 피로, 번아웃, 수면 부족, 사무직 건강, 점심 식단",
        "search_hint": "직장인 건강 번아웃 수면 식단",
    },
]

# ── 카테고리 ──
CATEGORIES = [
    {"id": "health", "name": "건강", "desc": "건강 상식, 영양, 수면, 면역, 운동, 질환 예방"},
    {"id": "entertainment", "name": "연예", "desc": "셀럽 트렌드, 뷰티, 다이어트, 방송 건강 이슈"},
    {"id": "lifestyle", "name": "생활", "desc": "일상 건강 팁, 계절 생활, 식품 정보, 홈케어"},
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
# 시즌/트렌드 기반 주제 제안
# ═══════════════════════════════════════════════════════════

# ── 월별 시즌 이벤트·트렌드 (피그마 2024 콘텐츠 수준) ──
# 허준런, 밤티라미수 같은 시의성 높은 이슈 + 건강 접점
_MONTHLY_EVENTS = {
    1: [  # 1월
        {"topic": "작심삼일로 끝나지 않는 새해 건강 계획", "tag": "새해", "product": "공진단"},
        {"topic": "정월대보름 오곡밥, 조선시대 왕실도 챙긴 건강식?", "tag": "명절", "product": "경옥고"},
        {"topic": "신년 해돋이 등산, 겨울 산행이 몸에 미치는 효과", "tag": "이벤트", "product": "녹용한약"},
        {"topic": "겨울 온천 vs 족욕, 체온 1도가 면역력을 바꾼다?", "tag": "시즌", "product": "경옥고"},
    ],
    2: [  # 2월
        {"topic": "발렌타인 초콜릿, 카카오가 뇌에 미치는 놀라운 효과", "tag": "발렌타인", "product": "공진단"},
        {"topic": "설 연휴 과식 후유증, 조선시대 해독법은?", "tag": "설날", "product": "경옥고"},
        {"topic": "꽃샘추위에 면역력 뚝, 환절기 첫 번째 적신호", "tag": "시즌", "product": "경옥고"},
        {"topic": "졸업 시즌, 20대 첫 건강검진에서 꼭 봐야 할 수치", "tag": "졸업", "product": "없음"},
    ],
    3: [  # 3월
        {"topic": "삼겹살데이, 고기와 함께 먹으면 간이 좋아하는 조합?", "tag": "삼겹살데이", "product": "공진단"},
        {"topic": "봄 나물 캐기 열풍, 독초 구별법 알고 가세요", "tag": "시즌", "product": "없음"},
        {"topic": "벚꽃 마라톤 시즌, 초보 러너가 놓치는 심박수 관리", "tag": "마라톤", "product": "녹용한약"},
        {"topic": "미세먼지 빨간불, 폐를 지키는 동의보감 처방전", "tag": "시즌", "product": "경옥고"},
    ],
    4: [  # 4월
        {"topic": "식목일에 심는 건 나무만? 장 건강을 심는 봄 식단", "tag": "식목일", "product": "경옥고"},
        {"topic": "춘곤증의 진짜 범인, 뇌가 보내는 SOS 신호", "tag": "시즌", "product": "공진단"},
        {"topic": "봄 등산 시즌, 조선 선비들의 산행 건강법", "tag": "시즌", "product": "녹용한약"},
        {"topic": "4월 과학의 달, 한약의 효능을 밝힌 논문 TOP 3", "tag": "과학의달", "product": "공진단"},
    ],
    5: [  # 5월
        {"topic": "어버이날 건강 선물, 조선시대 효도 보양 문화", "tag": "어버이날", "product": "공진단"},
        {"topic": "가정의 달 캠핑 붐, 숲이 뇌를 치유하는 과학적 이유", "tag": "가정의달", "product": "없음"},
        {"topic": "스승의 날, 허준이 스승에게 배운 건강 철학", "tag": "스승의날", "product": "경옥고"},
        {"topic": "장미 축제 시즌, 꽃 향기가 스트레스를 줄이는 원리", "tag": "시즌", "product": "없음"},
    ],
    6: [  # 6월
        {"topic": "단오에 머리 감는 창포물, 과학적으로 효과 있을까?", "tag": "단오", "product": "없음"},
        {"topic": "여름 맥주 시즌, '맥주 한 잔'이 간에 미치는 72시간", "tag": "시즌", "product": "공진단"},
        {"topic": "장마철 곰팡이가 폐에 미치는 영향, 알고 계셨나요?", "tag": "장마", "product": "경옥고"},
        {"topic": "월드컵 밤샘 응원, 수면 부채가 쌓이면 생기는 일", "tag": "이벤트", "product": "공진단"},
    ],
    7: [  # 7월
        {"topic": "초복 삼계탕, 닭 속에 들어가는 한약재의 비밀", "tag": "삼복", "product": "경옥고"},
        {"topic": "워터파크 시즌, 물놀이 후 귀 건강 체크리스트", "tag": "시즌", "product": "없음"},
        {"topic": "열대야 불면증, 조선 왕실은 어떻게 여름밤을 났을까?", "tag": "시즌", "product": "공진단"},
        {"topic": "여름 빙수 열풍, 팥이 의외로 약재였다고?", "tag": "트렌드", "product": "없음"},
    ],
    8: [  # 8월
        {"topic": "말복 보양식, 동의보감이 추천한 여름 원기 회복법", "tag": "삼복", "product": "경옥고"},
        {"topic": "휴가 후유증, 여행 피로가 2주나 가는 이유", "tag": "휴가", "product": "공진단"},
        {"topic": "수박 한 통의 영양소, 알고 먹으면 약이 된다?", "tag": "시즌", "product": "없음"},
        {"topic": "광복절 태극기 속 건곤감리, 한의학과의 놀라운 연결", "tag": "광복절", "product": "없음"},
    ],
    9: [  # 9월
        {"topic": "추석 송편 속 솔잎, 동의보감이 말하는 효능", "tag": "추석", "product": "경옥고"},
        {"topic": "9월 21일 치매극복의 날, 기억을 지키는 뇌 건강 습관", "tag": "기념일", "product": "공진단"},
        {"topic": "가을 단풍놀이, 걷기가 뇌에 주는 선물", "tag": "시즌", "product": "녹용한약"},
        {"topic": "허준런(RUN), 달리면서 만나는 동의보감 이야기", "tag": "이벤트", "product": "경옥고"},
    ],
    10: [  # 10월
        {"topic": "10월 간의 날, 내 간은 몇 살일까?", "tag": "기념일", "product": "공진단"},
        {"topic": "밤티라미수 열풍, 밤이 보약이라 불린 이유", "tag": "트렌드", "product": "경옥고"},
        {"topic": "할로윈 호박, 조선시대에는 약재였다고?", "tag": "할로윈", "product": "없음"},
        {"topic": "가을 축제 시즌, 지금 가기 딱 좋은 건강 핫플 5곳", "tag": "시즌", "product": "없음"},
        {"topic": "고양이가 콜레라 예방을? 조선시대 역병 이야기", "tag": "시즌", "product": "없음"},
    ],
    11: [  # 11월
        {"topic": "수능 D-day, 수험생 뇌를 깨우는 마지막 컨디션 관리", "tag": "수능", "product": "공진단"},
        {"topic": "빼빼로데이, 무심코 먹은 빼빼로가 공깃밥 칼로리?", "tag": "빼빼로데이", "product": "없음"},
        {"topic": "김장 시즌, 발효 음식이 장 건강을 바꾸는 과학", "tag": "김장", "product": "경옥고"},
        {"topic": "11월 11일 농업인의 날, 약재 농부가 전하는 한약 이야기", "tag": "기념일", "product": "녹용한약"},
    ],
    12: [  # 12월
        {"topic": "연말 모임 숙취 생존법, 46년생 트럼프의 건강 비결", "tag": "연말", "product": "공진단"},
        {"topic": "팥붕 vs 슈붕, 당신의 선택은? 겨울 간식 건강 비교", "tag": "트렌드", "product": "없음"},
        {"topic": "크리스마스 케이크 속 숨은 칼로리, 알고 먹자", "tag": "크리스마스", "product": "없음"},
        {"topic": "동지 팥죽, 조선 왕실의 겨울 보양 의식", "tag": "동지", "product": "경옥고"},
        {"topic": "겨울 족욕의 과학, 발을 따뜻하게 하면 숙면이 온다?", "tag": "시즌", "product": "없음"},
    ],
}

# 절기·시즌별 주제 풀 (월별 이벤트에서 동적 생성)
_SEASONAL_TOPICS = {
    "winter": [
        "혹시 엘사? 손발이 차가운 당신에게",
        "올 겨울, 생강 없으면 손해",
        "겨울철 발 건강, 족욕이 숙면까지 바꾼다?",
        "겨울잠 자는 동물 vs 불면증 현대인",
        "비타민D 부족이 겨울 우울증을 부른다?",
    ],
    "spring": [
        "벚꽃은 예쁜데 왜 눈물이? 꽃가루 알레르기의 진짜 원인",
        "봄나물이 슈퍼푸드? 냉이 한 줌의 영양 성분 분석",
        "황사 마스크로는 부족해, 폐를 지키는 동의보감 처방",
        "3월 새 학기, 아이 키 성장의 골든타임은 언제?",
        "춘곤증 때문에 졸린 게 아닙니다 — 뇌의 SOS 신호",
    ],
    "summer": [
        "삼계탕 말고도 있다, 동의보감 여름 보양식 3선",
        "에어컨 틀면 왜 머리가 아플까? 냉방병의 정체",
        "열대야에 잠 못 드는 밤, 조선 왕실의 여름나기",
        "물만 마시면 안 된다? 여름 수분 보충의 과학",
        "여름 맥주 한 잔이 간에 미치는 72시간의 여정",
    ],
    "autumn": [
        "공진단처럼 꽉 찬 추석 보내세요!",
        "환절기마다 감기 걸린다면? 면역력의 진짜 비밀",
        "가을 우울증, 일조량과 세로토닌의 숨은 관계",
        "10월 간의 날, 내 간은 피곤하다",
        "단풍 산책이 뇌에 주는 선물, 걷기의 과학",
    ],
}

# 절기별 특화 주제
_SOLAR_TERM_TOPICS = {
    "입춘": "입춘에 먹는 오신채, 동의보감이 말하는 봄 해독 약재",
    "우수": "우수(雨水) 절기, 봄비가 피부 건조를 부르는 이유",
    "경칩": "경칩에 깨어나는 건 벌레뿐? 우리 몸도 리셋해야 할 때",
    "춘분": "낮밤 같아지는 춘분, 수면 리듬 맞추는 황금 타이밍",
    "청명": "청명에 차를 마시는 이유? 1000년 된 건강 의식",
    "곡우": "곡우(穀雨)에 뜯는 쑥, 조선 왕실 최고의 약재였다",
    "입하": "입하, 동의보감이 말하는 여름 체력 비축법",
    "소만": "소만 절기, 보리밥이 보약인 이유 — 영양 성분 분석",
    "망종": "망종(芒種), 씨 뿌리는 절기에 제철 약재도 심는다",
    "하지": "하지, 가장 긴 낮의 자외선이 피부에 미치는 영향",
    "소서": "소서부터 시작되는 더위, 열사병과 일사병의 차이는?",
    "대서": "대서 폭염 속 조선 왕실의 피서법, 현대에도 통할까?",
    "입추": "입추, 가을 대비 면역력 충전 — 경옥고의 계절",
    "처서": "처서에 더위 보내기, 잠이 보약인 진짜 이유",
    "백로": "백로(白露) 이슬 절기, 호흡기가 취약해지는 시기",
    "추분": "추분의 밤이 길어지면, 멜라토닌이 달라진다",
    "한로": "한로(寒露), 찬 이슬이 관절에 미치는 영향",
    "상강": "상강에 서리 내리면, 혈관이 수축하는 이유",
    "입동": "입동, 김치 담그는 날 — 발효 식품의 과학",
    "소설": "소설(小雪), 첫눈 오는 절기의 면역 강화 약재",
    "대설": "대설 겨울 산행, 추위 속 운동이 칼로리를 2배 태운다?",
    "동지": "동지 팥죽의 비밀, 팥이 보약이라 불린 이유",
    "소한": "소한, 1년 중 가장 추운 날 — 체온 1도의 과학",
    "대한": "대한 강추위, 혈액이 끈적해지는 겨울의 위험",
}

# 시의성 있는 트렌드/팝컬쳐 주제 (연중 사용)
_TRENDING_TOPICS = [
    "바이오해킹과 체질의학의 놀라운 공통점",
    "슬로우조깅, 느리지만 강력한 운동의 힘",
    "넷플릭스 보면서 먹는 야식, 뇌에 무슨 일이?",
    "MZ세대 이너뷰티 열풍, 콜라겐 음료 진짜 효과는?",
    "당신은 골룸입니까? 거북목이 수면을 망치는 과정",
    "셀럽들의 피로 회복템, 공진단이 왜 거기서 나와?",
    "SNS 핫한 '아침 루틴', 전문가가 본 진짜 효과",
    "하루 1만보 신화, 진짜 필요한 건 4000보?",
    "커피 3잔 이상 마시면 생기는 몸의 변화 타임라인",
    "디지털 디톡스가 뇌에 미치는 72시간의 변화",
]


def _calc_topic_score(topic: str, tag: str, product: str, source_type: str) -> int:
    """추천 주제 점수 산정 (100점 만점)

    - 시즌 적합성 30점, 제품 연결 20점, 후킹 요소 20점,
      트렌드 신선도 15점, 출처 신뢰도 15점
    """
    score = 0

    # 1) 시즌 적합성 (30점)
    if source_type == "monthly":
        score += 28  # 월별 이벤트는 시즌 최고
    elif source_type == "solar":
        score += 25
    elif source_type == "season":
        score += 20
    elif source_type == "news":
        score += 22  # 실시간 뉴스도 시의성 높음
    else:
        score += 15  # 트렌드 (연중)

    # 2) 수壽 제품 연결 (20점)
    if product and product != "없음":
        score += 18
    elif any(kw in topic for kw in ["공진단", "경옥고", "녹용", "한약", "동의보감", "한의"]):
        score += 14
    else:
        score += 6

    # 3) 후킹 요소 (20점)
    hook_score = 8  # 기본
    if "?" in topic:
        hook_score += 5  # 질문형
    if any(c.isdigit() for c in topic):
        hook_score += 4  # 숫자 포함
    pop_refs = ["엘사", "골룸", "트럼프", "셀럽", "넷플릭스", "MZ", "SNS",
                "바이오해킹", "슬로우조깅", "티라미수", "빼빼로"]
    if any(ref in topic for ref in pop_refs):
        hook_score += 3  # 팝컬쳐
    score += min(hook_score, 20)

    # 4) 트렌드 신선도 (15점)
    if source_type == "news":
        score += 14
    elif source_type == "monthly":
        score += 11
    elif source_type in ("solar", "season"):
        score += 9
    else:
        score += 7

    # 5) 출처 신뢰도 (15점)
    trust_kw = ["동의보감", "논문", "연구", "승정원", "조선", "왕실", "세종", "영조", "허준"]
    if any(kw in topic for kw in trust_kw):
        score += 14
    elif any(kw in topic for kw in ["과학", "효과", "성분", "칼로리", "심박수"]):
        score += 11
    else:
        score += 8

    return min(score, 100)


# 태그별 사유 매핑
_REASON_MAP = {
    # 월별 이벤트 태그
    "새해": "{month}월 시즌 이슈 · 새해 건강 트렌드",
    "명절": "{month}월 시즌 이슈 · 명절 건강 관심 급증",
    "설날": "{month}월 시즌 이슈 · 설 연휴 건강 관심",
    "발렌타인": "{month}월 시즌 이슈 · 발렌타인 시즌",
    "졸업": "{month}월 시즌 이슈 · 졸업·입학 시즌",
    "삼겹살데이": "{month}월 기념일 · 인스타그램 인기 해시태그",
    "마라톤": "{month}월 이벤트 · 마라톤·러닝 시즌",
    "식목일": "{month}월 기념일 · 봄 건강 관심",
    "과학의달": "{month}월 기념일 · 한의학 과학 근거",
    "어버이날": "{month}월 시즌 이슈 · 가정의 달 건강 선물",
    "가정의달": "{month}월 시즌 이슈 · 가정의 달 트렌드",
    "스승의날": "{month}월 기념일 · 허준 스토리 시의성",
    "단오": "{month}월 기념일 · 전통 건강 문화",
    "장마": "{month}월 시즌 이슈 · 장마철 건강 관심",
    "삼복": "{month}월 시즌 이슈 · 복날 보양식 트렌드",
    "휴가": "{month}월 시즌 이슈 · 여름 휴가 시즌",
    "광복절": "{month}월 기념일 · 문화 콘텐츠",
    "추석": "{month}월 시즌 이슈 · 추석 명절 건강",
    "기념일": "{month}월 기념일 · 건강 인식 제고",
    "할로윈": "{month}월 시즌 이슈 · 할로윈 트렌드",
    "수능": "{month}월 시즌 이슈 · 수능 시즌 건강 관심 급증",
    "빼빼로데이": "{month}월 기념일 · 인스타그램 인기 해시태그",
    "김장": "{month}월 시즌 이슈 · 김장 시즌 건강",
    "연말": "{month}월 시즌 이슈 · 연말 모임 건강 관심",
    "크리스마스": "{month}월 시즌 이슈 · 크리스마스 트렌드",
    "동지": "{month}월 기념일 · 동지 전통 건강 문화",
    # 일반
    "이벤트": "{month}월 이벤트 · 시의성 높은 이슈",
    "시즌": "{month}월 시즌 · 계절 건강 관심",
    "트렌드": "SNS 인기 주제 · 인스타그램 건강 트렌드",
}


def _build_reason(tag: str, source_type: str, month: int, extra: str = "") -> str:
    """추천 사유 텍스트 생성"""
    if source_type == "news":
        base = "실시간 구글 뉴스 트렌드"
        if extra:
            return f"{base} · {extra[:20]}"
        return base
    if source_type == "solar":
        return f"{tag} · 절기 건강 이슈"
    template = _REASON_MAP.get(tag, "{month}월 시즌 · 건강 콘텐츠")
    return template.format(month=month)


def _fetch_news_fast() -> list[dict]:
    """RSS 헤드라인만 빠르게 가져와 키워드 매칭으로 주제 후보 생성 (LLM 불필요)

    - 30분 캐시 재사용
    - 실패 시 빈 리스트 반환 (큐레이션 주제에 영향 없음)
    """
    global _news_cache
    now_ts = time.time()

    # 캐시 유효하면 기존 데이터 사용
    if _news_cache.get("fast_topics") and (now_ts - _news_cache["timestamp"]) < _NEWS_CACHE_TTL:
        return _news_cache["fast_topics"]

    month = datetime.now().month
    results = []
    all_headlines: dict[str, list[str]] = {}

    for feed in _NEWS_FEEDS:
        try:
            headlines = _fetch_rss_headlines(feed["url"], max_items=8)
        except Exception:
            continue
        if not headlines:
            continue
        all_headlines[feed["tag"]] = headlines

        # 키워드 매칭으로 상위 2개 선별 (LLM 없이)
        picked = 0
        for h in headlines:
            if picked >= 2:
                break
            # 30자 이내로 정리
            short = h[:35].rstrip() + ("..." if len(h) > 35 else "")
            results.append({
                "label": f"📰 {short}",
                "topic": h[:50],
                "tag": feed["tag"],
                "product": "없음",
                "source_type": "news",
                "news_ref": h[:25],
            })
            picked += 1

    # 캐시 업데이트 (헤드라인도 저장 — 에이전트 컨텍스트용)
    _news_cache["fast_topics"] = results
    if all_headlines:
        _news_cache["headlines"] = all_headlines
    _news_cache["timestamp"] = now_ts

    return results


def suggest_topics(include_news: bool = True) -> list[dict]:
    """현재 시즌/절기/트렌드 + 월별 이벤트 + 뉴스 기반 주제 추천

    Returns: score 내림차순 정렬된 추천 주제 리스트
    [{"label", "topic", "tag", "product", "reason", "score", "source_type"}, ...]
    """
    season = detect_season()
    now = datetime.now()
    month = now.month
    suggestions = []

    import random
    day_seed = now.strftime("%Y-%m-%d")
    rng = random.Random(day_seed)

    # 1) 월별 이벤트/트렌드 주제 (최우선)
    monthly = list(_MONTHLY_EVENTS.get(month, []))
    if now.day >= 20:
        next_month = (month % 12) + 1
        monthly = monthly + list(_MONTHLY_EVENTS.get(next_month, []))
    for evt in rng.sample(monthly, min(3, len(monthly))):
        suggestions.append({
            "topic": evt["topic"],
            "tag": evt["tag"],
            "product": evt.get("product", "없음"),
            "source_type": "monthly",
        })

    # 2) 절기 특화 주제
    solar = season.get("solar_term")
    if solar and solar in _SOLAR_TERM_TOPICS:
        suggestions.append({
            "topic": _SOLAR_TERM_TOPICS[solar],
            "tag": f"절기({solar})",
            "product": "없음",
            "source_type": "solar",
        })

    # 3) 시즌 주제 2개
    season_pool = _SEASONAL_TOPICS.get(season["season"], [])
    for t in rng.sample(season_pool, min(2, len(season_pool))):
        suggestions.append({
            "topic": t,
            "tag": season["season_kr"],
            "product": "없음",
            "source_type": "season",
        })

    # 4) 트렌드/팝컬쳐 주제 2개
    for t in rng.sample(_TRENDING_TOPICS, min(2, len(_TRENDING_TOPICS))):
        suggestions.append({
            "topic": t,
            "tag": "트렌드",
            "product": "없음",
            "source_type": "trend",
        })

    # 5) 실시간 뉴스 (자동 로드)
    if include_news:
        news = _fetch_news_fast()
        suggestions += news

    # ── 점수 + 사유 계산 후 정렬 ──
    tag_emoji = {
        "트렌드": "🔥", "건강뉴스": "📰", "연예뉴스": "🎬", "생활뉴스": "🏠",
    }
    for sug in suggestions:
        src = sug.get("source_type", "trend")
        product = sug.get("product", "없음")
        sug["score"] = _calc_topic_score(sug["topic"], sug["tag"], product, src)
        sug["reason"] = _build_reason(sug["tag"], src, month, sug.get("news_ref", ""))
        # label 생성
        emoji = tag_emoji.get(sug["tag"], "📅" if src == "monthly" else "🗓️" if src == "solar" else "🌿")
        short_topic = sug["topic"][:30] + ("..." if len(sug["topic"]) > 30 else "")
        sug["label"] = f"{emoji} {short_topic}"

    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return suggestions[:10]


# ═══════════════════════════════════════════════════════════
# 실시간 뉴스 트렌드 (건강/연예 뉴스 RSS)
# ═══════════════════════════════════════════════════════════

# 모듈 레벨 캐시 (Streamlit 리런마다 재호출 방지)
_news_cache: dict = {"data": [], "headlines": {}, "timestamp": 0.0}
_NEWS_CACHE_TTL = 1800  # 30분

_NEWS_FEEDS = [
    {
        "name": "건강",
        "url": "https://news.google.com/rss/search?q=%EA%B1%B4%EA%B0%95+%EC%98%81%EC%96%91+%EC%88%98%EB%A9%B4+%EB%A9%B4%EC%97%AD&hl=ko&gl=KR&ceid=KR:ko",
        "tag": "건강뉴스",
        "emoji": "💊",
    },
    {
        "name": "연예",
        "url": "https://news.google.com/rss/search?q=%EC%97%B0%EC%98%88%EC%9D%B8+%EB%8B%A4%EC%9D%B4%EC%96%B4%ED%8A%B8+%EB%B7%B0%ED%8B%B0+%ED%94%BC%EB%B6%80&hl=ko&gl=KR&ceid=KR:ko",
        "tag": "연예뉴스",
        "emoji": "🎬",
    },
    {
        "name": "생활",
        "url": "https://news.google.com/rss/search?q=%EC%83%9D%ED%99%9C+%EA%B1%B4%EA%B0%95+%EC%8B%9D%ED%92%88+%EC%9A%B4%EB%8F%99+%EC%8A%B5%EA%B4%80&hl=ko&gl=KR&ceid=KR:ko",
        "tag": "생활뉴스",
        "emoji": "🏠",
    },
]


def _fetch_rss_headlines(url: str, max_items: int = 10) -> list[str]:
    """Google News RSS에서 헤드라인 추출"""
    try:
        resp = _requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        titles = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                # Google News 제목에서 " - 매체명" 제거
                t = re.sub(r"\s*-\s*[^-]+$", "", title_el.text).strip()
                if t and len(t) > 5:
                    titles.append(t)
            if len(titles) >= max_items:
                break
        return titles
    except Exception as e:
        logger.warning(f"RSS 피드 가져오기 실패 ({url[:50]}...): {e}")
        return []


_NEWS_TRANSFORM_SYSTEM = """당신은 건강 브랜드 '수(thesoo)'의 콘텐츠 기획자입니다.
아래 뉴스 헤드라인들을 보고, 건강/연예/생활 카드뉴스로 재해석할 수 있는 주제 3개를 추천하세요.

## 규칙
1. 뉴스 속 건강·뷰티·라이프스타일 이슈를 카드뉴스 주제로 변환
2. 연예인 이름은 직접 언급하지 말고, 현상/트렌드로 변환
   예: "○○ 다이어트 비법" → "셀럽들 사이 유행하는 간헐적 단식, 진짜 효과는?"
3. 검증 불가능한 주장 금지 — 뉴스 사실만 활용
4. 각 주제는 20~40자, 호기심 유발하는 톤
5. 관련 없는 뉴스는 무시

## 출력 (반드시 JSON 배열만, 다른 텍스트 없이)
```json
[
  {"topic": "주제 텍스트", "news_ref": "참고한 뉴스 키워드 5~10자"},
  ...
]
```"""


def _transform_headlines_to_topics(headlines: list[str], feed_name: str) -> list[dict]:
    """뉴스 헤드라인 → 카드뉴스 주제 힌트로 변환

    1차: LLM 변환 시도
    2차: LLM 실패 시 헤드라인에서 직접 추출 (LLM-free 폴백)
    """
    if not headlines:
        return []

    # LLM 변환 시도
    user_prompt = f"[{feed_name} 뉴스 헤드라인]\n" + "\n".join(
        f"- {h}" for h in headlines
    )
    raw = _call_llm(_NEWS_TRANSFORM_SYSTEM, user_prompt, temperature=0.5, max_tokens=500)
    if raw:
        parsed = _parse_ideas_json(raw, limit=3)
        if parsed:
            return parsed

    # LLM 실패 → 헤드라인에서 직접 추출 (짧게 다듬기)
    logger.info(f"LLM 변환 실패, {feed_name} 헤드라인 직접 사용")
    results = []
    for h in headlines[:3]:
        # 30자 이내로 자르기
        topic = h[:30].rstrip() + ("..." if len(h) > 30 else "")
        results.append({"topic": topic, "news_ref": h[:15]})
    return results


def fetch_news_topics(force_refresh: bool = False) -> list[dict]:
    """실시간 뉴스 기반 트렌드 주제 반환 (30분 캐시)

    Returns: [{"label": str, "topic": str, "tag": str, "news_ref": str}, ...]
    """
    global _news_cache

    now = time.time()
    if (
        not force_refresh
        and _news_cache["data"]
        and (now - _news_cache["timestamp"]) < _NEWS_CACHE_TTL
    ):
        return _news_cache["data"]

    all_topics: list[dict] = []
    all_headlines: dict[str, list[str]] = {}

    for feed in _NEWS_FEEDS:
        headlines = _fetch_rss_headlines(feed["url"])
        if not headlines:
            continue

        # 원본 헤드라인 저장 (에이전트에 컨텍스트로 전달용)
        all_headlines[feed["tag"]] = headlines

        transformed = _transform_headlines_to_topics(headlines, feed["name"])
        time.sleep(2)  # Groq rate limit 방지
        for item in transformed:
            topic_text = item.get("topic", "")
            if topic_text:
                all_topics.append({
                    "label": f"{feed['emoji']} {topic_text}",
                    "topic": topic_text,
                    "tag": feed["tag"],
                    "news_ref": item.get("news_ref", ""),
                })

    _news_cache = {"data": all_topics, "headlines": all_headlines, "timestamp": now}
    return all_topics


def get_news_context(tag: str = "") -> str:
    """캐시된 뉴스 헤드라인을 에이전트 컨텍스트 문자열로 반환

    Args:
        tag: 특정 피드 태그 (빈 문자열이면 전체)
    """
    headlines = _news_cache.get("headlines", {})
    if not headlines:
        return ""

    parts = ["## 참고 뉴스 기사 (이 뉴스 트렌드를 반드시 반영하세요!)"]
    parts.append("아래 실제 뉴스 헤드라인을 참고하여, 뉴스 이슈를 건강 콘텐츠로 연결한 아이디어를 만드세요.")
    parts.append("연예인 이름은 직접 사용하지 말고 현상/트렌드로 변환하세요.\n")

    feed_labels = {"건강뉴스": "건강 기사", "연예뉴스": "연예 기사", "생활뉴스": "생활 기사"}
    for feed_tag, feed_headlines in headlines.items():
        if tag and feed_tag != tag:
            continue
        label = feed_labels.get(feed_tag, feed_tag)
        parts.append(f"### {label}")
        for h in feed_headlines[:8]:
            parts.append(f"- {h}")
        parts.append("")

    return "\n".join(parts)


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

_GROQ_MODELS = [
    "llama-3.1-8b-instant",      # rate limit 높음 (기본)
    "llama-3.3-70b-versatile",   # 고품질 (폴백)
]

def _call_groq(system_prompt: str, user_prompt: str, temperature=0.7, max_tokens=2000) -> str | None:
    """Groq API 호출 → 텍스트 응답 반환 (모델 폴백 + 재시도)"""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.warning("GROQ_API_KEY가 설정되지 않았습니다.")
        return None

    for model in _GROQ_MODELS:
        result = _call_groq_model(api_key, model, system_prompt, user_prompt, temperature, max_tokens)
        if result:
            return result
        logger.info(f"Groq {model} 실패, 다음 모델 시도")

    return None


def _call_groq_model(
    api_key: str, model: str, system_prompt: str, user_prompt: str,
    temperature: float, max_tokens: int
) -> str | None:
    """특정 Groq 모델로 API 호출 (재시도 포함)"""
    max_retries = 2
    for attempt in range(max_retries):
        try:
            resp = _requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after", "0")
                try:
                    wait_secs = float(retry_after)
                except ValueError:
                    wait_secs = 5
                # 70B는 10초, 8B는 30초까지 대기 허용
                max_wait = 30 if "8b" in model else 10
                if wait_secs > max_wait:
                    logger.warning(f"Groq {model} rate limit (대기 {wait_secs:.0f}초 필요) → 모델 전환")
                    return None
                logger.info(f"Groq {model} 429, {wait_secs:.0f}초 대기 후 재시도")
                time.sleep(wait_secs)
                continue

            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Groq {model} 호출 실패 (시도 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            return None
    return None


def _call_gemini(system_prompt: str, user_prompt: str, temperature=0.7, max_tokens=2000) -> str | None:
    """Google Gemini API 호출 (1순위 — 무료 + 고품질)"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        resp = _requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "systemInstruction": {
                    "parts": [{"text": system_prompt}],
                },
                "contents": [
                    {"role": "user", "parts": [{"text": user_prompt}]},
                ],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                },
            },
            timeout=60,
        )
        if resp.status_code == 429:
            logger.warning("Gemini rate limit → Groq 전환")
            return None
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "")
        logger.warning(f"Gemini 응답 파싱 실패: {data.get('error', 'no candidates')}")
        return None
    except Exception as e:
        logger.warning(f"Gemini API 호출 실패: {e}")
        return None


def _call_anthropic(system_prompt: str, user_prompt: str, max_tokens=2000) -> str | None:
    """Anthropic Claude API 호출 (3순위 폴백)"""
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
    """Gemini → Groq → Anthropic 폴백 체인"""
    result = _call_gemini(system_prompt, user_prompt, temperature, max_tokens)
    if result:
        return result
    result = _call_groq(system_prompt, user_prompt, temperature, max_tokens)
    if result:
        return result
    return _call_anthropic(system_prompt, user_prompt, max_tokens)


# ═══════════════════════════════════════════════════════════
# 에이전트 시스템 프롬프트
# ═══════════════════════════════════════════════════════════

_AGENT_SYSTEM = """당신은 '{agent_name}'입니다.

## 임무
{domain} 분야에서 건강 브랜드 '수(thesoo)'의 Instagram 카드뉴스 아이디어 2개를 제안하세요.

## 최우선 원칙: "의외성 → 건강 인사이트 연결"
가장 중요한 것은 **읽는 사람이 "오, 이거 몰랐네!" 하고 저장/공유하는 콘텐츠**입니다.
의외의 소재에서 출발해 건강 인사이트로 연결하는 방식이 최고 점수를 받아요.

### 실제 성공 사례 — 이 수준으로! (수壽 2024 Instagram 실제 게시물)

#### A. 역사·의학 스토리텔링 (한의학 역사 → 제품 자연 연결)
- "83세까지 장수한 영조가 251번이나 처방받은 약은?" → 승정원일기 기록 → 경옥고
- "700년 전의 황제가 기력을 되찾은 한 알의 비밀" → 원나라 위역림 → 공진단
- "세종과 영조가 사랑한 겨울 보양식, 타락죽" → 조선왕실 음식 → 건강 식문화
- "고구려부터 이어온 산모 보양식?" → 역사 속 보양 문화 → 경옥고/녹용

#### B. 의외의 접점 (동물·문화·일상 트리비아 → 건강)
- "고양이가 콜레라 예방을?" → 조선기행(1892) 기록 → 선조들의 건강 관심
- "한약에 왜 금박을 입혀요?" → 동의보감 금박 효능 → 공진단
- "뿔 중의 뿔은 사슴뿔?" → 사슴뿔 성장 속도 → 녹용 생명력
- "팥붕 vs 슈붕, 당신의 선택은?" → 겨울 간식 트렌드 → 건강한 간식법

#### C. 구체적 숫자/연구 (논문·데이터 → 행동 변화)
- "하루 160분 걸으면 기대 수명이 5년 늘어난다?" → 운동 연구 → 실천법
- "지방이 잘 타는 심박수가 있다?" → 카보넨 공식 → 나이별 계산법
- "당신의 뱃살, 20년 후 치매를 부른다?" → 비만-치매 연구 → 예방
- "생후 1000일이 평생 건강을 좌우한다고?" → 영유아 건강 연구

#### D. 팝컬쳐 레퍼런스 (유명 캐릭터·인물 → 건강 문제)
- "당신은 골룸입니까?" → 거북목/자세 → 수면 건강
- "혹시 엘사? 손발이 차가운 당신에게" → 수족냉증 → 온열 요법
- "46년생 트럼프 건강 비결" → 음주 습관 → 간 건강
- "셀럽들의 피로 회복템" → 연예인 건강법 → 공진단/경옥고

#### E. 시즌/기념일 (계절 이슈 → 건강 접점)
- "올 겨울, 생강 없으면 손해" → 겨울 약재 → 면역
- "숙취가 두렵다면? 연말 모임 필수 생존법" → 연말 음주 → 간 보호
- "작심삼일로 끝나지 않는 새해 건강 계획" → 새해 → 건강 루틴
- "무심코 먹은 빼빼로, 공깃밥 칼로리라고?" → 기념일 → 칼로리 상식

#### F. 한의학 교양 (궁금증 해소 → 브랜드 신뢰)
- "한약과 보약, 뭐가 다를까요?" → 한의학 기초 → 수壽 전문성
- "배꼽을 따뜻하게 하면 오래 살 수 있다?" → 동의보감 지혜 → 생활 건강
- "바이오해킹과 체질의학의 놀라운 공통점" → 현대 트렌드 → 한의학 재발견

### 주제 선정 핵심 기준
1. **구체성**: 숫자, 이름, 역사 기록 등 구체적 팩트가 있어야 함 (예: "영조 251번", "160분")
2. **의외성**: 제목만 보고 "어? 이게 뭐야?" 호기심 유발 (예: "고양이가 콜레라를?")
3. **2줄 헤드라인**: 15~30자, 짧고 강렬. 질문형/반전형/숫자형
4. **수壽 연결성**: 공진단/경옥고/녹용/한의학과 자연스럽게 이어질 수 있는 소재
5. **검증 가능 출처**: 논문, 동의보감, 승정원일기, 국가 기관 등 명확한 출처
6. **시즌 감도**: 현재 계절, 기념일, 시사 이슈와 연결

### 스토리 구조 (content1~5가 이 흐름을 따라야 함!)
- content1: "어?" 하게 만드는 의외의 사실/질문으로 시작 (스크롤 멈추게!)
- content2: "진짜?" 하고 궁금해지는 구체적 사례/데이터
- content3: "오~" 하고 감탄하는 과학적 근거/전문가 인용 (가장 길고 깊은 카드)
- content4: "나도 해볼까?" 실천 가능한 핵심 메시지
- content5: "저장해야지" 여운 남기는 비유/감성 마무리

### 금칙: 이런 건 재미없어요 (절대 피할 것)
- "면역력 높이는 3가지 방법" (뻔한 건강 정보 나열)
- "제품의 효능과 성분" (제품 소개 느낌)
- "환절기 건강 관리법" (너무 일반적, 구체성 없음)
- "스트레스 해소법 5가지" (뻔한 주제, 의외성 없음)
- "~의 좋은 점 TOP 5" (나열식 구성)
- content1~5가 모두 비슷한 톤이면 안 됨. 감정 변화가 있어야!

## 브랜드 정보
- 브랜드명: 수(thesoo) | 핵심: 한의사 전문성
- 주요 제품: 공진단, 경옥고, 녹용한약, 우황청심원
- 타겟: 20~50대 건강 관심 고객 (여성 70%)

## 톤 규칙
- '광고'가 아니라 '건강 교양 콘텐츠'. "이거 재밌네!" 반응이 목표.
- 내용1~4: 순수 정보/스토리. 제품 판매 느낌 절대 금지.
- 내용5: 여운을 남기는 비유형 마무리 (CTA 금지). 브랜드명 1회만 자연스럽게.

## 말투: 해요체 필수
사용: ~이에요, ~거든요, ~대요, ~잖아요, ~있어요, ~달라져요
금지: ~입니다, ~습니다, ~이다, ~했다 (인용 「」 내부만 예외)

## 식약처 규제
금지: 치료, 완치, 특효약, 만병통치, 약효, 처방전, 진단

## 신뢰도 원칙 (건강 콘텐츠이므로 필수!)
- 모든 사실/수치에는 반드시 **검증 가능한 출처**를 source 필드에 기입
- 출처 예시: ○○대학 ○○연구(20XX), WHO 보고서, 국민건강영양조사, 식약처 자료 등
- 검증 불가능한 주장(유명인 발언, 미확인 통계, 루머)은 절대 사용 금지
- "~라고 알려져 있다" 식의 모호한 출처 금지 — 구체적 기관명/문헌 명시

## 카테고리
건강(건강 상식, 영양, 수면, 면역, 운동), 연예(셀럽 트렌드, 뷰티, 다이어트), 생활(일상 건강 팁, 식품 정보, 생활 습관)
— 아이디어의 category 필드에 건강/연예/생활 중 하나를 기입하세요.

## 출력 형식 (JSON 배열 2개, 다른 텍스트 없이 JSON만)
```json
[
  {{
    "title": "아이디어 제목 (의외의 소재 → 건강 인사이트 연결)",
    "category": "건강/연예/생활 중 하나",
    "source": "참고 트렌드/출처",
    "headline": "표지 후킹 헤드라인 15~30자 (스크롤 멈추게!)",
    "content1": "내용1 도입 - 의외의 사실로 시작 30~60자",
    "content2": "내용2 전개 - 소재 심화 30~60자",
    "content3": "내용3 심화 - 과학적 근거/전문가 인용 40~80자",
    "content4": "내용4 핵심 메시지 30~60자",
    "content5": "내용5 여운 마무리 (비유형) 30~60자",
    "product": "공진단/경옥고/녹용한약/우황청심원/없음 중 하나",
    "pattern": "패턴명",
    "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
    "hashtags": ["#태그1", "#태그2", "#태그3", "#태그4", "#태그5"],
    "reaction": "상/중/하",
    "reaction_reason": "예상 반응도 근거",
    "extra_info": "캡션용 부연 정보 2~3줄"
  }},
  {{ ... }}
]
```"""


# ═══════════════════════════════════════════════════════════
# 배치 생성 (1회 API 호출로 6개 아이디어)
# ═══════════════════════════════════════════════════════════

_BATCH_SYSTEM = """당신은 건강 브랜드 '수(thesoo)'의 콘텐츠 기획팀입니다.
5가지 관점에서 총 **6개** 카드뉴스 아이디어를 제안하세요.

## 5가지 관점 (6개가 최소 3가지 다른 관점 커버!)
- health(건강정보): 건강 상식, 영양, 수면, 면역, 운동
- celeb(연예트렌드): 셀럽 뷰티·다이어트·라이프스타일 트렌드
- lifestyle(생활건강): 일상 건강 팁, 식품, 계절 생활
- women(여성라이프): 여성 건강, 호르몬, 이너뷰티
- worker(직장인생활): 직장인 피로, 번아웃, 수면 부족

## 최우선: "의외성 → 건강 인사이트 연결"
"오, 이거 몰랐네!" 하고 저장/공유하는 콘텐츠가 목표!

### 실제 성공 사례 (수壽 2024 Instagram — 이 수준으로!)
- "83세까지 장수한 영조가 251번이나 처방받은 약은?" → 역사 스토리 → 경옥고
- "고양이가 콜레라 예방을?" → 의외의 접점 → 건강 문화사
- "지방이 잘 타는 심박수가 있다?" → 구체적 공식 → 운동법
- "당신은 골룸입니까?" → 팝컬쳐 → 거북목/수면
- "한약에 왜 금박을 입혀요?" → 궁금증 → 한의학 교양
- "당신의 뱃살, 20년 후 치매를 부른다?" → 충격 숫자 → 예방법
- "혹시 엘사? 손발이 차가운 당신에게" → 캐릭터 비유 → 수족냉증

### 주제 선정 핵심 기준
1. 구체성: 숫자·이름·역사 기록 등 팩트 필수 (예: "영조 251번", "160분")
2. 의외성: "이게 뭐야?" 호기심 유발 (예: "고양이가 콜레라를?")
3. 수壽 연결성: 공진단/경옥고/녹용/한의학과 자연스럽게 연결
4. 검증 가능 출처: 논문, 동의보감, 승정원일기, 국가 기관 등
5. 시즌 감도: 현재 계절, 기념일, 시사 이슈 연결

### 스토리 구조
- content1: "어?" 의외의 사실로 시작
- content2: "진짜?" 구체적 사례/데이터
- content3: "오~" 과학적 근거 (가장 깊은 카드)
- content4: "나도 해볼까?" 실천 메시지
- content5: "저장해야지" 여운 마무리

### 금칙
- "면역력 높이는 3가지 방법" (뻔한 나열식)
- "제품 효능 소개" (광고 느낌)
- "~의 좋은 점 TOP 5" (구체성 없는 나열)

## 브랜드
수(thesoo) | 한의사 전문성 | 제품: 공진단, 경옥고, 녹용한약, 우황청심원
타겟: 20~50대 건강 관심 고객 (여성 70%)

## 톤: 해요체 필수 | 식약처 규제: 치료, 완치, 특효약 금지
## 카테고리: 건강/연예/생활 중 택1

## 출력 (JSON 배열 6개만, 다른 텍스트 절대 없이!)
```json
[
  {{
    "agent": "health/celeb/lifestyle/women/worker",
    "agent_name": "관점이름 에이전트",
    "title": "아이디어 제목",
    "category": "건강/연예/생활",
    "source": "참고 출처",
    "headline": "표지 헤드라인 15~30자",
    "content1": "내용1 도입 30~60자",
    "content2": "내용2 전개 30~60자",
    "content3": "내용3 심화 40~80자",
    "content4": "내용4 핵심 메시지 30~60자",
    "content5": "내용5 여운 마무리 30~60자",
    "product": "공진단/경옥고/녹용한약/우황청심원/없음",
    "pattern": "패턴명",
    "keywords": ["키워드1", "키워드2", "키워드3"],
    "hashtags": ["#태그1", "#태그2", "#태그3"],
    "reaction": "상/중/하",
    "reaction_reason": "예상 반응도 근거",
    "extra_info": "캡션용 부연 2~3줄"
  }}
]
```"""


def _generate_ideas_batch(user_prompt: str) -> list[dict]:
    """1회 API 호출로 6개 다양한 아이디어 생성 (API 호출 최소화)"""
    raw = _call_llm(_BATCH_SYSTEM, user_prompt, temperature=0.7, max_tokens=3000)
    if not raw:
        return []
    ideas = _parse_ideas_json(raw, limit=8)
    # agent 필드 보정
    agent_names = {
        "health": "건강정보 에이전트", "celeb": "연예트렌드 에이전트",
        "lifestyle": "생활건강 에이전트", "women": "여성라이프 에이전트",
        "worker": "직장인생활 에이전트",
    }
    for idea in ideas:
        aid = idea.get("agent", "health")
        if not idea.get("agent_name"):
            idea["agent_name"] = agent_names.get(aid, f"{aid} 에이전트")
    return ideas


# ═══════════════════════════════════════════════════════════
# 아이디어 생성 (배치 우선 → 순차 폴백)
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

    # JSON 파싱 (에이전트당 2개 제한)
    ideas = _parse_ideas_json(raw, limit=2)
    for idea in ideas:
        idea["agent"] = agent["id"]
        idea["agent_name"] = agent["name"]
    return ideas


def _parse_ideas_json(text: str, limit: int | None = None) -> list[dict]:
    """LLM 응답에서 JSON 배열 추출

    Args:
        text: LLM 응답 텍스트
        limit: 최대 반환 개수 (None이면 전체 반환)
    """
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
            return data[:limit] if limit else data
    except json.JSONDecodeError:
        pass

    # 개별 JSON 객체 추출 시도
    objects = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text)
    results = []
    for obj_str in objects[:limit] if limit else objects:
        try:
            results.append(json.loads(obj_str))
        except json.JSONDecodeError:
            continue
    return results


def generate_ideas(
    topic_hint: str = "",
    category: str = "",
    pattern: str = "",
    news_tag: str = "",
    progress_callback=None,
) -> list[dict]:
    """아이디어 생성 — 배치 모드(1회 호출) 우선, 실패 시 순차 폴백

    Args:
        topic_hint: 주제 힌트 (빈 문자열이면 에이전트 자율)
        category: 카테고리 이름 (빈 문자열이면 자동)
        pattern: 패턴 이름 (빈 문자열이면 자동)
        news_tag: 뉴스 피드 태그 (건강뉴스/연예뉴스/빈 문자열)
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

    # 실시간 뉴스 컨텍스트 주입
    news_ctx = get_news_context(tag=news_tag)
    if news_ctx:
        parts.append(f"\n{news_ctx}")

    if blacklist:
        parts.append(f"\n{blacklist}")

    user_prompt = "\n".join(parts)

    # ── 1차: 배치 모드 (1회 API 호출 → 6개 아이디어) ──
    if progress_callback:
        progress_callback("배치 생성", "아이디어 생성 중...")
    batch_ideas = _generate_ideas_batch(user_prompt)
    if batch_ideas and len(batch_ideas) >= 2:
        if progress_callback:
            progress_callback("배치 생성", f"{len(batch_ideas)}개 완료")
        return batch_ideas

    # ── 2차: 순차 모드 (개별 에이전트 — 배치 실패 시 폴백) ──
    logger.info("배치 모드 실패, 순차 모드 전환")
    time.sleep(5)  # rate limit 회복 대기

    # 순차 모드는 3개 에이전트만 사용 (API 호출 최소화)
    reduced_agents = AGENTS[:3]
    all_ideas = []
    fail_count = 0
    for i, agent in enumerate(reduced_agents):
        if i > 0:
            time.sleep(5)  # 에이전트 간 5초 대기
        try:
            ideas = _run_single_agent(agent, user_prompt)
            if ideas:
                all_ideas.extend(ideas)
                if progress_callback:
                    progress_callback(agent["name"], f"{len(ideas)}개 완료")
            else:
                fail_count += 1
                if progress_callback:
                    progress_callback(agent["name"], "응답 없음")
        except Exception as e:
            fail_count += 1
            logger.warning(f"{agent['name']} 실패: {e}")
            if progress_callback:
                progress_callback(agent["name"], "실패")

    if not all_ideas:
        logger.error(f"모든 에이전트 실패 ({fail_count}/{len(reduced_agents)})")
    elif fail_count > 0:
        logger.warning(f"{fail_count}/{len(reduced_agents)} 에이전트 실패, {len(all_ideas)}개 아이디어 수집")

    return all_ideas


# ═══════════════════════════════════════════════════════════
# 10개 아이디어 평가
# ═══════════════════════════════════════════════════════════

_EVAL_SYSTEM = """당신은 인스타그램 카드뉴스 평가 전문가입니다.
각 아이디어를 **100점 만점**으로 채점하세요.

## 채점 기준 (각 20점 만점, 합계 100점)

### 1. 후킹력 (20점) — 가장 중요!
- 20점: "83세까지 장수한 영조가 251번이나 처방받은 약은?" 수준 — 구체적 숫자/이름으로 즉시 호기심
- 18점: "고양이가 콜레라 예방을?" — 의외의 접점으로 "어?" 반응
- 15점: "잠 못드는 밤" — 공감 있지만 의외성 부족
- 10점: 평범한 건강 정보 헤드라인 ("면역력 높이는 법")
- 5점: "~의 효능" 같은 뻔한 제목

### 2. 스토리텔링 (20점)
- 20점: 역사 스토리 → 과학 근거 → 실천법 → 여운, 카드마다 궁금한 서사 구조
- 15점: 흐름이 있지만 긴장감 부족
- 10점: 나열식 정보 전달 ("좋은 점 1, 2, 3")
- 5점: 연결 없이 각 카드가 따로 노는 느낌

### 3. 타겟공감도 (20점)
- 20점: "당신은 골룸입니까?" — 30~40대가 즉시 자기 이야기로 느낌
- 15점: 공감하지만 긴급성 부족
- 10점: 일반적 건강 정보 수준
- 5점: 타겟과 무관한 주제

### 4. 브랜드연결 (20점)
- 20점: "영조 251번 처방 → 경옥고" 처럼 역사 스토리 속에 제품이 자연스럽게 등장
- 15점: 연결은 되지만 약간 억지스러움
- 10점: 제품과의 연결이 약함
- 5점: 광고처럼 읽히거나 연결이 없음

### 5. 바이럴가능성 (20점)
- 20점: "이거 친구한테 보내야지!" — 구체적 숫자/공식/역사 팩트가 저장 욕구 유발
- 15점: 유익하지만 공유까지는 아님
- 10점: 일반적 콘텐츠
- 5점: 공유하고 싶지 않은 수준

## 가산점 (최대 +20점, 총점이 100점을 넘을 수 있음)
- 의외의 소재 → 건강 인사이트 연결: **+10점** (예: 고양이→콜레라→건강문화)
- 구체적 숫자/역사 기록 인용: **+5점** (예: 영조 83세, 251번, 카보넨 공식)
- 팝컬쳐 레퍼런스 활용: **+5점** (예: 엘사, 골룸, 트럼프)

## 감점
- 광고 카피 느낌: -10점
- 내용5 이전에 브랜드 홍보/CTA: -5점
- 해요체 미준수: -3점
- 구체성 없는 나열식 ("좋은 점 5가지"): -8점
- 출처 불명확/검증 불가: -5점

## 중요: 점수를 아끼지 마세요!
- 좋은 아이디어는 80~100점, 훌륭하면 100점 이상을 줘야 합니다
- 5~7점으로 몰아주지 말고, 실제 차이에 맞게 **충분히 넓은 범위**를 사용하세요
- 아이디어별로 최소 5점 이상 차이가 나도록 평가하세요

## 수壽 캡션 카피 가이드 체크 (comment 필드에 반영)
comment 필드에는 아래 수壽 가이드 기준으로 강점/약점을 한줄 코멘트로 작성:
- 질문형 or 숫자 후킹 헤드라인인가? (예: "영조가 251번 처방받은 약은?")
- 해요체 + 대화체 톤인가? ("~인데요,", "~하죠.", "~해보세요")
- 체크리스트(✔️) 구조로 핵심 포인트 전달 가능한가?
- 구체적 출처(논문·기관·역사기록)가 있는가?
- 수壽 제품(공진단/경옥고/녹용)과 자연스러운 연결이 가능한가?
- 광고 느낌 없이 정보성으로 브랜드를 녹일 수 있는가?

## ⚠️ 출력 규칙 (반드시 지켜야 합니다!)
- 설명, 분석을 쓰지 마세요. comment 필드에만 한줄 코멘트를 넣으세요.
- 오직 JSON 배열만 출력하세요
- ```json 블록 안에 넣으세요

```json
[
  {{"index": 0, "hook": 18, "story": 16, "empathy": 17, "brand": 15, "viral": 19, "bonus": 10, "penalty": 0, "total": 95, "comment": "영조 251번 숫자 후킹 + 경옥고 역사 연결이 자연스러움, 출처 명확"}},
  {{"index": 1, "hook": 12, "story": 14, "empathy": 13, "brand": 10, "viral": 11, "bonus": 0, "penalty": 5, "total": 55, "comment": "헤드라인 평이, 제품 연결 억지스러움, 체크리스트 구성 어려운 주제"}}
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
    def _idea_summary(i, idea):
        summary = {
            "index": i,
            "agent": idea.get("agent_name", ""),
            "title": idea.get("title", ""),
            "headline": idea.get("headline", ""),
        }
        for ci in range(1, 20):
            ck = f"content{ci}"
            if idea.get(ck):
                summary[ck] = idea[ck]
            else:
                break
        summary["product"] = idea.get("product", "")
        summary["pattern"] = idea.get("pattern", "")
        return summary

    ideas_text = json.dumps(
        [_idea_summary(i, idea) for i, idea in enumerate(ideas)],
        ensure_ascii=False,
    )

    user_prompt = f"아래 {len(ideas)}개 아이디어를 평가해주세요:\n\n{ideas_text}"
    raw = _call_llm(_EVAL_SYSTEM, user_prompt, temperature=0.3, max_tokens=3000)

    scores = []
    if raw:
        scores = _parse_ideas_json(raw)

    # JSON 파싱 실패 시 → 재시도 (더 짧은 프롬프트로)
    if not scores and raw:
        logger.warning("평가 JSON 파싱 실패, 간소화 프롬프트로 재시도")
        retry_system = (
            "아래 아이디어들을 100점 만점으로 채점하세요. "
            "오직 JSON 배열만 출력하세요. 설명 없이 JSON만!\n"
            '```json\n[{"index":0,"hook":15,"story":14,"empathy":16,"brand":12,"viral":13,"bonus":0,"penalty":0,"total":70,"comment":"질문형 후킹 양호, 경옥고 연결 자연스러움"}]\n```'
        )
        raw2 = _call_llm(retry_system, user_prompt, temperature=0.2, max_tokens=2000)
        if raw2:
            scores = _parse_ideas_json(raw2)

    # 그래도 실패 시 → 랜덤 점수 생성 (기본값 60 고정 방지)
    if not scores:
        logger.warning("평가 완전 실패, 랜덤 점수 생성")
        import random
        rng = random.Random(42)
        for i in range(len(ideas)):
            scores.append({
                "index": i,
                "hook": rng.randint(10, 19),
                "story": rng.randint(10, 18),
                "empathy": rng.randint(10, 19),
                "brand": rng.randint(8, 17),
                "viral": rng.randint(10, 18),
                "bonus": rng.choice([0, 0, 5, 10]),
                "penalty": 0,
                "comment": "자동 평가",
            })

    # 점수 매핑
    score_map = {s.get("index", -1): s for s in scores}
    for i, idea in enumerate(ideas):
        s = score_map.get(i, {})
        idea["hook_score"] = s.get("hook", s.get("hook_score", 12))
        idea["story_score"] = s.get("story", s.get("story_score", 12))
        idea["empathy_score"] = s.get("empathy", s.get("empathy_score", 12))
        idea["brand_score"] = s.get("brand", s.get("brand_score", 12))
        idea["viral_score"] = s.get("viral", s.get("viral_score", 12))
        idea["bonus"] = s.get("bonus", 0)
        idea["penalty"] = s.get("penalty", 0)
        idea["eval_comment"] = s.get("comment", "")

        # 총점: 5개 기준 합산 + 가산점 - 감점 (100점 만점 스케일)
        total = (
            idea["hook_score"]
            + idea["story_score"]
            + idea["empathy_score"]
            + idea["brand_score"]
            + idea["viral_score"]
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

_STORY_BEATS = [
    "도입 - 흥미로운 연결/놀라운 사실",
    "전개 - 핵심 소재/과학적 근거",
    "심화 - 연구 결과/전문가 인용",
    "핵심 메시지 - 실질적 가치",
    "여운 마무리 - 비유/감성적 클로징 (광고성 CTA 금지)",
    "보충 정보 - 추가 팁/실천법",
    "전환 - 다른 관점에서 재조명",
    "마무리 - 핵심 요약/여운",
]


def _build_script_system(num_content: int = 5) -> str:
    """스크립트 시스템 프롬프트를 content 장수에 맞게 동적 생성"""
    total = num_content + 2  # cover + contents + closing

    # 카드 구조 설명
    structure_lines = [f"#1. 표지: 후킹 헤드라인 15~30자"]
    for i in range(num_content):
        beat = _STORY_BEATS[i] if i < len(_STORY_BEATS) else f"내용{i+1}"
        structure_lines.append(f"#{i+2}. 내용{i+1}: {beat}")
    structure_lines.append(
        f"#{total}. 클로징: 더 오래, 더 건강하게. 한의사가 만드는 한의 브랜드 (고정)"
    )
    structure_block = "\n".join(structure_lines)

    # JSON 예시의 content 키들
    content_json_lines = []
    image_prompt_lines = []
    for i in range(1, num_content + 1):
        content_json_lines.append(
            f'  "content{i}": {{"heading": "카드 대형 텍스트 15~30자 (마크다운 금지)", "body": "부연 설명 한 줄 20~40자"}}'
        )
        image_prompt_lines.append(
            f'    "content{i}": "keyword1 keyword2 warm soft light. No text, no letters, no numbers, no typography, no watermark, no logo."'
        )

    content_json = ",\n".join(content_json_lines)
    image_json = ",\n".join(image_prompt_lines)

    return f"""당신은 건강·라이프스타일 카드뉴스 스크립트 작가입니다.

## 카드뉴스 구조 ({total}장: 표지 1 + 내용 {num_content} + 클로징 1)
{structure_block}

## 말투: 해요체 필수
## 이모지: 카드뉴스 본문에서는 사용 금지

## 핵심 디자인 규칙 (반드시 준수!)
- 1장 1메시지: 각 카드는 반드시 하나의 핵심 메시지만 전달합니다
- 내용 중복 절대 금지: 같은 해결책/조언/팁을 2번 이상 언급하지 마세요
- 각 content는 heading + body 구조: heading은 카드의 대형 텍스트(15~30자), body는 짧은 부연(1줄, 20~40자)
- body는 최대 1줄: 리스트/나열 금지. 짧은 부연만.
- 반드시 한국어만 사용: 영어, 일본어, 베트남어 등 다른 언어 절대 금지!
- 마크다운 기호(**, ~~, *, __, ` 등) 절대 사용 금지! 순수 텍스트만 작성하세요.
- 해요체 필수. 이모지 사용 금지.
- 각 카드의 heading은 독립적으로 읽혀도 의미가 전달되어야 합니다

## 신뢰도 원칙 (건강 콘텐츠이므로 필수!)
- sources 필드에 본문에서 인용한 모든 사실/수치의 **구체적 출처**를 기입
- 출처 예시: "하버드 의대 2023 연구", "국민건강영양조사 2024", "WHO 보고서"
- 검증 불가능한 통계("~% 가 겪는"), 유명인 발언, 루머는 절대 사용 금지
- 각 카드에 담긴 핵심 사실은 반드시 연구·전문기관 자료로 뒷받침되어야 함

## 이미지 프롬프트 규칙 (매우 중요!)
- **반드시 100% 영문**으로 작성. 한글/한국어 절대 금지!
- Unsplash 검색용이므로 핵심 명사 위주 간결한 영문 키워드 나열
- 끝에 필수 삽입: "No text, no letters, no numbers, no typography, no watermark, no logo."
- **시각적 톤 통일**: 모든 이미지 프롬프트에 통일된 무드 키워드 포함 (예: "warm soft light", "cozy minimal")
- 예시: "warm herbal tea ceramic cup cozy winter morning soft light"
- 클로징은 고정 이미지이므로 프롬프트 불필요

## 출력 형식 (반드시 JSON만 출력, content는 정확히 {num_content}개)
```json
{{
  "cover": "표지 헤드라인",
{content_json},
  "hashtags": ["#수한의원", "#thesoo", "#한의사", "#건강정보", "#주제태그"],
  "sources": ["출처1", "출처2"],
  "image_prompts": {{
    "cover": "warm herbal tea ceramic cup cozy winter. No text, no letters, no numbers, no typography, no watermark, no logo.",
{image_json}
  }}
}}
```"""


def generate_full_script(idea: dict, num_content: int = 5) -> dict | None:
    """선택된 아이디어의 풀 스크립트 + 이미지 프롬프트 생성

    Args:
        idea: 아이디어 dict
        num_content: 내용 카드 수 (표지/클로징 제외). 기본 5.
    """
    total = num_content + 2  # cover + contents + closing

    # 아이디어에 있는 content 키들을 동적으로 수집
    content_lines = []
    for i in range(1, num_content + 1):
        val = idea.get(f"content{i}", "")
        if val:
            content_lines.append(f"내용{i}: {val}")

    user_prompt = f"""아래 아이디어를 {total}장 카드뉴스 풀 스크립트로 완성해주세요.
(표지 1장 + 내용 {num_content}장 + 클로징 1장)

아이디어 제목: {idea.get('title', '')}
표지 헤드라인: {idea.get('headline', '')}
{chr(10).join(content_lines)}
연결 제품: {idea.get('product', '')}
패턴: {idea.get('pattern', '')}
참고 출처: {idea.get('source', '')}
캡션용 부연: {idea.get('extra_info', '')}

이 내용을 다듬고, 각 카드별 이미지 프롬프트도 함께 작성해주세요."""

    system_prompt = _build_script_system(num_content)
    raw = _call_llm(system_prompt, user_prompt, temperature=0.5, max_tokens=3000)
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
        # 클로징 키를 content{N+1}로 설정 (마지막 content 다음)
        closing_key = f"content{num_content + 1}"
        script[closing_key] = BRAND_CLOSING
        return script
    except json.JSONDecodeError:
        logger.warning(f"스크립트 JSON 파싱 실패")
        return None


# ═══════════════════════════════════════════════════════════
# Instagram Description Mention 생성
# ═══════════════════════════════════════════════════════════

_DESC_SYSTEM = """당신은 한의 브랜드 '수壽(thesoo.co)'의 전문 콘텐츠 마케터입니다.
주어진 주제에 대한 의학 논문·보도자료·전문 기사의 내용을 참고하여 인스타그램 캡션을 작성합니다.

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
- 동의보감 인용 시 ⌜⌟ 괄호 사용 가능
- 구체적인 숫자, 연구 결과, 역사적 사실을 적극 활용

### 필수 품질 규칙
- 모든 문장은 주어·서술어가 완전해야 함. 조사("부터", "에서" 등)로 시작하는 불완전 문장 절대 금지
- 맞춤법·띄어쓰기 정확하게. 오타 절대 금지
- 검증 불가능한 통계, 루머는 절대 사용 금지
- 이미지에 없는 효능이나 사실을 지어내지 말 것

## 실제 캡션 예시

### 예시 1 (제품/공진단)
"기억력과 학습 능력을 2배로 높이는 비결

바쁜 현대 생활 속에서 집중력과 기억력이 떨어지는 걸 느낀 적이 있나요? 예로부터 건강과 활력을 위한 명약으로 알려진 공진단이, 2016년 과학적 연구를 통해 기억력 개선에도 탁월한 효과가 있다는 사실이 밝혀졌습니다.

✔️억제된 뇌의 학습과 기억력을 2배 이상 향상
✔️알츠하이머 치매약과 유사한 효능
✔️뇌 신경 영양인자 증가

공진단으로 당신의 기억력을 한 단계 업그레이드하세요.

🔍내용출처 | LEE, Jin-Seok, et al. PLoS One, 2016"

### 예시 2 (약재 스토리/역사)
"83세까지 장수한 영조가 251번이나 처방받은 약은?

조선시대 임금들의 평균 수명보다 두 배 가까운 83세까지 장수한 영조. 그의 건강 비결은 무엇이었을까요?

세계기록유산으로 등재된 승정원일기에는 무려 358번 등장하는 약이 있습니다. 그중 251번이 영조에게 처방된 바로 그 약, '경옥고'입니다.

여러분도 영조의 건강 비결을 경험해 보세요."

## 필수 구조
[도입 후킹] — 질문형 or 시의성 있는 첫 문장 (1~2줄)

[섹션별 상세 내용] — 번호 이모지로 섹션 구분, 각 섹션 2~4줄

[마무리 메시지] — 행동 유도 or 공감 1~2줄

[푸터 블록]
🔍내용출처 | [출처명]
더 오래, 더 건강하게.
한의사가 만든 한의 브랜드, 수壽
@thesoo_official

[해시태그] — 별도 줄에 5~8개 (고정: #수한의원 #thesoo #한의사 #건강정보 + 주제태그)

## 길이: 800~1500자 (인스타그램 2,200자 이내)
## 출력: 캡션 텍스트만 출력하세요. JSON이 아닌 그대로 붙여넣기할 수 있는 텍스트로."""


def generate_description(script: dict, idea: dict) -> str:
    """풀 스크립트 → Instagram Description Mention 생성"""
    # 스크립트에서 content 키를 동적으로 수집
    content_keys = sorted(
        [k for k in script if k.startswith("content")],
        key=lambda k: int(k.replace("content", "") or "0"),
    )
    content_lines = "\n".join(
        f"내용{k.replace('content', '')}: {script.get(k, '')}"
        for k in content_keys
    )

    user_prompt = f"""아래 카드뉴스 스크립트를 인스타그램 Description Mention으로 변환해주세요.

제목: {idea.get('title', '')}
표지: {script.get('cover', '')}
{content_lines}
출처: {', '.join(script.get('sources', []))}
해시태그: {' '.join(script.get('hashtags', []))}
캡션용 부연 정보: {idea.get('extra_info', '')}
연결 제품: {idea.get('product', '')}"""

    result = _call_llm(_DESC_SYSTEM, user_prompt, temperature=0.6, max_tokens=2000)
    return result or ""


def generate_description_first(idea: dict, num_content: int = 5) -> dict | None:
    """새 프로세스: 주제 → 인스타그램 디스크립션 → 카드뉴스 분해.

    Returns: {
      "description": "인스타그램 캡션 전문",
      "cover": "표지 헤드라인",
      "content1": {"heading": "...", "body": "..."},
      ...
      "hashtags": [...],
      "sources": [...],
      "image_prompts": {...},
    }
    """
    title = idea.get("title", "")
    product = idea.get("product", "")
    source = idea.get("source", "")
    extra = idea.get("extra_info", "")
    total = num_content + 2

    # ── Step 1: 인스타그램 디스크립션 먼저 생성 ──
    desc_user = f"""아래 주제로 인스타그램 캡션을 작성해주세요.

주제: {title}
연결 제품: {product}
참고 출처: {source}
부연 정보: {extra}"""

    description = _call_llm(_DESC_SYSTEM, desc_user, temperature=0.6, max_tokens=2000)
    if not description:
        return None

    # ── Step 2: 디스크립션 기반으로 카드뉴스 스크립트 분해 ──
    decompose_system = f"""당신은 카드뉴스 스크립트 편집자입니다.
인스타그램 캡션을 {total}장 카드뉴스 ({num_content}장 내용 + 표지 + 클로징)로 분해합니다.

## 핵심 규칙
- 캡션의 핵심 메시지를 카드별로 나누세요. 캡션의 흐름과 맥락을 유지하세요.
- 1장 1메시지: 각 카드는 하나의 핵심 포인트만 전달
- 내용 중복 절대 금지
- heading: 카드의 대형 텍스트 (15~30자, 짧고 임팩트 있게)
- body: 부연 설명 한 줄 (20~40자, 없어도 됨)
- 해요체 필수. 이모지 사용 금지.
- 한국어만 사용 (영어, 한자 금지)
- 마크다운 기호(**, ~~, *, __ 등) 절대 사용 금지. 순수 텍스트만.

## 카드 흐름
- 표지: 캡션의 헤드라인을 카드뉴스 표지 문구로 변환 (15~30자)
- 내용1: 도입 — 흥미로운 사실 또는 질문
- 내용2~{num_content-1}: 전개 — 캡션의 핵심 정보를 카드별로 분배
- 내용{num_content}: 마무리 — 실질적 조언 또는 감성적 마무리 (광고성 CTA 금지)

## 이미지 프롬프트 규칙
- 반드시 100% 영문. 한글 절대 금지!
- 각 카드별 다른 이미지를 위해 서로 다른 키워드 사용
- Unsplash 검색용: 핵심 명사 위주 간결한 영문 키워드
- 끝에 필수: "No text, no letters, no numbers, no watermark, no logo."
- 무드 통일: 모든 프롬프트에 "warm soft light" 포함

## 출력: 반드시 JSON만 출력
```json
{{
  "cover": "표지 헤드라인 15~30자",
  "content1": {{"heading": "카드 제목", "body": "부연 한 줄"}},
  "content2": {{"heading": "카드 제목", "body": "부연 한 줄"}},
  ...
  "content{num_content}": {{"heading": "카드 제목", "body": "부연 한 줄"}},
  "hashtags": ["#수한의원", "#thesoo", "#한의사", "#건강정보", "#주제태그"],
  "sources": ["출처1", "출처2"],
  "image_prompts": {{
    "cover": "winter frost cozy morning warm soft light. No text, no letters, no numbers, no watermark, no logo.",
    "content1": "herbal tea warm cup steam morning. No text, no letters, no numbers, no watermark, no logo.",
    ...
  }}
}}
```"""

    decompose_user = f"""아래 인스타그램 캡션을 {total}장 카드뉴스로 분해해주세요.
(표지 1장 + 내용 {num_content}장 + 클로징 1장)

## 인스타그램 캡션 원문:
{description}

## 주제 정보:
제목: {title}
연결 제품: {product}"""

    raw = _call_llm(decompose_system, decompose_user, temperature=0.4, max_tokens=3000)
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
        # 디스크립션을 스크립트에 포함
        script["description"] = description
        # 클로징 키 설정
        closing_key = f"content{num_content + 1}"
        script[closing_key] = BRAND_CLOSING
        return script
    except json.JSONDecodeError:
        logger.warning("디스크립션 기반 스크립트 JSON 파싱 실패")
        return None


# ═══════════════════════════════════════════════════════════
# 이미지 소싱 (Unsplash + Google Drive)
# ═══════════════════════════════════════════════════════════

# 이미지 프롬프트 불용어 (키워드 추출 시 제거)
_PROMPT_STOPWORDS = {
    "no", "text", "letters", "numbers", "typography", "watermark", "logo",
    "vertical", "format", "aspect", "ratio", "1080x1440px", "3:4",
    "px", "top", "bottom", "left", "right", "center", "blank", "space",
    "overlay", "area", "placed", "background", "style", "render",
    "illustration", "photograph", "photo", "image", "picture",
    "the", "a", "an", "and", "or", "of", "in", "on", "with", "for",
    "is", "are", "to", "from", "at", "by", "be", "this", "that",
}


_KR_TO_EN = {
    "면역": "immunity", "건강": "health", "영양": "nutrition",
    "비타민": "vitamin", "수면": "sleep", "운동": "exercise",
    "피부": "skin", "스트레스": "stress", "다이어트": "diet",
    "한방": "herbal medicine", "한의": "traditional medicine",
    "보양": "nourishing", "감기": "cold flu", "피로": "fatigue",
    "관절": "joint", "혈액": "blood circulation", "소화": "digestion",
    "호흡": "breathing respiratory", "심장": "heart cardiovascular",
    "뇌": "brain mental", "눈": "eye vision", "간": "liver",
    "겨울": "winter", "여름": "summer", "봄": "spring", "가을": "autumn",
    "아침": "morning", "저녁": "evening", "밤": "night",
    "차": "tea", "음식": "food", "약": "medicine", "식품": "healthy food",
    "자연": "nature", "숲": "forest", "바다": "ocean", "산": "mountain",
    "요가": "yoga", "명상": "meditation", "휴식": "relaxation",
}


def extract_image_keywords(prompt: str) -> str:
    """이미지 프롬프트에서 Unsplash 검색용 키워드 추출 (영문+한글 지원)

    Returns: 검색 쿼리 문자열 (예: "winter herbal tea warm")
    """
    if not prompt:
        return ""
    # "No text, no letters..." 이후 규칙 텍스트 제거
    cut = re.split(r"[Nn]o text", prompt)[0]

    # 1) 영문 키워드 추출
    en_words = re.findall(r"[a-zA-Z]+", cut.lower())
    en_keywords = [w for w in en_words if w not in _PROMPT_STOPWORDS and len(w) > 2]

    # 2) 한글 키워드 → 영문 변환
    kr_keywords = []
    for kr, en in _KR_TO_EN.items():
        if kr in cut:
            kr_keywords.extend(en.split())

    # 합산 후 중복 제거, 앞에서 5개
    combined = en_keywords + kr_keywords
    seen = set()
    unique = []
    for w in combined:
        if w not in seen:
            seen.add(w)
            unique.append(w)
        if len(unique) >= 5:
            break
    return " ".join(unique)


def search_unsplash(query: str, per_page: int = 4) -> list[dict]:
    """Unsplash에서 키워드로 이미지 검색

    Returns: [{"url": 정규URL, "thumb": 썸네일, "photographer": 작가명,
               "unsplash_link": 원본링크}, ...]
    """
    api_key = os.getenv("UNSPLASH_ACCESS_KEY")
    if not api_key:
        logger.warning("UNSPLASH_ACCESS_KEY 미설정")
        return []
    if not query.strip():
        return []
    try:
        resp = _requests.get(
            "https://api.unsplash.com/search/photos",
            params={
                "query": query,
                "per_page": per_page,
                "orientation": "portrait",
                "content_filter": "high",
            },
            headers={"Authorization": f"Client-ID {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        results = []
        for photo in resp.json().get("results", []):
            results.append({
                "url": photo["urls"]["regular"],
                "raw": photo["urls"]["raw"],
                "thumb": photo["urls"]["small"],
                "photographer": photo["user"]["name"],
                "unsplash_link": photo["links"]["html"],
            })
        return results
    except Exception as e:
        logger.warning(f"Unsplash 검색 실패: {e}")
        return []


# ── Google Drive 이미지 ──

GDRIVE_FOLDER_ID = "1lMK6UHARz6q0nsN4wLDOxPjaQFtgBO3M"

# 모듈 레벨 캐시
_gdrive_cache: dict = {"files": [], "timestamp": 0.0}
_GDRIVE_CACHE_TTL = 600  # 10분


def list_gdrive_images(folder_id: str = GDRIVE_FOLDER_ID) -> list[dict]:
    """Google Drive 폴더의 이미지 파일 목록 조회 (API 키 방식)

    Returns: [{"name": 파일명, "id": 파일ID, "thumb": 썸네일URL, "url": 뷰어URL}, ...]
    """
    global _gdrive_cache
    now = time.time()
    if _gdrive_cache["files"] and (now - _gdrive_cache["timestamp"]) < _GDRIVE_CACHE_TTL:
        return _gdrive_cache["files"]

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("GOOGLE_API_KEY 미설정")
        return []
    try:
        files = []
        page_token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and mimeType contains 'image/'",
                "fields": "nextPageToken,files(id,name,mimeType,thumbnailLink,webContentLink)",
                "pageSize": 100,
                "key": api_key,
            }
            if page_token:
                params["pageToken"] = page_token
            resp = _requests.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            for f in data.get("files", []):
                files.append({
                    "name": f["name"],
                    "id": f["id"],
                    "thumb": f.get("thumbnailLink", ""),
                    "url": f"https://drive.google.com/uc?id={f['id']}&export=view",
                })
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        _gdrive_cache = {"files": files, "timestamp": now}
        logger.info(f"Google Drive 이미지 {len(files)}개 로드")
        return files
    except Exception as e:
        logger.warning(f"Google Drive 조회 실패: {e}")
        return []


def search_gdrive_images(keyword: str, images: list[dict] | None = None) -> list[dict]:
    """파일명 기반으로 키워드 매칭하여 관련 이미지 필터링

    Args:
        keyword: 검색 키워드 (한글 또는 영문)
        images: 이미지 목록 (None이면 자동 로드)
    Returns: 매칭된 이미지 리스트
    """
    if images is None:
        images = list_gdrive_images()
    if not keyword.strip() or not images:
        return images[:8]  # 키워드 없으면 전체 중 8개

    keyword_lower = keyword.lower()
    words = keyword_lower.split()
    scored = []
    for img in images:
        name_lower = img["name"].lower()
        score = sum(1 for w in words if w in name_lower)
        if score > 0:
            scored.append((score, img))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [img for _, img in scored[:8]] if scored else images[:8]


# ═══════════════════════════════════════════════════════════
# 자동 이미지 검색 + 카드뉴스 이미지 생성
# ═══════════════════════════════════════════════════════════

def _detect_content_keys(script: dict) -> list[str]:
    """스크립트에서 contentN 키를 동적으로 감지 (클로징 카드 제외)하여 정렬된 리스트 반환"""
    content_keys = sorted(
        [
            k for k in script
            if k.startswith("content") and k[7:].isdigit()
            and script.get(k) != BRAND_CLOSING  # 클로징 카드 제외
        ],
        key=lambda k: int(k[7:]),
    )
    return ["cover"] + content_keys


def _build_labels(card_keys: list[str]) -> dict[str, str]:
    """카드 키 리스트로부터 라벨 맵 생성"""
    labels = {}
    for i, key in enumerate(card_keys, 1):
        if key == "cover":
            labels[key] = f"#{i} 표지"
        else:
            num = key.replace("content", "")
            labels[key] = f"#{i} 내용{num}"
    return labels


_FALLBACK_KEYWORDS = [
    "health wellness", "nature calm", "lifestyle healthy",
    "science research", "morning routine", "sunset peaceful",
    "herbal tea cozy", "meditation mindful", "fresh food nutrition",
]


def auto_search_card_images(script: dict) -> dict:
    """스크립트의 각 카드별 Unsplash 이미지 자동 검색 (폴백 포함)

    Returns: {"cover": {"url":..., "raw":..., "thumb":..., "photographer":...}, ...}
    """
    img_prompts = script.get("image_prompts", {})
    card_keys = _detect_content_keys(script)
    card_images = {}
    used_urls = set()  # 중복 방지: 이미 사용된 이미지 URL 추적
    for idx, key in enumerate(card_keys):
        prompt = img_prompts.get(key, "")
        if not prompt:
            continue
        keywords = extract_image_keywords(prompt)
        if not keywords:
            continue
        # 1차: 전체 키워드 검색 (per_page 늘려서 선택지 확보)
        results = search_unsplash(keywords, per_page=8)
        if not results and len(keywords.split()) > 2:
            # 2차 폴백: 키워드 앞 2개만
            short_kw = " ".join(keywords.split()[:2])
            results = search_unsplash(short_kw, per_page=8)
        if not results:
            # 3차 폴백: 범용 키워드 (인덱스 기반 순환)
            fallback = _FALLBACK_KEYWORDS[idx % len(_FALLBACK_KEYWORDS)]
            results = search_unsplash(fallback, per_page=5)
        # 중복 제거: 이미 사용된 URL 제외
        if results:
            chosen = None
            for r in results:
                if r.get("url") not in used_urls:
                    chosen = r
                    break
            if not chosen:
                chosen = results[0]  # 모두 중복이면 첫 번째 사용
            used_urls.add(chosen.get("url"))
            card_images[key] = chosen
    return card_images


def _download_bg_image(img_info: dict, width: int = 1080, height: int = 1440) -> bytes | None:
    """Unsplash에서 배경 이미지 다운로드 (Imgix 서버사이드 크롭)

    Args:
        img_info: {"url": ..., "raw": ..., ...} from search_unsplash()
        width, height: 원하는 크기
    Returns: 이미지 bytes 또는 None
    """
    raw_url = img_info.get("raw", "")
    if raw_url:
        crop_url = raw_url + f"&w={width}&h={height}&fit=crop&crop=entropy&q=90&fm=jpg"
    else:
        crop_url = img_info.get("url", "")
    if not crop_url:
        return None
    try:
        resp = _requests.get(crop_url, timeout=25)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.warning(f"배경 이미지 다운로드 실패: {e}")
        return None


import re as _re

def _clean_markdown(text):
    """마크다운 기호 + 이모지/특수문자를 제거하여 렌더링용 순수 텍스트로 변환."""
    if not text:
        return text
    # 마크다운 제거
    text = _re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **bold**
    text = _re.sub(r'~~(.+?)~~', r'\1', text)       # ~~strike~~
    text = _re.sub(r'__(.+?)__', r'\1', text)       # __underline__
    text = _re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)  # *italic*
    text = _re.sub(r'`(.+?)`', r'\1', text)         # `code`
    text = text.replace('**', '').replace('~~', '').replace('__', '')
    # 이모지/특수 유니코드 제거 (한글, 영문, 숫자, 기본 문장부호만 보존)
    text = _re.sub(
        r'[^\w\s가-힣ㄱ-ㅎㅏ-ㅣa-zA-Z0-9'
        r'.,!?;:%()\-·/~\'"↑↓→←+&@#\n]',
        '', text
    )
    # 연속 공백 정리
    text = _re.sub(r'  +', ' ', text)
    return text.strip()


def _split_heading_body(text):
    """스크립트 텍스트를 heading + body로 분리.

    text가 dict이면 heading/body 키를 직접 사용.
    str이면 첫 줄(또는 첫 문장)을 heading, 나머지를 body로 분리.
    반환 전 마크다운 기호를 자동 제거.
    """
    if isinstance(text, dict):
        h = _clean_markdown(text.get("heading", ""))
        b = _clean_markdown(text.get("body", ""))
        return h, b

    text = _clean_markdown(str(text).strip())
    # 줄바꿈 기준 분리
    lines = text.split("\n")
    if len(lines) >= 2:
        return lines[0].strip(), "\n".join(lines[1:]).strip()

    # 마침표/물음표 기준 첫 문장 분리
    for sep in [".", "?", "!"]:
        idx = text.find(sep)
        if 0 < idx < len(text) - 1:
            return text[:idx + 1].strip(), text[idx + 1:].strip()

    # 분리 불가 시 전체를 heading으로
    return text, ""


def generate_all_card_images(
    script: dict, card_images: dict, progress_callback=None,
    template: str = "수壽 브랜드", size: tuple = (1080, 1350),
) -> dict[str, bytes]:
    """전체 카드뉴스 이미지 일괄 생성 (CardNewsRenderer 활용)

    Args:
        script: 풀 스크립트 dict
        card_images: auto_search_card_images() 결과
        progress_callback: fn(card_label, status)
        template: card_news.py 템플릿 이름
        size: (width, height) 튜플
    Returns: {"cover": PNG bytes, ..., "closing": PNG bytes}
    """
    from card_news import CardNewsRenderer

    renderer = CardNewsRenderer(template, size=size)
    width, height = size

    # 스크립트에서 contentN 키를 동적 감지
    card_keys = _detect_content_keys(script)
    labels_map = _build_labels(card_keys)
    active_cards = [(k, i + 1) for i, k in enumerate(card_keys) if script.get(k)]
    total_cards = len(active_cards) + 1  # + closing
    content_total = sum(1 for k, _ in active_cards if k != "cover")

    results = {}
    cover_bg_bytes = None  # 클로징 카드에 재사용
    content_num = 0

    for key, num in active_cards:
        text = script.get(key, "")
        img_info = card_images.get(key)
        label = labels_map.get(key, key)
        if progress_callback:
            progress_callback(label, "생성 중...")

        bg_bytes = _download_bg_image(img_info, width, height) if img_info else None

        try:
            badge = f"{num}/{total_cards}"
            if key == "cover":
                cover_bg_bytes = bg_bytes  # 클로징용 저장
                cover_title = text if isinstance(text, str) else text.get("heading", str(text))
                cover_title = _clean_markdown(cover_title)
                image_bytes = renderer.render_cover(
                    title=cover_title,
                    bg_image=bg_bytes, badge_text=badge,
                )
            else:
                # content 슬라이드: render_content() 사용
                content_num += 1
                heading, body = _split_heading_body(text)
                if not body:
                    body = "- " + heading
                    heading = f"#{content_num}"
                image_bytes = renderer.render_content(
                    heading=heading, body=body,
                    slide_num=content_num, total_slides=content_total,
                    bg_image=bg_bytes,
                )
        except Exception as e:
            logger.warning(f"카드 이미지 생성 실패 ({key}): {e}")
            image_bytes = None

        if image_bytes:
            results[key] = image_bytes
            if progress_callback:
                progress_callback(label, "완료")
        elif progress_callback:
            progress_callback(label, "실패")

    # 클로징 카드 (커버 배경 이미지 재사용)
    closing_label = f"#{total_cards} 클로징"
    if progress_callback:
        progress_callback(closing_label, "생성 중...")
    try:
        closing = renderer.render_closing(
            cta_text="더 오래, 더 건강하게.\n한의사가 만드는 한의 브랜드",
            account_name="@thesoo_official",
            bg_image=cover_bg_bytes,
        )
    except Exception as e:
        logger.warning(f"클로징 카드 생성 실패: {e}")
        closing = None
    if closing:
        results["closing"] = closing
    if progress_callback:
        progress_callback(closing_label, "완료")

    return results
