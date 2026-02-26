import base64
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests as req
import streamlit as st

# ── Streamlit Cloud secrets → 환경 변수 브릿지 ────────────
try:
    if "api" in st.secrets:
        for key, value in st.secrets["api"].items():
            os.environ.setdefault(key, str(value))
except Exception:
    pass

# 로컬 개발용 .env 폴백
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from caption_generator import generate_caption
from cardnews_generator import (
    AGENTS, CATEGORIES, PATTERNS,
    generate_ideas, evaluate_ideas,
    generate_full_script, generate_description, generate_description_first,
    load_history, save_history, detect_season,
    suggest_topics, fetch_news_topics, get_news_context,
    extract_image_keywords, search_unsplash,
    list_gdrive_images, search_gdrive_images,
    auto_search_card_images, generate_all_card_images,
)
from figma_client import FigmaClient
from image_host import ImageHost
from instagram_client import InstagramClient
from pencil_client import PencilClient
from media_source import search_and_download, search_media, download_media, get_available_sources, check_api_status
from reels_renderer import ReelsRenderer
from reels_script_generator import generate_reels_script
from reels_video import create_reel, VOICES, DEFAULT_VOICE
from token_manager import TokenManager

ACCOUNTS_FILE = os.path.join(os.path.dirname(__file__), "accounts.json")

# ── 글로벌 CSS ──
CUSTOM_CSS = """
<style>
/* metric 카드 */
[data-testid="stMetric"] {
    background: #f8f9fa;
    border: 1px solid #e9ecef;
    border-radius: 8px;
    padding: 12px 16px;
}
[data-testid="stMetric"] label { font-size: 13px; color: #6c757d; }
[data-testid="stMetric"] [data-testid="stMetricValue"] { font-size: 22px; font-weight: 700; }

/* 게시물 카드 */
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlock"] {
    border-radius: 8px;
}

/* 탭 텍스트 */
button[data-baseweb="tab"] { font-size: 14px !important; }

/* dataframe */
[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }

/* caption 통일 */
[data-testid="stCaptionContainer"] { font-size: 13px !important; }

/* 버튼 간격 */
.stButton > button { border-radius: 6px; }

/* expander 헤더 */
[data-testid="stExpander"] summary { font-size: 14px; font-weight: 600; }

/* divider 여백 줄이기 */
[data-testid="stHorizontalBlock"] { gap: 0.5rem; }

/* info box 통일 */
[data-testid="stAlert"] { border-radius: 8px; font-size: 13px; }
</style>
"""


# ── 계정 관리 ──────────────────────────────────────────────


def _clean_account(account):
    """토큰/ID 값의 공백·개행을 제거합니다."""
    cleaned = dict(account)
    for key in ("access_token", "instagram_user_id"):
        if key in cleaned and isinstance(cleaned[key], str):
            cleaned[key] = cleaned[key].strip()
    return cleaned


def load_accounts():
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f).get("accounts", [])
            return [_clean_account(a) for a in raw]
    try:
        if "accounts" in st.secrets:
            return [_clean_account(dict(a)) for a in st.secrets["accounts"]]
    except Exception:
        pass
    return []


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"accounts": accounts}, f, ensure_ascii=False, indent=2)


# ── Slack 알림 ─────────────────────────────────────────────


def get_slack_webhook():
    """secrets 또는 환경변수에서 Slack Webhook URL을 가져옵니다."""
    try:
        if "api" in st.secrets and "SLACK_WEBHOOK_URL" in st.secrets["api"]:
            return st.secrets["api"]["SLACK_WEBHOOK_URL"]
    except Exception:
        pass
    return os.getenv("SLACK_WEBHOOK_URL", "")


def _send_slack(blocks):
    """Slack으로 메시지를 보냅니다. 실패 시 에러 메시지를 반환합니다."""
    webhook_url = get_slack_webhook()
    if not webhook_url:
        return "Webhook URL 미설정"

    try:
        resp = req.post(webhook_url, json={"blocks": blocks}, timeout=10)
        if resp.status_code != 200:
            return f"Slack 응답 {resp.status_code}: {resp.text[:100]}"
        return None
    except Exception as e:
        return f"Slack 전송 실패: {e}"


def send_slack_start(group_summaries):
    """발행 시작 알림을 Slack으로 보냅니다."""
    lines = [f"• *{g['name']}* ({g['count']}장) → {g['account']}" for g in group_summaries]
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚀 Instagram 발행 시작"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*시간:* {datetime.now().strftime('%Y-%m-%d %H:%M')}\n*총 {len(group_summaries)}개 시리즈*\n\n" + "\n".join(lines),
            },
        },
    ]
    return _send_slack(blocks)


def send_slack_notification(results):
    """발행 결과를 Slack으로 알립니다."""
    webhook_url = get_slack_webhook()
    if not webhook_url:
        return

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📸 Instagram 발행 완료"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*시간:* {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            },
        },
        {"type": "divider"},
    ]

    for r in results:
        status_emoji = "✅" if r["success"] else "❌"
        text = f"{status_emoji} *{r['group']}* ({r['count']}장) → {r.get('account_name', '')}"
        if r["success"]:
            if r.get("media_id"):
                text += f"\nMedia ID: `{r['media_id']}`"
            elif r.get("container_id"):
                text += f"\n예약 발행 | Container: `{r['container_id']}`"
            if r.get("caption"):
                caption_preview = r["caption"][:80] + ("..." if len(r["caption"]) > 80 else "")
                text += f"\n> {caption_preview}"
        else:
            text += f"\n에러: {r.get('error', '알 수 없음')}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    return _send_slack(blocks)


# ── 프레임 그룹핑 ─────────────────────────────────────────


def group_frames_by_date(frames):
    groups = defaultdict(list)
    ungrouped = []
    for f in frames:
        match = re.match(r"^(\d{6})-(\d+)$", f["name"])
        if match:
            date_key = match.group(1)
            order = int(match.group(2))
            groups[date_key].append({**f, "_order": order})
        else:
            ungrouped.append(f)

    for key in groups:
        groups[key].sort(key=lambda x: x["_order"])

    return dict(sorted(groups.items(), reverse=True)), ungrouped


def upload_bytes_to_imgbb(file_bytes, filename, expiration=86400):
    """업로드된 파일 바이트를 imgbb에 직접 업로드합니다."""
    image_data = base64.b64encode(file_bytes).decode("utf-8")
    api_key = os.getenv("IMGBB_API_KEY", "")
    if not api_key:
        try:
            api_key = st.secrets["api"]["IMGBB_API_KEY"]
        except Exception:
            pass
    payload = {
        "key": api_key,
        "image": image_data,
        "name": filename,
        "expiration": expiration,
    }
    resp = req.post("https://api.imgbb.com/1/upload", data=payload)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("success"):
        raise RuntimeError(f"imgbb 업로드 실패: {result}")
    return result["data"]["url"]


def publish_one_group(group_name, group_info, caption, scheduled_time, account, status_container):
    """하나의 그룹을 Instagram에 발행합니다. source별로 처리가 다릅니다."""
    source = group_info["source"]
    count = group_info["count"]
    result_info = {"group": group_name, "count": count, "caption": caption, "account_name": account["name"], "success": False}

    try:
        # 토큰 사전 검증
        status_container.write(f"🔑 [{group_name}] 토큰 확인 중...")
        token = account["access_token"].strip()
        uid = account["instagram_user_id"].strip()
        verify_resp = req.get(
            f"https://graph.facebook.com/v21.0/{uid}",
            params={"fields": "id", "access_token": token},
            timeout=10,
        )
        if verify_resp.status_code != 200:
            err = verify_resp.json().get("error", {}).get("message", verify_resp.text)
            raise RuntimeError(f"토큰 검증 실패: {err}")

        # 소스별 이미지 공개 URL 준비
        if source == "figma":
            node_ids = group_info["node_ids"]

            status_container.write(f"📐 [{group_name}] Figma에서 이미지 추출 중...")
            figma = FigmaClient()
            image_urls = figma.export_images(node_ids, fmt="png", scale=2)

            status_container.write(f"⬇️ [{group_name}] 이미지 다운로드 중...")
            figma.download_images(image_urls)
            ordered_files = []
            for nid in node_ids:
                safe = nid.replace(":", "-")
                path = os.path.join("downloads", f"frame_{safe}.png")
                if os.path.exists(path):
                    ordered_files.append(path)

            status_container.write(f"☁️ [{group_name}] imgbb 업로드 중...")
            host = ImageHost()
            public_urls = host.upload_batch(ordered_files, expiration=86400)

        elif source == "upload":
            files = group_info["files"]
            status_container.write(f"☁️ [{group_name}] imgbb 업로드 중 ({len(files)}장)...")
            public_urls = []
            for i, f in enumerate(files):
                status_container.write(f"☁️ [{group_name}] 업로드 {i+1}/{len(files)}: {f['name']}")
                url = upload_bytes_to_imgbb(f["bytes"], f["name"])
                public_urls.append(url)

        elif source == "url":
            public_urls = list(group_info["urls"])
            status_container.write(f"🔗 [{group_name}] URL {len(public_urls)}개 확인됨")

        else:
            raise ValueError(f"알 수 없는 소스: {source}")

        # Instagram 발행
        status_container.write(f"📸 [{group_name}] Instagram에 발행 중...")
        ig = InstagramClient()
        ig.user_id = uid
        ig.access_token = token

        if len(public_urls) == 1:
            result = ig.publish_single(public_urls[0], caption, scheduled_time)
        else:
            result = ig.publish_carousel(public_urls, caption, scheduled_time)

        result_info["success"] = True
        if result["status"] == "published":
            result_info["media_id"] = result["media_id"]
        else:
            result_info["container_id"] = result["container_id"]

    except Exception as e:
        result_info["error"] = str(e)

    return result_info


# ── 인사이트 페이지 ──────────────────────────────────────


def _fmt_type(post):
    """게시물 포맷 텍스트를 반환합니다."""
    if post.get("media_product_type") == "REELS":
        return "릴스"
    return {"IMAGE": "이미지", "VIDEO": "동영상", "CAROUSEL_ALBUM": "캐러셀"}.get(post.get("media_type", ""), "기타")


def render_cardnews_page():
    """카드뉴스 생성 페이지를 렌더링합니다."""
    st.markdown("##### 카드뉴스 스크립트 생성")
    st.caption("다양한 관점의 카드뉴스 아이디어를 생성하고, 평가를 통해 Top 스크립트를 선정합니다.")

    # ── 세션 초기화 ──
    if "cn_ideas" not in st.session_state:
        st.session_state.cn_ideas = []
    if "cn_scripts" not in st.session_state:
        st.session_state.cn_scripts = {}
    if "cn_descriptions" not in st.session_state:
        st.session_state.cn_descriptions = {}

    # ── Step 1: 설정 ──
    st.markdown("---")
    st.markdown("###### Step 1. 설정")

    # ── 상태 초기화 ──
    if "cn_news_tag" not in st.session_state:
        st.session_state.cn_news_tag = ""
    if "cn_news_loaded" not in st.session_state:
        st.session_state.cn_news_loaded = False
    if "cn_news_topics" not in st.session_state:
        st.session_state.cn_news_topics = []

    # on_click 콜백: 추천 주제 / 뉴스 토픽 클릭 시 text_input에 직접 반영
    def _set_topic(topic: str, news_tag: str = ""):
        st.session_state["cn_topic_input"] = topic
        st.session_state.cn_news_tag = news_tag

    col_topic, col_cat, col_pat = st.columns(3)
    with col_topic:
        topic_hint = st.text_input(
            "주제 힌트 (선택)",
            key="cn_topic_input",
            placeholder="예: 봄철 피로, 수면 부족, 사향...",
            help="빈칸이면 에이전트가 자율적으로 주제를 선정합니다",
        )
    with col_cat:
        cat_options = ["자동 선택"] + [c["name"] for c in CATEGORIES]
        selected_cat = st.selectbox("카테고리", cat_options)
    with col_pat:
        pat_options = ["자동 선택"] + [p["name"] for p in PATTERNS]
        selected_pat = st.selectbox("패턴", pat_options)

    # ── 추천 주제 (시즌/절기/트렌드/뉴스 통합) ──
    sug_header_col, sug_refresh_col = st.columns([6, 1])
    with sug_header_col:
        # 마지막 업데이트 시각
        from cardnews_generator import _news_cache
        last_ts = _news_cache.get("gtrend_ts", 0) or _news_cache.get("xtrend_ts", 0)
        if last_ts:
            from datetime import datetime as _dt
            updated = _dt.fromtimestamp(last_ts).strftime("%H:%M")
            st.caption(f"📌 추천 주제 — 점수순 · 클릭하면 자동 입력 · 🕐 {updated} 업데이트")
        else:
            st.caption("📌 추천 주제 — 점수순 · 클릭하면 주제 힌트에 자동 입력")
    with sug_refresh_col:
        if st.button("🔄", key="cn_refresh_all", help="추천 주제 + 트렌드 새로고침"):
            from cardnews_generator import _news_cache, _trend_convert_cache
            # 모든 캐시 완전 초기화
            _news_cache.clear()
            _news_cache["timestamp"] = 0.0
            _trend_convert_cache.clear()
            # 새로고침 시드 변경용
            st.session_state["sug_refresh_count"] = st.session_state.get("sug_refresh_count", 0) + 1
            st.rerun()

    suggestions = suggest_topics(
        include_news=True,
        refresh_seed=st.session_state.get("sug_refresh_count", 0),
    )
    if suggestions:
        # 콤팩트 칩 레이아웃: 5열 × 최대 4행 = 20개
        display = suggestions[:20]
        _src_emoji = {
            "monthly": "📅", "solar": "🗓️", "season": "🌿",
            "trend": "🔥", "news": "📰",
            "google_trend": "🔍", "google_trend_general": "🔍",
            "x_trend": "𝕏",
            "naver_trend": "🅽", "naver_trend_general": "🅽",
        }
        num_cols = 5
        for row_start in range(0, len(display), num_cols):
            row_items = display[row_start:row_start + num_cols]
            cols = st.columns(num_cols)
            for idx_in_row, sug in enumerate(row_items):
                global_idx = row_start + idx_in_row
                with cols[idx_in_row]:
                    score = sug.get("score", 0)
                    src = sug.get("source_type", "")
                    emoji = _src_emoji.get(src, "📌")
                    score_color = "#e74c3c" if score >= 80 else "#f39c12" if score >= 60 else "#95a5a6"
                    clean_topic = sug["topic"].replace("**", "").replace("*", "").replace("__", "")
                    topic_short = clean_topic[:22] + ("…" if len(clean_topic) > 22 else "")
                    reason = sug.get("reason", "")[:25]
                    product = sug.get("product", "")
                    prod_txt = f" · {product}" if product and product != "없음" else ""
                    news_tag = sug["tag"] if src == "news" else ""
                    # 콤팩트 카드: 점수+태그 한줄, 제목, 사유+제품 한줄, 버튼
                    st.markdown(
                        f"<div style='border:1px solid #e0e0e0;border-radius:8px;padding:8px 10px;margin-bottom:4px'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:2px'>"
                        f"<span style='font-size:11px;color:#999'>{emoji} {sug['tag'][:6]}</span>"
                        f"<span style='font-size:11px;font-weight:700;color:{score_color}'>{score}</span></div>"
                        f"<div style='font-size:13px;font-weight:600;line-height:1.3;margin-bottom:3px'>{topic_short}</div>"
                        f"<div style='font-size:10px;color:#aaa;line-height:1.2'>{reason}{prod_txt}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.button(
                        "선택",
                        key=f"cn_sug_{global_idx}",
                        use_container_width=True,
                        on_click=_set_topic,
                        args=(sug["topic"], news_tag),
                    )

    # 현재 계절/절기 표시
    season = detect_season()
    history = load_history()
    col_info1, col_info2 = st.columns(2)
    with col_info1:
        season_text = f"{season['season_kr']} ({season['theme']})"
        if season.get("solar_term"):
            season_text += f" | 절기: {season['solar_term']}"
        st.info(f"현재 계절: {season_text}")
    with col_info2:
        past_count = len(history.get("selected_ideas", []))
        st.info(f"히스토리: {past_count}개 선정작 (중복 방지 적용)")

    # ── 뉴스 컨텍스트 표시 ──
    news_tag_val = st.session_state.get("cn_news_tag", "")
    news_ctx_preview = get_news_context(tag=news_tag_val)
    if news_ctx_preview:
        if news_tag_val:
            label = {"건강뉴스": "건강 기사", "연예뉴스": "연예 기사", "생활뉴스": "생활 기사"}.get(news_tag_val, "뉴스")
            st.success(f"📰 **{label}** 뉴스 컨텍스트가 아이디어 생성에 반영됩니다.")
        else:
            st.success("📰 **실시간 뉴스 트렌드**가 아이디어 생성에 자동 반영됩니다.")

    # ── 아이디어 생성 버튼 ──
    if st.button("아이디어 생성", type="primary", use_container_width=True):
        cat_val = "" if selected_cat == "자동 선택" else selected_cat
        pat_val = "" if selected_pat == "자동 선택" else selected_pat

        progress_bar = st.progress(0, text="아이디어 생성 중...")
        agent_status = {}

        def on_progress(agent_name, status):
            agent_status[agent_name] = status
            progress_bar.progress(
                min(len(agent_status) / 3, 0.9),
                text=f"진행 중... ({status})",
            )

        with st.spinner("아이디어를 생성하고 있습니다..."):
            ideas = generate_ideas(
                topic_hint=topic_hint,
                category=cat_val,
                pattern=pat_val,
                news_tag=news_tag_val,
                progress_callback=on_progress,
            )
        progress_bar.progress(1.0, text="아이디어 생성 완료!")

        if not ideas:
            st.error("아이디어 생성 실패 — API rate limit 가능성이 높습니다.")
            st.info("1~2분 후 다시 시도해주세요. (Groq 무료 플랜은 분당 호출 제한이 있습니다)")
        else:
            st.success(f"{len(ideas)}개 아이디어 생성 완료! 평가 중...")
            time.sleep(3)  # eval 호출 전 rate limit 여유
            with st.spinner("아이디어 경쟁 평가 중..."):
                ideas = evaluate_ideas(ideas)
            st.session_state.cn_ideas = ideas
            st.session_state.cn_scripts = {}
            st.session_state.cn_descriptions = {}
            st.rerun()

    # ── Step 2: 아이디어 평가 결과 ──
    ideas = st.session_state.cn_ideas
    if not ideas:
        st.caption("아이디어를 생성하면 여기에 결과가 표시됩니다.")
        return

    st.markdown("---")
    st.markdown(f"###### Step 2. {len(ideas)}개 아이디어 평가 결과")

    # 요약 테이블
    table_data = []
    for idea in ideas:
        dup_mark = "중복" if idea.get("is_duplicate") else ""
        table_data.append({
            "순위": idea.get("rank", "-"),
            "에이전트": idea.get("agent_name", ""),
            "제목": idea.get("title", "")[:30],
            "표지": idea.get("headline", "")[:25],
            "제품": idea.get("product", ""),
            "총점": idea.get("total_score", 0),
            "중복": dup_mark,
        })
    st.dataframe(table_data, use_container_width=True, hide_index=True)

    # 상세 보기 (expander)
    for idea in ideas:
        rank = idea.get("rank", "?")
        dup_tag = " [중복]" if idea.get("is_duplicate") else ""
        with st.expander(f"#{rank} | {idea.get('title', '')}{dup_tag} — {idea.get('total_score', 0)}점"):
            cols = st.columns(5)
            labels = ["후킹력", "스토리텔링", "타겟공감도", "브랜드연결", "바이럴"]
            keys = ["hook_score", "story_score", "empathy_score", "brand_score", "viral_score"]
            for col, label, key in zip(cols, labels, keys):
                col.metric(label, f"{idea.get(key, 0)}/20")

            if idea.get("bonus"):
                st.caption(f"가산점: +{idea['bonus']}")
            if idea.get("penalty"):
                st.caption(f"감점: -{idea['penalty']}")
            if idea.get("eval_comment"):
                st.caption(f"평가: {idea['eval_comment']}")
            if idea.get("is_duplicate"):
                st.warning(f"중복 사유: {idea.get('dup_reason', '')}")

            st.markdown(f"**표지**: {idea.get('headline', '')}")
            for ci in range(1, 20):
                ck = f"content{ci}"
                if idea.get(ck):
                    st.markdown(f"**내용{ci}**: {idea.get(ck, '')}")
                else:
                    break
            st.markdown(f"**제품**: {idea.get('product', '')} | **패턴**: {idea.get('pattern', '')}")
            if idea.get("hashtags"):
                st.caption(" ".join(idea["hashtags"]))

    # ── Step 3: 스크립트 생성 ──
    st.markdown("---")
    st.markdown("###### Step 3. 스크립트 생성")

    # 선택 (기본 Top 2)
    non_dup = [i for i, idea in enumerate(ideas) if not idea.get("is_duplicate")]
    default_sel = non_dup[:2] if len(non_dup) >= 2 else non_dup[:1]

    select_options = [
        f"#{idea.get('rank', i+1)} {idea.get('title', '')[:25]} ({idea.get('total_score', 0)}점)"
        for i, idea in enumerate(ideas)
        if not idea.get("is_duplicate")
    ]
    non_dup_ideas = [idea for idea in ideas if not idea.get("is_duplicate")]

    if not select_options:
        st.warning("중복이 아닌 아이디어가 없습니다. 다시 생성해주세요.")
        return

    sel_col, slide_col = st.columns([3, 1])
    with sel_col:
        selected = st.multiselect(
            "스크립트를 생성할 아이디어 선택",
            select_options,
            default=select_options[:min(2, len(select_options))],
        )
    with slide_col:
        num_content = st.slider(
            "내용 카드 수", min_value=3, max_value=8, value=5,
            help="표지 + 내용N장 + 클로징 = 총 장수",
        )
        st.caption(f"총 {num_content + 2}장 (표지+내용{num_content}+클로징)")

    gen_mode = st.radio(
        "생성 방식",
        ["디스크립션 우선 (권장)", "기존 방식"],
        horizontal=True,
        help="디스크립션 우선: 인스타그램 캡션을 먼저 작성 → 카드뉴스로 분해. 맥락·가독성이 더 좋습니다.",
    )

    if st.button("선택 아이디어 스크립트 생성", type="primary"):
        for sel_text in selected:
            # 순위 번호 추출
            rank_match = re.match(r"#(\d+)", sel_text)
            if not rank_match:
                continue
            rank = int(rank_match.group(1))
            idea = next((x for x in ideas if x.get("rank") == rank), None)
            if not idea:
                continue

            if gen_mode == "디스크립션 우선 (권장)":
                with st.spinner(f"#{rank} 인스타그램 디스크립션 작성 → 카드뉴스 분해 중..."):
                    script = generate_description_first(idea, num_content=num_content)
                    if script:
                        desc = script.pop("description", "")
                        st.session_state.cn_scripts[rank] = script
                        card_imgs = auto_search_card_images(script)
                        st.session_state[f"cn_card_images_{rank}"] = card_imgs
                        st.session_state.cn_descriptions[rank] = desc
                        st.success(f"#{rank} 디스크립션 → 스크립트 → 이미지 완료")
                    else:
                        st.error(f"#{rank} 생성 실패")
            else:
                with st.spinner(f"#{rank} '{idea.get('title', '')[:20]}...' 스크립트 생성 중..."):
                    script = generate_full_script(idea, num_content=num_content)
                    if script:
                        st.session_state.cn_scripts[rank] = script
                        card_imgs = auto_search_card_images(script)
                        st.session_state[f"cn_card_images_{rank}"] = card_imgs
                        desc = generate_description(script, idea)
                        st.session_state.cn_descriptions[rank] = desc
                        st.success(f"#{rank} 스크립트 + 이미지 + Description 완료")
                    else:
                        st.error(f"#{rank} 스크립트 생성 실패")
        st.rerun()

    # ── 스크립트 결과 표시 ──
    scripts = st.session_state.cn_scripts
    descriptions = st.session_state.cn_descriptions

    if scripts:
        st.markdown("---")
        st.markdown("###### 완성된 스크립트")

        tabs = st.tabs([f"#{rank}위 스크립트" for rank in sorted(scripts.keys())])
        for tab, rank in zip(tabs, sorted(scripts.keys())):
            with tab:
                script = scripts[rank]
                idea = next((x for x in ideas if x.get("rank") == rank), {})

                st.markdown(f"**{idea.get('title', '')}** | {idea.get('agent_name', '')} | {idea.get('total_score', 0)}점")

                # 스크립트 테이블 (동적 장수)
                content_keys = sorted(
                    [k for k in script if k.startswith("content") and k[7:].isdigit()],
                    key=lambda k: int(k[7:]),
                )
                total_slides = len(content_keys) + 1  # +1 for cover
                st.markdown(f"**카드뉴스 스크립트 ({total_slides}장)**")
                card_data = [{"카드": "#1 표지", "스크립트": script.get("cover", "")}]
                for i, ck in enumerate(content_keys, 2):
                    val = script.get(ck, "")
                    if isinstance(val, dict):
                        val = f"{val.get('heading', '')} | {val.get('body', '')}"
                    card_data.append({"카드": f"#{i} 내용{ck[7:]}", "스크립트": val})
                st.dataframe(card_data, use_container_width=True, hide_index=True)

                # 카드뉴스 이미지 생성
                card_images = st.session_state.get(f"cn_card_images_{rank}", {})
                if card_images:
                    with st.expander("카드뉴스 이미지", expanded=True):
                        # 자동 검색된 배경 이미지 미리보기
                        st.caption("Unsplash에서 자동 검색된 배경 이미지")
                        _CLABELS = {f"content{i}": f"#{i+1} 내용{i}" for i in range(1, 20)}
                        _CLABELS["cover"] = "#1 표지"
                        preview_cols = st.columns(min(len(card_images), 6))
                        for idx, (key, img_info) in enumerate(card_images.items()):
                            with preview_cols[idx % len(preview_cols)]:
                                st.image(img_info["thumb"], use_container_width=True)
                                st.caption(f"{_CLABELS.get(key, key)}\nby {img_info['photographer']}")

                        # 카드뉴스 이미지 생성 버튼
                        gen_key = f"cn_generated_cards_{rank}"
                        n_slides = total_slides + 1  # +1 for closing
                        if st.button(f"카드뉴스 이미지 생성 ({n_slides}장)", key=f"gen_cards_{rank}", type="primary", use_container_width=True):
                            gen_progress = st.progress(0, text="카드 이미지 생성 중...")
                            gen_status = {}

                            def _on_gen_progress(label, status):
                                gen_status[label] = status
                                gen_progress.progress(
                                    min(len(gen_status) / n_slides, 0.99),
                                    text=f"{label} {status}",
                                )

                            with st.spinner(f"카드뉴스 이미지 {n_slides}장을 생성하고 있습니다..."):
                                generated = generate_all_card_images(script, card_images, _on_gen_progress)
                            gen_progress.progress(1.0, text=f"{len(generated)}장 생성 완료!")
                            st.session_state[gen_key] = generated
                            st.rerun()

                        # 생성된 카드 이미지 표시
                        if gen_key in st.session_state and st.session_state[gen_key]:
                            generated = st.session_state[gen_key]
                            st.markdown(f"**생성된 카드뉴스 ({len(generated)}장)**")
                            display_order = ["cover"] + [f"content{i}" for i in range(1, 20) if f"content{i}" in generated] + ["closing"]
                            display_labels = {**_CLABELS, "closing": f"#{len(display_order)} 클로징"}

                            # 3열 그리드
                            for row_start in range(0, len(display_order), 3):
                                row_keys = [k for k in display_order[row_start:row_start+3] if k in generated]
                                if not row_keys:
                                    continue
                                g_cols = st.columns(len(row_keys))
                                for col, key in zip(g_cols, row_keys):
                                    with col:
                                        st.image(generated[key], caption=display_labels.get(key, key), use_container_width=True)

                            # ZIP 다운로드
                            import zipfile
                            from io import BytesIO
                            zip_buf = BytesIO()
                            with zipfile.ZipFile(zip_buf, "w") as zf:
                                for key, img_bytes in generated.items():
                                    label = display_labels.get(key, key).replace("#", "").replace(" ", "_")
                                    zf.writestr(f"card_{label}.png", img_bytes)
                            st.download_button(
                                "전체 카드 이미지 다운로드 (ZIP)",
                                data=zip_buf.getvalue(),
                                file_name=f"cardnews_{rank}위_{idea.get('title', '')[:10]}.zip",
                                mime="application/zip",
                                use_container_width=True,
                            )

                # Description Mention
                desc = descriptions.get(rank, "")
                if desc:
                    with st.expander("Instagram Description Mention"):
                        st.text_area(
                            "캡션 (복사용)",
                            value=desc,
                            height=400,
                            key=f"cn_desc_{rank}",
                        )
                        st.caption(f"글자수: {len(desc)} / 2,200자")

                # 복사용 JSON
                col_dl, col_save = st.columns(2)
                with col_dl:
                    export = {
                        "idea": {
                            "title": idea.get("title", ""),
                            "agent": idea.get("agent", ""),
                            "product": idea.get("product", ""),
                            "pattern": idea.get("pattern", ""),
                            "total_score": idea.get("total_score", 0),
                        },
                        "script": script,
                        "description": desc,
                    }
                    st.download_button(
                        "JSON 다운로드",
                        data=json.dumps(export, ensure_ascii=False, indent=2),
                        file_name=f"cardnews_{rank}_{datetime.now().strftime('%y%m%d')}.json",
                        mime="application/json",
                        key=f"cn_dl_{rank}",
                    )
                with col_save:
                    if st.button(f"히스토리 저장 (#{rank})", key=f"cn_save_{rank}"):
                        save_entry = {
                            "date": datetime.now().strftime("%Y-%m-%d"),
                            "rank": rank,
                            "agent": idea.get("agent", ""),
                            "title": idea.get("title", ""),
                            "headline": idea.get("headline", script.get("cover", "")),
                            "product": idea.get("product", ""),
                            "pattern": idea.get("pattern", ""),
                            "keywords": idea.get("keywords", []),
                        }
                        save_history(save_entry)
                        st.success(f"#{rank} 아이디어가 히스토리에 저장되었습니다.")


# ═════════════════════════════════════════════════════════════════════════════
# 릴스 생성 페이지
# ═════════════════════════════════════════════════════════════════════════════


def render_reels_page():
    """🎬 릴스 생성 페이지 — GIF/영상 배경 + 유머 스크립트."""
    st.markdown("##### 🎬 릴스 생성 — 1분건강톡")
    st.caption("주제 → AI 스크립트(유머+밈) → GIF/영상 배경 → 나레이션 → 영상 합성")

    # ── 미디어 소스 상태 ──
    sources = get_available_sources()
    active_sources = [k for k, v in sources.items() if v]
    source_labels = {"giphy": "🎭 GIPHY GIF", "tenor": "🎵 Tenor GIF", "pexels": "🎬 Pexels Video", "unsplash": "📷 Unsplash"}
    st.caption(f"미디어 소스: {' · '.join(source_labels.get(s, s) for s in active_sources)}")

    # ── 채널 인사이트 ──
    _insights_path = os.path.join(os.path.dirname(__file__), "assets", "1min_health", "insights_summary.json")
    if os.path.exists(_insights_path):
        with open(_insights_path) as _f:
            _insights = json.load(_f)
        with st.expander("📊 바이럴 성공 공식 (66개 릴스 분석)", expanded=False):
            _acct = _insights.get("account", {})
            ic1, ic2, ic3, ic4 = st.columns(4)
            ic1.metric("팔로워", f'{_acct.get("followers", 0):,}')
            ic2.metric("총 릴스", f'{_acct.get("total_reels", 0)}개')
            ic3.metric("총 조회수", f'{_acct.get("total_views", 0):,}')
            ic4.metric("평균 조회수", f'{_acct.get("avg_views", 0):,}')
            st.markdown("**Hook 기법**: 숫자(33%) · 질문(29%) · 충격(18%) · 공감 저격(11%)")
            st.caption("스크립트 생성 시 이 패턴들이 자동 반영됩니다.")

    # ── 세션 초기화 ──
    for key, default in [("rl_script", None), ("rl_frames", None), ("rl_result", None), ("rl_media", None)]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── Step 1: 주제 설정 ──
    st.markdown("---")
    st.markdown("###### Step 1. 주제 설정")

    def _set_reels_topic(topic: str):
        st.session_state["rl_topic_input"] = topic

    topic = st.text_input("릴스 주제", key="rl_topic_input",
                          placeholder="예: 겨울철 일교차 건강관리, 수면 부족 해결법...")
    st.caption("AI가 나레이션 분량에 맞게 씬 수를 자동 결정합니다 (30~60초)")
    num_slides = None  # LLM이 동적으로 결정

    with st.expander("📌 추천 주제 (클릭하면 자동 입력)", expanded=False):
        suggestions = suggest_topics(include_news=True)
        if suggestions:
            display = suggestions[:15]
            _src_emoji = {"monthly": "📅", "solar": "🗓️", "season": "🌿", "trend": "🔥", "news": "📰",
                          "google_trend": "🔍", "google_trend_general": "🔍", "x_trend": "𝕏",
                          "naver_trend": "🅽", "naver_trend_general": "🅽"}
            for row_start in range(0, len(display), 5):
                row_items = display[row_start:row_start + 5]
                cols = st.columns(5)
                for idx_in_row, sug in enumerate(row_items):
                    global_idx = row_start + idx_in_row
                    with cols[idx_in_row]:
                        emoji = _src_emoji.get(sug.get("source_type", ""), "📌")
                        clean = sug["topic"].replace("**", "").replace("*", "")
                        short = clean[:18] + ("…" if len(clean) > 18 else "")
                        st.markdown(
                            f"<div style='border:1px solid #e0e0e0;border-radius:6px;padding:6px 8px;margin-bottom:2px'>"
                            f"<span style='font-size:11px;color:#999'>{emoji}</span> "
                            f"<span style='font-size:12px;font-weight:600'>{short}</span></div>",
                            unsafe_allow_html=True)
                        st.button("선택", key=f"rl_sug_{global_idx}", use_container_width=True,
                                  on_click=_set_reels_topic, args=(sug["topic"],))

    # ── Step 2: 스크립트 생성 ──
    st.markdown("---")
    st.markdown("###### Step 2. 릴스 스크립트 (유머 + GIF 매칭)")

    if st.button("🎬 스크립트 생성", type="primary", use_container_width=True, disabled=not topic):
        with st.spinner("밈+유머 스크립트 생성 중..."):
            script = generate_reels_script(topic, num_slides=num_slides)
        if script:
            st.session_state.rl_script = script
            st.session_state.rl_frames = None
            st.session_state.rl_result = None
            st.session_state.rl_media = None
            st.success(f"스크립트 생성 완료! ({len(script.get('slides', []))}장)")
            st.rerun()
        else:
            st.error("스크립트 생성 실패 — 잠시 후 다시 시도해주세요.")

    script = st.session_state.rl_script
    if not script:
        st.caption("주제를 입력하고 스크립트를 생성하면 여기에 결과가 표시됩니다.")
        return

    st.markdown(f"**{script.get('title', '')}**")
    slides = script.get("slides", [])

    # 스크립트 미리보기 (media_query 포함)
    slide_data = []
    for i, s in enumerate(slides):
        slide_data.append({
            "#": i + 1,
            "타입": {"hook": "🎣 Hook", "content": "📄", "closing": "👋"}.get(s["type"], s["type"]),
            "나레이션": s.get("narration", "")[:50],
            "화면": s.get("display_text", "").replace("\n", " | ")[:30],
            "미디어": f'{s.get("media_type", "gif")} | {s.get("media_query", "")[:25]}',
        })
    st.dataframe(slide_data, use_container_width=True, hide_index=True)

    if script.get("hashtags"):
        st.caption(" ".join(script["hashtags"][:10]))

    with st.expander("스크립트 JSON 편집"):
        edited_json = st.text_area("JSON", value=json.dumps(script, ensure_ascii=False, indent=2),
                                   height=300, key="rl_script_editor")
        if st.button("스크립트 업데이트", key="rl_script_update"):
            try:
                updated = json.loads(edited_json)
                st.session_state.rl_script = updated
                st.session_state.rl_frames = None
                st.session_state.rl_result = None
                st.session_state.rl_media = None
                st.success("스크립트 업데이트 완료")
                st.rerun()
            except json.JSONDecodeError as e:
                st.error(f"JSON 파싱 오류: {e}")

    # ── Step 3: 나레이션 & 영상 생성 ──
    st.markdown("---")
    st.markdown("###### Step 3. GIF/영상 배경 + 나레이션 + 영상 합성")

    col_voice, col_intro, col_bumper = st.columns(3)
    with col_voice:
        voice_name = st.selectbox("TTS 음성", list(VOICES.keys()), index=0)
        voice_id = VOICES[voice_name]
    with col_intro:
        inc_intro = st.checkbox("인트로 포함", value=False, help="INTRO.mp4 (기본 비활성 — 본론부터 시작)")
    with col_bumper:
        inc_bumper = st.checkbox("범퍼 포함", value=True, help="BUMPER.mov")

    if st.button("🎬 릴스 영상 생성", type="primary", use_container_width=True):
        script = st.session_state.rl_script
        slides = script.get("slides", [])

        progress_bar = st.progress(0, text="준비 중...")
        status_text = st.empty()

        def _progress(pct, msg):
            progress_bar.progress(min(pct, 0.99), text=msg)
            status_text.caption(msg)

        # Phase 1: GIF/영상 미디어 검색 + 다운로드
        _progress(0.0, "GIF/영상 미디어 검색 중...")
        media_data = []  # [(bytes, metadata), ...]
        for i, slide in enumerate(slides):
            query = slide.get("media_query", "") or slide.get("image_prompt", "")
            media_type = slide.get("media_type", "gif")

            if not query or slide.get("type") == "closing":
                media_data.append((None, None))
            else:
                m_bytes, m_info = search_and_download(query, preferred_type=media_type)
                if m_bytes and m_info:
                    media_data.append((m_bytes, m_info))
                    _progress(0.02 + (i / len(slides)) * 0.13,
                              f"미디어 {i + 1}/{len(slides)}: {m_info['type']}/{m_info.get('source', '?')}")
                else:
                    media_data.append((None, None))
                    _progress(0.02 + (i / len(slides)) * 0.13,
                              f"미디어 {i + 1}/{len(slides)}: 폴백 (단색 배경)")

        st.session_state.rl_media = media_data

        # Phase 2: 텍스트 오버레이 렌더링
        _progress(0.15, "텍스트 오버레이 렌더링 중...")
        renderer = ReelsRenderer()
        overlay_images = renderer.render_overlays(slides)
        st.session_state.rl_frames = overlay_images
        _progress(0.20, f"오버레이 {len(overlay_images)}장 렌더링 완료")

        # Phase 3: 나레이션 + 영상 합성
        import tempfile
        output_dir = tempfile.mkdtemp(prefix="reel_")

        result = create_reel(
            slides=slides,
            media_data=media_data,
            overlay_images=overlay_images,
            output_dir=output_dir,
            voice=voice_id,
            include_intro=inc_intro,
            include_bumper=inc_bumper,
            progress_callback=lambda pct, msg: _progress(0.20 + pct * 0.75, msg),
        )
        st.session_state.rl_result = result

        progress_bar.progress(1.0, text="릴스 영상 생성 완료!")
        status_text.empty()
        st.rerun()

    # ── Step 4: 결과 ──
    result = st.session_state.rl_result
    if not result:
        return

    st.markdown("---")
    st.markdown("###### Step 4. 결과")

    st.video(result["video_bytes"])
    dur = result.get("duration", 0)
    size_mb = len(result["video_bytes"]) / 1024 / 1024
    st.caption(f"길이: {dur:.1f}초 | 크기: {size_mb:.1f} MB | 1080×1920 (9:16)")

    # 미디어 소스 요약
    media_data = st.session_state.get("rl_media", [])
    if media_data:
        source_summary = []
        for i, (_, m_info) in enumerate(media_data):
            if m_info:
                source_summary.append(f"#{i+1}: {m_info['type']}/{m_info.get('source', '?')}")
            else:
                source_summary.append(f"#{i+1}: 브랜드 배경")
        st.caption("배경: " + " · ".join(source_summary))

    title_slug = (script.get("title", "reel") or "reel")[:15].replace(" ", "_")
    col_dl_video, col_dl_json = st.columns(2)
    with col_dl_video:
        st.download_button("🎬 MP4 다운로드", data=result["video_bytes"],
                           file_name=f"reel_{title_slug}_{datetime.now().strftime('%y%m%d_%H%M')}.mp4",
                           mime="video/mp4", use_container_width=True)
    with col_dl_json:
        export = {"script": script, "duration": dur, "created_at": datetime.now().isoformat()}
        st.download_button("📄 스크립트 JSON",
                           data=json.dumps(export, ensure_ascii=False, indent=2),
                           file_name=f"reel_script_{title_slug}.json",
                           mime="application/json", use_container_width=True)

    frames = st.session_state.rl_frames
    if frames:
        with st.expander("슬라이드 프레임 이미지", expanded=False):
            for row_start in range(0, len(frames), 4):
                row = frames[row_start:row_start + 4]
                cols = st.columns(len(row))
                for col_idx, img_bytes in enumerate(row):
                    with cols[col_idx]:
                        slide_idx = row_start + col_idx
                        stype = slides[slide_idx]["type"] if slide_idx < len(slides) else "?"
                        st.image(img_bytes, caption=f"#{slide_idx + 1} {stype}", use_container_width=True)

    desc = script.get("description", "")
    if desc:
        with st.expander("Instagram 캡션"):
            st.text_area("캡션 (복사용)", value=desc, height=120, key="rl_desc_copy")
            tags = script.get("hashtags", [])
            if tags:
                st.caption(" ".join(tags))


def render_insights_page(account):
    """콘텐츠 인사이트 페이지를 렌더링합니다."""
    from datetime import datetime, date, timedelta
    from collections import defaultdict
    import pandas as pd
    import csv, io

    st.caption(f"계정: **{account['name']}**")

    # 공통 카드 템플릿
    _card = (
        '<div style="background:#f8f9fa;border:1px solid #e9ecef;border-radius:10px;padding:20px;margin-bottom:12px">'
        '{content}</div>'
    )
    _card_accent = (
        '<div style="background:{bg};border:1px solid {border};border-radius:10px;padding:20px;margin-bottom:12px">'
        '{content}</div>'
    )

    # 한국어 불용어 (공용)
    _stopwords = {
        # 조사/어미
        "이", "그", "저", "것", "수", "등", "및", "더", "에", "의", "를", "을", "가", "은", "는",
        "으로", "로", "에서", "와", "과", "도", "만", "까지", "부터", "에게", "보다", "한테",
        "처럼", "같이", "위해", "대해", "통해", "따라", "대한", "것이", "것을", "것은",
        # 용언 활용형
        "있는", "없는", "하는", "되는", "있습니다", "됩니다", "합니다", "입니다",
        "하세요", "주세요", "있어요", "해요", "했어요", "드세요", "드려요", "드립니다",
        "하고", "하면", "않은", "않는", "해서", "해도", "해야", "하게", "하지", "해주", "해줘",
        "됩니다", "되어", "되면", "되고", "되지", "했습니다", "했는데", "하였", "되었",
        "보세요", "볼까요", "봐요", "봅니다", "세요", "예요", "이에요", "거예요",
        "있어", "없어", "해봐", "할게", "할까", "한다", "한다면", "한번", "해보",
        # 대명사/지시
        "우리", "나의", "저희", "여러분", "이것", "그것", "이런", "저런", "그런",
        "이번", "다음", "마지막", "처음",
        # 부사/접속
        "오늘", "정말", "함께", "모든", "지금", "바로", "아주", "많은", "좋은", "새로운",
        "가장", "매우", "항상", "때문", "그래서", "그리고", "하지만", "그러나", "또한",
        "역시", "다시", "또", "꼭", "잘", "못", "안", "좀", "참", "너무", "진짜", "완전",
        "특히", "약간", "조금", "살짝", "딱", "쭉", "계속", "먼저", "나중",
        "천천히", "빠르게", "자세히", "쉽게", "간단히", "편하게",
        # 일반 동사/형용사 어근
        "만들", "사용", "확인", "추천", "소개", "공유", "많이", "좋아", "싶은",
        "같은", "다른", "어떤", "모두", "각각", "하나", "여기", "거기", "언제",
        "어떻게", "무엇", "왜", "누구",
    }

    # ── 조회 조건 ──
    date_range = st.date_input(
        "게시일", value=(date.today() - timedelta(days=30), date.today()),
        key="insights_date_range",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        date_from, date_to = date_range
    else:
        date_from = date_range[0] if isinstance(date_range, (list, tuple)) else date_range
        date_to = date_from

    col_btn_fetch, col_btn_csv = st.columns([3, 1])
    with col_btn_fetch:
        fetch_clicked = st.button("조회", use_container_width=True, type="primary")
    with col_btn_csv:
        if st.session_state.get("insights_posts"):
            csv_posts = st.session_state.insights_posts
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["날짜", "유형", "캡션", "좋아요", "댓글", "저장", "공유", "조회수", "도달"])
            for p in csv_posts:
                ins = p.get("insights", {})
                writer.writerow([
                    p.get("timestamp", "")[:10],
                    _fmt_type(p),
                    (p.get("caption") or "")[:100],
                    ins.get("likes", 0), ins.get("comments", 0),
                    ins.get("saved", 0), ins.get("shares", 0),
                    ins.get("views", 0), ins.get("reach", 0),
                ])
            st.download_button("CSV 다운로드", buf.getvalue(), file_name="insights.csv", mime="text/csv", use_container_width=True)
        else:
            st.button("CSV 다운로드", disabled=True, use_container_width=True)

    # ── 데이터 fetch ──
    if fetch_clicked:
        ig = InstagramClient()
        ig.user_id = account["instagram_user_id"].strip()
        ig.access_token = account["access_token"].strip()

        with st.spinner("게시물 조회 중..."):
            media_data = ig.get_media_list(limit=50)
            all_posts = media_data.get("data", [])

        # 팔로워 데이터 수집 (각 호출 독립 처리)
        follower_data = {"_errors": []}
        with st.spinner("팔로워 분석 중..."):
            try:
                follower_data["account"] = ig.get_account_info()
            except Exception as e:
                follower_data["_errors"].append(f"계정 정보: {e}")
            try:
                follower_data["demographics"] = ig.get_follower_demographics()
            except Exception as e:
                follower_data["_errors"].append(f"인구통계: {e}")
            try:
                since_ts = int(datetime.combine(date_from, datetime.min.time()).timestamp())
                until_ts = int(datetime.combine(date_to, datetime.max.time()).timestamp())
                follower_data["daily"] = ig.get_daily_follower_metrics(since=since_ts, until=until_ts)
            except Exception as e:
                follower_data["_errors"].append(f"일별 지표: {e}")
        st.session_state.follower_data = follower_data

        posts = []
        for p in all_posts:
            ts = p.get("timestamp", "")[:10]
            if ts:
                try:
                    d = datetime.strptime(ts, "%Y-%m-%d").date()
                    if date_from <= d <= date_to:
                        posts.append(p)
                except ValueError:
                    posts.append(p)
            else:
                posts.append(p)

        if not posts:
            st.info("해당 기간에 게시물이 없습니다.")
            return

        progress = st.progress(0, text="인사이트 수집 중...")
        insight_errors = []
        for i, post in enumerate(posts):
            try:
                mtype = post.get("media_type", "IMAGE")
                if post.get("media_product_type") == "REELS":
                    mtype = "REEL"
                post["_resolved_type"] = mtype
                post["insights"] = ig.get_media_insights(post["id"], media_type=mtype)
                if "_errors" in post["insights"] and not insight_errors:
                    insight_errors = post["insights"]["_errors"]
            except Exception as e:
                post["insights"] = {}
                if not insight_errors:
                    insight_errors.append(str(e))
            progress.progress((i + 1) / len(posts))
        progress.empty()

        if insight_errors:
            with st.expander("인사이트 조회 중 일부 오류 발생"):
                for err in insight_errors:
                    st.code(err)
                st.caption("instagram_manage_insights 권한이 필요합니다.")

        st.session_state.insights_posts = posts

    if not st.session_state.get("insights_posts"):
        st.info("기간을 설정한 후 조회 버튼을 눌러주세요.")
        return

    posts = st.session_state.insights_posts

    def _safe(key):
        return sum(p.get("insights", {}).get(key, 0) for p in posts
                   if isinstance(p.get("insights", {}).get(key, 0), (int, float)))

    has_insights = any(
        p.get("insights", {}).get("reach") is not None
        for p in posts if "_errors" not in p.get("insights", {})
    )
    na = "–"

    # ── 팔로워 분석 ──
    fd = st.session_state.get("follower_data", {})
    acct = fd.get("account", {})
    demo = fd.get("demographics", {})
    daily_raw = fd.get("daily", {})

    fd_errors = fd.get("_errors", [])
    if fd_errors:
        with st.expander("팔로워 분석 중 일부 오류 발생"):
            for err in fd_errors:
                st.code(err)

    if acct:
        st.markdown("##### 팔로워 분석")

        # 기본 지표
        fc1, fc2, fc3, fc4 = st.columns(4)
        fc1.metric("팔로워", f'{acct.get("followers_count", 0):,}')
        fc2.metric("팔로잉", f'{acct.get("follows_count", 0):,}')
        fc3.metric("게시물", f'{acct.get("media_count", 0):,}')
        followers_count = acct.get("followers_count", 0)
        follows_count = acct.get("follows_count", 0)
        ff_ratio = round(followers_count / max(follows_count, 1), 1)
        fc4.metric("팔로워/팔로잉 비율", f"{ff_ratio}")

        # 일별 팔로워 증감 / 도달 / 프로필 조회 차트
        daily_data = daily_raw.get("data", [])
        if daily_data:
            daily_chart_rows = []
            for metric_item in daily_data:
                m_name = metric_item.get("name", "")
                label_map = {"reach": "도달", "follower_count": "팔로워 증감", "profile_views": "프로필 조회"}
                label = label_map.get(m_name, m_name)
                for val in metric_item.get("values", []):
                    daily_chart_rows.append({
                        "날짜": val.get("end_time", "")[:10],
                        "지표": label,
                        "값": val.get("value", 0),
                    })
            if daily_chart_rows:
                daily_df = pd.DataFrame(daily_chart_rows)
                daily_df["날짜"] = pd.to_datetime(daily_df["날짜"])
                pivot_df = daily_df.pivot_table(index="날짜", columns="지표", values="값", aggfunc="sum").fillna(0)
                st.markdown("---")
                st.markdown("##### 일별 계정 성과")
                daily_metrics_sel = st.multiselect(
                    "지표", list(pivot_df.columns), default=list(pivot_df.columns),
                    key="follower_daily_metrics", label_visibility="collapsed",
                )
                if daily_metrics_sel:
                    st.line_chart(pivot_df[daily_metrics_sel])

        # 인구통계 분석
        has_demo = any(k for k in demo if not k.startswith("_error"))
        if has_demo:
            st.markdown("---")
            st.markdown("##### 팔로워 인구통계")
            demo_tabs = st.tabs(["연령·성별", "도시", "국가"])

            # 연령·성별
            with demo_tabs[0]:
                age_gender = demo.get("age_gender", [])
                if age_gender:
                    ag_rows = []
                    for item in age_gender:
                        dim = item.get("dimension_values", [])
                        if len(dim) >= 2:
                            age = dim[0]
                            gender_raw = dim[1]
                            gender = {"M": "남성", "F": "여성", "U": "기타"}.get(gender_raw, gender_raw)
                            ag_rows.append({"연령대": age, "성별": gender, "수": item.get("value", 0)})
                    if ag_rows:
                        ag_df = pd.DataFrame(ag_rows)
                        total = ag_df["수"].sum()

                        # 성별 비율 요약
                        gender_summary = ag_df.groupby("성별")["수"].sum()
                        gc1, gc2, gc3 = st.columns(3)
                        for col, g in zip([gc1, gc2, gc3], ["여성", "남성", "기타"]):
                            v = gender_summary.get(g, 0)
                            pct = round(v / max(total, 1) * 100, 1)
                            col.metric(g, f"{v:,} ({pct}%)")

                        # 연령대별 바 차트
                        age_pivot = ag_df.pivot_table(index="연령대", columns="성별", values="수", aggfunc="sum").fillna(0)
                        age_order = sorted(age_pivot.index, key=lambda x: int(x.split("-")[0]) if "-" in x else 0)
                        age_pivot = age_pivot.reindex(age_order)
                        st.bar_chart(age_pivot)

                        # 핵심 연령대
                        age_total = ag_df.groupby("연령대")["수"].sum().sort_values(ascending=False)
                        top_ages = age_total.head(3)
                        top_age_text = ", ".join(f"**{a}** ({round(v/max(total,1)*100,1)}%)" for a, v in top_ages.items())
                        st.caption(f"핵심 연령대: {top_age_text}")
                else:
                    st.caption("연령·성별 데이터를 불러올 수 없습니다.")

            # 도시
            with demo_tabs[1]:
                city_data = demo.get("city", [])
                if city_data:
                    city_rows = [{"도시": item.get("dimension_values", [""])[0], "수": item.get("value", 0)} for item in city_data]
                    city_df = pd.DataFrame(city_rows).sort_values("수", ascending=False).head(15)
                    total_city = sum(r["수"] for r in city_rows)
                    city_df["비율"] = city_df["수"].apply(lambda x: f"{round(x / max(total_city, 1) * 100, 1)}%")

                    # TOP 5 도시 카드
                    top5 = city_df.head(5)
                    cols = st.columns(5)
                    for col, (_, row) in zip(cols, top5.iterrows()):
                        col.metric(row["도시"], f'{row["수"]:,}', row["비율"])

                    with st.expander("전체 도시 보기"):
                        st.dataframe(city_df.reset_index(drop=True), use_container_width=True, hide_index=True)
                else:
                    st.caption("도시 데이터를 불러올 수 없습니다.")

            # 국가
            with demo_tabs[2]:
                country_data = demo.get("country", [])
                if country_data:
                    country_rows = [{"국가": item.get("dimension_values", [""])[0], "수": item.get("value", 0)} for item in country_data]
                    country_df = pd.DataFrame(country_rows).sort_values("수", ascending=False).head(15)
                    total_country = sum(r["수"] for r in country_rows)
                    country_df["비율"] = country_df["수"].apply(lambda x: f"{round(x / max(total_country, 1) * 100, 1)}%")

                    top5c = country_df.head(5)
                    cols = st.columns(5)
                    for col, (_, row) in zip(cols, top5c.iterrows()):
                        col.metric(row["국가"], f'{row["수"]:,}', row["비율"])

                    with st.expander("전체 국가 보기"):
                        st.dataframe(country_df.reset_index(drop=True), use_container_width=True, hide_index=True)
                else:
                    st.caption("국가 데이터를 불러올 수 없습니다.")

        # 팔로워 기반 인사이트 요약
        if acct:
            insights_items = []
            if followers_count > 0 and len(posts) > 0:
                avg_reach = _safe("reach") / len(posts) if has_insights else 0
                reach_rate = round(avg_reach / followers_count * 100, 1) if followers_count else 0
                if reach_rate > 0:
                    insights_items.append(f"게시물당 평균 도달률 **{reach_rate}%** (팔로워 대비)")
                    if reach_rate > 100:
                        insights_items.append("도달률이 100%를 초과 → 비팔로워에게 노출이 잘 되는 계정입니다. 릴스·공유 확산 전략을 강화하세요.")
                    elif reach_rate > 30:
                        insights_items.append("도달률이 양호합니다. 현재 콘텐츠 전략을 유지하면서 공유 유도를 강화해보세요.")
                    elif reach_rate > 10:
                        insights_items.append("도달률이 평균적입니다. 릴스 비중을 높이거나 해시태그를 최적화해보세요.")
                    else:
                        insights_items.append("도달률이 낮습니다. 팔로워 참여를 높이는 인터랙티브 콘텐츠(투표, 질문)를 시도해보세요.")

                avg_eng = (_safe("likes") + _safe("comments") + _safe("saved")) / len(posts) if has_insights else 0
                eng_rate = round(avg_eng / followers_count * 100, 2) if followers_count else 0
                if eng_rate > 0:
                    insights_items.append(f"게시물당 평균 참여율 **{eng_rate}%** (좋아요+댓글+저장 / 팔로워)")
                    if eng_rate > 3:
                        insights_items.append("참여율 우수 — 팔로워와의 관계가 매우 좋습니다.")
                    elif eng_rate > 1:
                        insights_items.append("참여율 양호 — 꾸준히 소통형 콘텐츠를 유지하세요.")
                    else:
                        insights_items.append("참여율 개선 필요 — 스토리·질문·투표 등 쌍방향 콘텐츠를 늘려보세요.")

            if ff_ratio > 5:
                insights_items.append(f"팔로워/팔로잉 비율 **{ff_ratio}** — 영향력 있는 계정입니다.")
            elif ff_ratio < 1:
                insights_items.append(f"팔로워/팔로잉 비율 **{ff_ratio}** — 팔로잉 정리 또는 콘텐츠 강화로 자연 유입을 늘려보세요.")

            if insights_items:
                st.markdown("---")
                items_html = "".join(f'<li style="margin-bottom:8px;font-size:13px">{it}</li>' for it in insights_items)
                st.markdown(_card_accent.format(bg="#f8fafc", border="#cbd5e1", content=(
                    f'<p style="font-size:13px;font-weight:600;color:#334155;margin:0 0 8px">팔로워 기반 인사이트</p>'
                    f'<ul style="padding-left:18px;margin:0">{items_html}</ul>'
                )), unsafe_allow_html=True)

    # ── 팔로워 관심사 분석 (게시물 반응 기반) ──
    if has_insights and len(posts) >= 3:
        st.markdown("---")
        st.markdown("##### 팔로워 관심사 분석")
        st.caption("게시물별 참여도를 분석하여 팔로워가 어떤 주제·키워드·해시태그에 반응하는지 추론합니다.")

        # 게시물별 참여도 계산
        def _eng_score(p):
            ins = p.get("insights", {})
            return (ins.get("likes", 0) or 0) + (ins.get("comments", 0) or 0) * 3 + (ins.get("saved", 0) or 0) * 2 + (ins.get("shares", 0) or 0) * 3

        # 키워드별 평균 참여도
        kw_eng = defaultdict(list)
        ht_eng = defaultdict(list)
        for p in posts:
            cap = p.get("caption") or ""
            score = _eng_score(p)
            words = [w for w in re.findall(r"[가-힣]{2,}", cap) if w not in _stopwords and len(w) >= 2]
            for w in set(words):
                kw_eng[w].append(score)
            hashtags = re.findall(r"#([가-힣a-zA-Z0-9_]+)", cap)
            for ht in set(hashtags):
                ht_eng[ht].append(score)

        # 2회 이상 등장한 키워드만 (우연 제거)
        kw_stats = [(w, sum(scores) / len(scores), len(scores))
                    for w, scores in kw_eng.items() if len(scores) >= 2]
        kw_stats.sort(key=lambda x: x[1], reverse=True)
        top_kw = kw_stats[:10]

        ht_stats = [(ht, sum(scores) / len(scores), len(scores))
                    for ht, scores in ht_eng.items() if len(scores) >= 2]
        ht_stats.sort(key=lambda x: x[1], reverse=True)
        top_ht = ht_stats[:8]

        int_tab1, int_tab2, int_tab3 = st.tabs(["관심 키워드", "해시태그 반응", "관심사 요약"])

        with int_tab1:
            if top_kw:
                kw_html_rows = ""
                max_eng = top_kw[0][1] if top_kw else 1
                for rank, (w, avg_eng, cnt) in enumerate(top_kw, 1):
                    bar_pct = round(avg_eng / max(max_eng, 1) * 100)
                    kw_html_rows += (
                        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
                        f'<span style="font-size:12px;color:#6b7280;width:20px;text-align:right">{rank}</span>'
                        f'<span style="font-size:13px;font-weight:600;width:80px">{w}</span>'
                        f'<div style="flex:1;background:#e5e7eb;border-radius:4px;height:20px;overflow:hidden">'
                        f'<div style="width:{bar_pct}%;height:100%;background:linear-gradient(90deg,#818cf8,#6366f1);border-radius:4px"></div></div>'
                        f'<span style="font-size:12px;color:#374151;width:70px;text-align:right">평균 {avg_eng:,.0f}</span>'
                        f'<span style="font-size:11px;color:#9ca3af;width:40px">({cnt}회)</span>'
                        f'</div>'
                    )
                st.markdown(_card.format(content=(
                    f'<p style="font-size:13px;font-weight:600;margin:0 0 12px">팔로워가 가장 반응하는 키워드</p>'
                    f'<p style="font-size:11px;color:#6b7280;margin:0 0 12px">참여도 = 좋아요 + 댓글×3 + 저장×2 + 공유×3</p>'
                    f'{kw_html_rows}'
                )), unsafe_allow_html=True)
            else:
                st.caption("키워드 분석에 충분한 데이터가 없습니다 (동일 키워드 2회 이상 등장 필요).")

        with int_tab2:
            if top_ht:
                ht_tags = ""
                max_ht_eng = top_ht[0][1] if top_ht else 1
                for ht, avg_eng, cnt in top_ht:
                    intensity = min(round(avg_eng / max(max_ht_eng, 1) * 100), 100)
                    r = 99 - int(intensity * 0.6)
                    g = 102 - int(intensity * 0.4)
                    b = 241
                    ht_tags += (
                        f'<span style="display:inline-block;background:rgba({r},{g},{b},{max(0.15, intensity/100)});'
                        f'color:#312e81;border-radius:16px;padding:6px 14px;font-size:13px;font-weight:500;margin:4px 3px">'
                        f'#{ht} <span style="font-size:11px;color:#6366f1">({avg_eng:,.0f} · {cnt}회)</span></span>'
                    )
                st.markdown(_card.format(content=(
                    f'<p style="font-size:13px;font-weight:600;margin:0 0 8px">반응 높은 해시태그</p>'
                    f'<p style="font-size:11px;color:#6b7280;margin:0 0 12px">색이 진할수록 참여도가 높은 해시태그</p>'
                    f'<div>{ht_tags}</div>'
                )), unsafe_allow_html=True)
            else:
                st.caption("해시태그 분석에 충분한 데이터가 없습니다.")

        with int_tab3:
            # 관심사 클러스터 추론
            interest_clusters = {
                "뷰티/스킨케어": ["피부", "케어", "스킨", "보습", "세럼", "크림", "화장", "메이크업", "뷰티", "클렌징", "선크림", "팩"],
                "건강/웰니스": ["건강", "운동", "다이어트", "영양", "비타민", "면역", "수면", "스트레스", "요가", "필라테스", "헬스"],
                "패션/스타일": ["코디", "패션", "스타일", "옷", "착용", "트렌드", "컬러", "데일리", "룩"],
                "음식/맛집": ["맛집", "레시피", "음식", "카페", "디저트", "요리", "브런치", "맛있", "식단"],
                "라이프스타일": ["일상", "루틴", "집", "인테리어", "정리", "생활", "습관", "아침", "저녁"],
                "여행": ["여행", "호텔", "관광", "제주", "바다", "풍경", "숙소", "액티비티"],
                "교육/정보": ["팁", "방법", "가이드", "추천", "비교", "리뷰", "정보", "알려", "소개"],
                "이벤트/프로모션": ["이벤트", "할인", "세일", "쿠폰", "혜택", "무료", "증정", "기간", "선착순"],
            }
            all_captions_text = " ".join(p.get("caption", "") or "" for p in posts)
            cluster_scores = {}
            for cluster, keywords in interest_clusters.items():
                matched = [(kw, avg) for kw, avg, _ in kw_stats if kw in keywords]
                mention_count = sum(1 for kw in keywords if kw in all_captions_text)
                if mention_count >= 1:
                    avg_score = sum(a for _, a in matched) / len(matched) if matched else 0
                    cluster_scores[cluster] = {"mentions": mention_count, "avg_eng": avg_score, "matched_kw": [k for k, _ in matched]}

            if cluster_scores:
                sorted_clusters = sorted(cluster_scores.items(), key=lambda x: (x[1]["mentions"], x[1]["avg_eng"]), reverse=True)

                cluster_html = ""
                for cluster_name, info in sorted_clusters[:5]:
                    mentions = info["mentions"]
                    avg_e = info["avg_eng"]
                    matched = info["matched_kw"]
                    bar_label = f"관련 키워드 {mentions}개"
                    if avg_e > 0:
                        bar_label += f" · 평균 참여 {avg_e:,.0f}"
                    kw_list = ", ".join(matched[:4]) if matched else "–"
                    cluster_html += (
                        f'<div style="background:#f5f3ff;border:1px solid #e0e7ff;border-radius:8px;padding:12px 16px;margin-bottom:8px">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
                        f'<span style="font-size:13px;font-weight:600;color:#3730a3">{cluster_name}</span>'
                        f'<span style="font-size:11px;color:#6366f1">{bar_label}</span>'
                        f'</div>'
                        f'<p style="font-size:11px;color:#6b7280;margin:0">반응 키워드: {kw_list}</p>'
                        f'</div>'
                    )

                # 관심사 요약 인사이트
                top_cluster = sorted_clusters[0][0] if sorted_clusters else ""
                summary_items = []
                summary_items.append(f"팔로워의 주요 관심사는 **{top_cluster}** 영역에 집중되어 있습니다.")
                if len(sorted_clusters) >= 2:
                    second = sorted_clusters[1][0]
                    summary_items.append(f"**{second}** 관련 콘텐츠도 높은 반응을 보이고 있어, 교차 주제 콘텐츠가 효과적일 수 있습니다.")
                if len(sorted_clusters) >= 3:
                    others = ", ".join(c[0] for c in sorted_clusters[2:4])
                    summary_items.append(f"보조 관심사: {others} — 주기적으로 변주를 줘보세요.")

                summary_html = "".join(f'<li style="margin-bottom:6px;font-size:13px">{s}</li>' for s in summary_items)
                st.markdown(_card.format(content=(
                    f'<p style="font-size:13px;font-weight:600;margin:0 0 12px">팔로워 관심사 분포</p>'
                    f'{cluster_html}'
                    f'<div style="margin-top:12px;padding-top:12px;border-top:1px solid #e5e7eb">'
                    f'<p style="font-size:12px;font-weight:600;color:#374151;margin:0 0 6px">인사이트</p>'
                    f'<ul style="padding-left:18px;margin:0">{summary_html}</ul>'
                    f'</div>'
                )), unsafe_allow_html=True)
            else:
                st.caption("캡션 데이터가 부족하여 관심사를 분류할 수 없습니다.")

    st.markdown("---")

    # ── 요약 지표 ──
    st.markdown(f"##### {date_from} ~ {date_to} · {len(posts)}개 게시물")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("좋아요", f"{_safe('likes'):,}" if has_insights else na)
    m2.metric("댓글", f"{_safe('comments'):,}" if has_insights else na)
    m3.metric("저장", f"{_safe('saved'):,}" if has_insights else na)
    m4.metric("공유", f"{_safe('shares'):,}" if has_insights else na)
    m5.metric("조회", f"{_safe('views'):,}" if has_insights else na)
    m6.metric("도달", f"{_safe('reach'):,}" if has_insights else na)

    # ── 일자별 추이 ──
    if has_insights:
        chart_rows = []
        for p in posts:
            ts = p.get("timestamp", "")[:10]
            if not ts:
                continue
            ins = p.get("insights", {})
            chart_rows.append({
                "날짜": ts,
                "좋아요": ins.get("likes", 0) or 0,
                "댓글": ins.get("comments", 0) or 0,
                "저장": ins.get("saved", 0) or 0,
                "공유": ins.get("shares", 0) or 0,
                "조회": ins.get("views", 0) or 0,
                "도달": ins.get("reach", 0) or 0,
            })

        if chart_rows:
            chart_df = pd.DataFrame(chart_rows)
            chart_df["날짜"] = pd.to_datetime(chart_df["날짜"])
            chart_df = chart_df.groupby("날짜").sum().sort_index()

            st.markdown("---")
            st.markdown("##### 일자별 추이")
            chart_metrics = st.multiselect(
                "지표", ["좋아요", "댓글", "저장", "공유", "조회", "도달"],
                default=["좋아요", "조회", "도달"], key="insights_chart_metrics",
                label_visibility="collapsed",
            )
            if chart_metrics:
                st.line_chart(chart_df[chart_metrics])

    # ── 콘텐츠 캘린더 ──
    st.markdown("---")
    st.markdown("##### 콘텐츠 캘린더")

    import calendar as _cal

    # 월 이동
    cal_key = "cal_month_offset"
    if cal_key not in st.session_state:
        st.session_state[cal_key] = 0

    cal_nav1, cal_nav2, cal_nav3 = st.columns([1, 3, 1])
    with cal_nav1:
        if st.button("◀ 이전 달", key="cal_prev", use_container_width=True):
            st.session_state[cal_key] -= 1
            st.rerun()
    with cal_nav3:
        if st.button("다음 달 ▶", key="cal_next", use_container_width=True):
            st.session_state[cal_key] += 1
            st.rerun()

    today = datetime.now()
    cal_month = today.month + st.session_state[cal_key]
    cal_year = today.year
    while cal_month < 1:
        cal_month += 12
        cal_year -= 1
    while cal_month > 12:
        cal_month -= 12
        cal_year += 1

    with cal_nav2:
        st.markdown(
            f"<div style='text-align:center;font-size:16px;font-weight:600;padding:6px'>{cal_year}년 {cal_month}월</div>",
            unsafe_allow_html=True,
        )

    # 게시물 날짜별 매핑
    post_by_date = defaultdict(list)
    for p in posts:
        ts = p.get("timestamp", "")
        if ts:
            d = ts[:10]
            post_by_date[d].append(p)

    # 달력 그리드
    first_weekday, num_days = _cal.monthrange(cal_year, cal_month)
    # 한국식: 월=0
    day_headers = ["월", "화", "수", "목", "금", "토", "일"]
    header_html = "".join(
        f'<th style="padding:6px;font-size:12px;color:#6b7280;text-align:center;font-weight:600">{d}</th>'
        for d in day_headers
    )

    rows_html = ""
    day_num = 1
    # first_weekday: 0=Monday in calendar module
    for week in range(6):
        if day_num > num_days:
            break
        cells = ""
        for dow in range(7):
            if (week == 0 and dow < first_weekday) or day_num > num_days:
                cells += '<td style="padding:4px;border:1px solid #f3f4f6;height:64px"></td>'
            else:
                date_str = f"{cal_year}-{cal_month:02d}-{day_num:02d}"
                day_posts = post_by_date.get(date_str, [])
                is_today = (cal_year == today.year and cal_month == today.month and day_num == today.day)

                if day_posts:
                    n = len(day_posts)
                    total_eng = sum(
                        (dp.get("like_count", 0) or 0) + (dp.get("comments_count", 0) or 0)
                        for dp in day_posts
                    )
                    # 포맷 아이콘
                    icons = []
                    for dp in day_posts:
                        mt = dp.get("media_type", "")
                        if mt == "CAROUSEL_ALBUM":
                            icons.append("📑")
                        elif mt == "VIDEO" or dp.get("media_product_type") == "REELS":
                            icons.append("🎬")
                        else:
                            icons.append("📷")
                    icon_str = " ".join(icons[:3])
                    bg = "#eef2ff"
                    border_c = "#818cf8"
                    cell_content = (
                        f'<div style="font-size:11px;font-weight:600;color:#4338ca">{day_num}</div>'
                        f'<div style="font-size:11px;margin-top:2px">{icon_str}</div>'
                        f'<div style="font-size:10px;color:#6366f1;margin-top:1px">♥{total_eng:,}</div>'
                    )
                else:
                    bg = "#ffffff"
                    border_c = "#f3f4f6"
                    cell_content = f'<div style="font-size:11px;color:#9ca3af">{day_num}</div>'

                if is_today:
                    bg = "#fef3c7"
                    border_c = "#f59e0b"

                cells += (
                    f'<td style="padding:4px;border:1px solid {border_c};height:64px;'
                    f'vertical-align:top;background:{bg};border-radius:4px">{cell_content}</td>'
                )
                day_num += 1
        rows_html += f"<tr>{cells}</tr>"

    cal_html = (
        f'<table style="width:100%;border-collapse:separate;border-spacing:2px;table-layout:fixed">'
        f'<thead><tr>{header_html}</tr></thead>'
        f'<tbody>{rows_html}</tbody></table>'
    )
    st.markdown(cal_html, unsafe_allow_html=True)

    # 게시 빈도 요약
    month_posts = [
        p for p in posts
        if p.get("timestamp", "")[:7] == f"{cal_year}-{cal_month:02d}"
    ]
    month_count = len(month_posts)
    weeks_in_month = (num_days + first_weekday + 6) // 7
    avg_per_week = round(month_count / max(weeks_in_month, 1), 1)

    # 연속 미게시 일수 계산
    max_gap = 0
    if posts:
        post_dates = sorted(set(p.get("timestamp", "")[:10] for p in posts if p.get("timestamp")))
        for i in range(1, len(post_dates)):
            try:
                d1 = datetime.strptime(post_dates[i - 1], "%Y-%m-%d")
                d2 = datetime.strptime(post_dates[i], "%Y-%m-%d")
                gap = (d2 - d1).days - 1
                if gap > max_gap:
                    max_gap = gap
            except ValueError:
                pass

    freq_parts = [f"이번 달 **{month_count}개** 게시 · 주 평균 **{avg_per_week}개**"]
    if max_gap >= 3:
        freq_parts.append(f"  ⚠️ 최대 **{max_gap}일** 연속 미게시 구간이 있습니다")
    st.caption(" | ".join(freq_parts))

    # ── 콘텐츠 분석 ──
    st.markdown("---")
    st.markdown("##### 콘텐츠 분석")

    reels_posts = [p for p in posts if p.get("media_product_type") == "REELS"]
    non_reels = [p for p in posts if p.get("media_product_type") != "REELS"]
    has_reels = has_insights and len(reels_posts) >= 2

    tab_names = ["포맷별", "캡션 길이별", "요일별", "TOP / WORST", "게시 시간"]
    if has_reels:
        tab_names.append("릴스")
    all_tabs = st.tabs(tab_names)
    tab_fmt, tab_cap, tab_day, tab_rank, tab_time = all_tabs[:5]
    tab_reels = all_tabs[5] if has_reels else None

    with tab_fmt:
        format_stats = defaultdict(lambda: {"count": 0, "likes": 0, "comments": 0, "saved": 0, "shares": 0, "views": 0, "reach": 0})
        for p in posts:
            fmt = _fmt_type(p)
            ins = p.get("insights", {})
            format_stats[fmt]["count"] += 1
            for k in ["likes", "comments", "saved", "shares", "views", "reach"]:
                format_stats[fmt][k] += (ins.get(k, 0) or 0)

        if format_stats:
            # 포맷별 metric 카드
            fmt_cols = st.columns(len(format_stats))
            for col, (fmt, s) in zip(fmt_cols, format_stats.items()):
                cnt = s["count"]
                avg_eng = round((s["likes"] + s["comments"] + s["saved"]) / cnt)
                avg_reach = round(s["reach"] / cnt)
                with col:
                    st.markdown(_card.format(content=(
                        f'<p style="font-size:11px;color:#6c757d;margin:0 0 4px">포맷</p>'
                        f'<p style="font-size:18px;font-weight:700;margin:0 0 12px">{fmt}</p>'
                        f'<p style="font-size:12px;color:#495057;margin:0">게시물 {cnt}개</p>'
                        f'<p style="font-size:12px;color:#495057;margin:0">평균 참여 {avg_eng:,}</p>'
                        f'<p style="font-size:12px;color:#495057;margin:0">평균 도달 {avg_reach:,}</p>'
                    )), unsafe_allow_html=True)

            # 상세 테이블
            with st.expander("상세 데이터"):
                fmt_rows = []
                for fmt, s in format_stats.items():
                    cnt = s["count"]
                    fmt_rows.append({
                        "포맷": fmt, "게시물": cnt,
                        "평균 좋아요": round(s["likes"] / cnt),
                        "평균 댓글": round(s["comments"] / cnt),
                        "평균 저장": round(s["saved"] / cnt),
                        "평균 공유": round(s["shares"] / cnt),
                        "평균 조회": round(s["views"] / cnt),
                        "평균 도달": round(s["reach"] / cnt),
                    })
                st.dataframe(pd.DataFrame(fmt_rows).set_index("포맷"), use_container_width=True)

            best_engage = max(format_stats.items(), key=lambda x: (x[1]["likes"] + x[1]["comments"] + x[1]["saved"]) / x[1]["count"])
            best_reach = max(format_stats.items(), key=lambda x: x[1]["reach"] / x[1]["count"])
            st.caption(f"참여 최고: **{best_engage[0]}** · 도달 최고: **{best_reach[0]}**")

    with tab_cap:
        buckets = {"~50자": [], "50~150자": [], "150자~": []}
        for p in posts:
            cap_len = len(p.get("caption") or "")
            ins = p.get("insights", {})
            eng = (ins.get("likes", 0) or 0) + (ins.get("comments", 0) or 0) + (ins.get("saved", 0) or 0)
            if cap_len <= 50:
                buckets["~50자"].append(eng)
            elif cap_len <= 150:
                buckets["50~150자"].append(eng)
            else:
                buckets["150자~"].append(eng)

        # 캡션 길이별 카드
        cap_cols = st.columns(3)
        best_cap_avg = 0
        best_cap_label = ""
        for col, (label, vals) in zip(cap_cols, buckets.items()):
            avg = round(sum(vals) / len(vals)) if vals else 0
            if avg > best_cap_avg:
                best_cap_avg = avg
                best_cap_label = label
            with col:
                st.markdown(_card.format(content=(
                    f'<p style="font-size:11px;color:#6c757d;margin:0 0 4px">캡션 길이</p>'
                    f'<p style="font-size:18px;font-weight:700;margin:0 0 12px">{label}</p>'
                    f'<p style="font-size:12px;color:#495057;margin:0">{len(vals)}개 게시물</p>'
                    f'<p style="font-size:12px;color:#495057;margin:0">평균 참여 {avg:,}</p>'
                )), unsafe_allow_html=True)
        if best_cap_label:
            st.caption(f"**{best_cap_label}** 캡션의 평균 참여가 가장 높습니다.")

    with tab_day:
        day_names = ["월", "화", "수", "목", "금", "토", "일"]
        day_stats = defaultdict(lambda: {"count": 0, "likes": 0, "reach": 0, "engagement": 0})
        for p in posts:
            ts = p.get("timestamp", "")[:10]
            if not ts:
                continue
            try:
                weekday = datetime.strptime(ts, "%Y-%m-%d").weekday()
            except ValueError:
                continue
            ins = p.get("insights", {})
            day = day_names[weekday]
            day_stats[day]["count"] += 1
            day_stats[day]["likes"] += (ins.get("likes", 0) or 0)
            day_stats[day]["reach"] += (ins.get("reach", 0) or 0)
            day_stats[day]["engagement"] += (ins.get("likes", 0) or 0) + (ins.get("comments", 0) or 0) + (ins.get("saved", 0) or 0)

        if day_stats:
            # 요일별 바 차트
            day_chart_data = []
            best_day_name = ""
            best_day_eng = 0
            for day in day_names:
                if day in day_stats:
                    s = day_stats[day]
                    cnt = s["count"]
                    avg_eng = round(s["engagement"] / cnt)
                    day_chart_data.append({"요일": day, "평균 참여": avg_eng, "게시물": cnt})
                    if avg_eng > best_day_eng:
                        best_day_eng = avg_eng
                        best_day_name = day
            if day_chart_data:
                day_df = pd.DataFrame(day_chart_data).set_index("요일")
                st.bar_chart(day_df["평균 참여"])

                with st.expander("상세 데이터"):
                    st.dataframe(day_df, use_container_width=True)

                if best_day_name:
                    st.caption(f"**{best_day_name}요일** 게시물의 평균 참여가 가장 높습니다.")

    with tab_rank:
        ranked = sorted(posts, key=lambda p: (p.get("insights", {}).get("likes", 0) or 0) + (p.get("insights", {}).get("comments", 0) or 0) + (p.get("insights", {}).get("saved", 0) or 0), reverse=True)

        def _rank_card(p, rank, color_bg, color_border):
            ins = p.get("insights", {})
            eng = (ins.get("likes", 0) or 0) + (ins.get("comments", 0) or 0) + (ins.get("saved", 0) or 0)
            cap = (p.get("caption") or "")[:60]
            ts = p.get("timestamp", "")[:10]
            fmt = _fmt_type(p)
            link = p.get("permalink", "")
            link_html = f' · <a href="{link}" target="_blank" style="color:#6c757d;font-size:12px">보기</a>' if link else ""
            thumb = p.get("thumbnail_url") or p.get("media_url") or ""
            img_html = f'<img src="{thumb}" style="width:56px;height:56px;object-fit:cover;border-radius:6px;flex-shrink:0" />' if thumb else '<div style="width:56px;height:56px;background:#e9ecef;border-radius:6px;flex-shrink:0"></div>'
            return _card_accent.format(bg=color_bg, border=color_border, content=(
                f'<div style="display:flex;gap:12px;align-items:start">'
                f'{img_html}'
                f'<div style="flex:1;min-width:0">'
                f'<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px">'
                f'<span style="font-size:18px;font-weight:700;color:{color_border}">{rank}</span>'
                f'<span style="font-size:12px;color:#6c757d">{ts} · {fmt}</span>'
                f'</div>'
                f'<p style="font-size:13px;font-weight:600;margin:0 0 3px">참여 {eng:,}</p>'
                f'<p style="font-size:11px;color:#495057;margin:0">'
                f'좋아요 {ins.get("likes",0) or 0} · 댓글 {ins.get("comments",0) or 0} · 저장 {ins.get("saved",0) or 0}'
                f'{link_html}</p>'
                f'<p style="font-size:11px;color:#868e96;margin:2px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
                f'{cap}{"..." if len(p.get("caption","") or "") > 60 else ""}</p>'
                f'</div></div>'
            ))

        if len(ranked) >= 3:
            col_top, col_worst = st.columns(2)
            with col_top:
                st.markdown('<p style="font-size:13px;font-weight:600;margin-bottom:8px">TOP 3</p>', unsafe_allow_html=True)
                for i, p in enumerate(ranked[:3], 1):
                    st.markdown(_rank_card(p, i, "#f0fdf4", "#22c55e"), unsafe_allow_html=True)
            with col_worst:
                st.markdown('<p style="font-size:13px;font-weight:600;margin-bottom:8px">WORST 3</p>', unsafe_allow_html=True)
                for i, p in enumerate(reversed(ranked[-3:]), 1):
                    st.markdown(_rank_card(p, i, "#fef2f2", "#ef4444"), unsafe_allow_html=True)

        # ── 패턴 분석 & 인사이트 ──
        if len(ranked) >= 6:
            top_n = ranked[:max(3, len(ranked) // 4)]
            worst_n = ranked[-max(3, len(ranked) // 4):]

            def _analyze_group(group):
                fmts = defaultdict(int)
                cap_lens = []
                days = defaultdict(int)
                has_hashtag = 0
                has_cta = 0
                has_question = 0
                avg_reach = []
                day_names_kr = ["월", "화", "수", "목", "금", "토", "일"]
                for p in group:
                    fmts[_fmt_type(p)] += 1
                    cap = p.get("caption") or ""
                    cap_lens.append(len(cap))
                    if "#" in cap:
                        has_hashtag += 1
                    if any(w in cap for w in ["링크", "확인", "클릭", "바로가기", "구매", "신청", "DM", "댓글"]):
                        has_cta += 1
                    if "?" in cap:
                        has_question += 1
                    ts = p.get("timestamp", "")[:10]
                    if ts:
                        try:
                            days[day_names_kr[datetime.strptime(ts, "%Y-%m-%d").weekday()]] += 1
                        except ValueError:
                            pass
                    avg_reach.append(p.get("insights", {}).get("reach", 0) or 0)
                n = len(group)
                top_fmt = max(fmts.items(), key=lambda x: x[1])[0] if fmts else "–"
                top_fmt_pct = round(max(fmts.values()) / n * 100) if fmts else 0
                top_day = max(days.items(), key=lambda x: x[1])[0] if days else "–"
                return {
                    "top_fmt": top_fmt, "top_fmt_pct": top_fmt_pct,
                    "avg_cap": round(sum(cap_lens) / n) if cap_lens else 0,
                    "hashtag_pct": round(has_hashtag / n * 100),
                    "cta_pct": round(has_cta / n * 100),
                    "question_pct": round(has_question / n * 100),
                    "top_day": top_day,
                    "avg_reach": round(sum(avg_reach) / n) if avg_reach else 0,
                }

            top_a = _analyze_group(top_n)
            worst_a = _analyze_group(worst_n)

            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

            # Do's / Don'ts
            dos = []
            if top_a["top_fmt_pct"] >= 50:
                dos.append(f"**{top_a['top_fmt']}** 포맷이 상위의 {top_a['top_fmt_pct']}%를 차지 → 주력으로 활용")
            if top_a["avg_cap"] > worst_a["avg_cap"] + 30:
                dos.append(f"캡션 평균 **{top_a['avg_cap']}자** (하위 {worst_a['avg_cap']}자) → 충분한 맥락 전달")
            elif top_a["avg_cap"] < worst_a["avg_cap"] - 30:
                dos.append(f"캡션 평균 **{top_a['avg_cap']}자** (하위 {worst_a['avg_cap']}자) → 간결한 메시지가 효과적")
            if top_a["hashtag_pct"] > worst_a["hashtag_pct"] + 15:
                dos.append(f"해시태그 사용률 **{top_a['hashtag_pct']}%** → 적극 활용")
            if top_a["cta_pct"] > worst_a["cta_pct"] + 15:
                dos.append(f"CTA 포함률 **{top_a['cta_pct']}%** → 행동 유도 문구 추가")
            if top_a["question_pct"] > worst_a["question_pct"] + 15:
                dos.append(f"질문 포함률 **{top_a['question_pct']}%** → 소통형 캡션 작성")
            if top_a["top_day"]:
                dos.append(f"**{top_a['top_day']}요일** 게시 비중 높음 → 이 요일에 집중")
            if not dos:
                dos.append(f"주요 포맷 **{top_a['top_fmt']}**, 캡션 **{top_a['avg_cap']}자**, **{top_a['top_day']}요일** 게시")

            donts = []
            if worst_a["top_fmt_pct"] >= 50 and worst_a["top_fmt"] != top_a["top_fmt"]:
                donts.append(f"**{worst_a['top_fmt']}** 포맷 비중 {worst_a['top_fmt_pct']}% → 줄이기")
            if worst_a["hashtag_pct"] < top_a["hashtag_pct"] - 15:
                donts.append(f"해시태그 사용률 **{worst_a['hashtag_pct']}%**로 낮음 → 빠뜨리지 말기")
            if worst_a["cta_pct"] < top_a["cta_pct"] - 15:
                donts.append(f"CTA 포함률 **{worst_a['cta_pct']}%** → 단순 게시 피하기")
            if worst_a["avg_cap"] > top_a["avg_cap"] + 50:
                donts.append(f"캡션 평균 **{worst_a['avg_cap']}자**로 과도 → 핵심만")
            elif worst_a["avg_cap"] < 20:
                donts.append(f"캡션 평균 **{worst_a['avg_cap']}자**로 부족 → 최소 설명 추가")
            if worst_a["top_day"] and worst_a["top_day"] != top_a["top_day"]:
                donts.append(f"**{worst_a['top_day']}요일** 게시 성과 낮음 → 피하기")
            if not donts:
                donts.append(f"주요 포맷 **{worst_a['top_fmt']}**, 캡션 **{worst_a['avg_cap']}자**, **{worst_a['top_day']}요일** 게시")

            col_do, col_dont = st.columns(2)
            with col_do:
                do_items = "".join(f'<li style="margin-bottom:6px;font-size:13px">{d}</li>' for d in dos)
                st.markdown(_card_accent.format(bg="#f0fdf4", border="#bbf7d0", content=(
                    f'<p style="font-size:14px;font-weight:700;color:#16a34a;margin:0 0 10px">Do\'s</p>'
                    f'<ul style="padding-left:18px;margin:0">{do_items}</ul>'
                )), unsafe_allow_html=True)
            with col_dont:
                dont_items = "".join(f'<li style="margin-bottom:6px;font-size:13px">{d}</li>' for d in donts)
                st.markdown(_card_accent.format(bg="#fef2f2", border="#fecaca", content=(
                    f'<p style="font-size:14px;font-weight:700;color:#dc2626;margin:0 0 10px">Don\'ts</p>'
                    f'<ul style="padding-left:18px;margin:0">{dont_items}</ul>'
                )), unsafe_allow_html=True)

            # ── 콘텐츠 방향성 (확장) ──
            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
            st.markdown('<p style="font-size:14px;font-weight:700;margin-bottom:12px">콘텐츠 방향성</p>', unsafe_allow_html=True)

            # 1. 캡션 키워드/테마 분석
            top_captions = [p.get("caption", "") or "" for p in top_n]
            all_top_text = " ".join(top_captions)
            keywords = [w for w in re.findall(r"[가-힣]{2,}", all_top_text) if w not in _stopwords]
            keyword_counts = Counter(keywords).most_common(8)
            top_hashtags = Counter(re.findall(r"#([가-힣a-zA-Z0-9_]+)", all_top_text)).most_common(5)

            # 캡션 스타일 분석
            n_top = len(top_n)
            style_storytelling = sum(1 for c in top_captions if any(w in c for w in ["했어요", "했습니다", "이었", "되었", "경험", "후기", "느낌"]))
            style_list = sum(1 for c in top_captions if any(c.count(ch) >= 3 for ch in ["✅", "✔", "·", "-", "①", "1.", "2."]))
            style_emoji_heavy = sum(1 for c in top_captions if len(re.findall(r"[\U0001F300-\U0001FAFF]", c)) >= 5)

            # 참여 유형 분석
            top_saves = sum(p.get("insights", {}).get("saved", 0) or 0 for p in top_n) / max(n_top, 1)
            top_shares = sum(p.get("insights", {}).get("shares", 0) or 0 for p in top_n) / max(n_top, 1)
            top_comments = sum(p.get("insights", {}).get("comments", 0) or 0 for p in top_n) / max(n_top, 1)
            top_likes = sum(p.get("insights", {}).get("likes", 0) or 0 for p in top_n) / max(n_top, 1)

            # 1) 성과 키워드 태그
            if keyword_counts:
                tags_html = " ".join(
                    f'<span style="display:inline-block;background:#e0e7ff;color:#3730a3;'
                    f'border-radius:12px;padding:4px 12px;font-size:12px;font-weight:500;margin:3px 2px">'
                    f'{w} ({c})</span>' for w, c in keyword_counts
                )
                if top_hashtags:
                    tags_html += '<span style="display:inline-block;width:8px"></span>'
                    tags_html += " ".join(
                        f'<span style="display:inline-block;background:#dbeafe;color:#1d4ed8;'
                        f'border-radius:12px;padding:4px 12px;font-size:12px;font-weight:500;margin:3px 2px">'
                        f'#{t} ({c})</span>' for t, c in top_hashtags
                    )
                st.markdown(_card_accent.format(bg="#f5f3ff", border="#c4b5fd", content=(
                    f'<p style="font-size:13px;font-weight:600;color:#5b21b6;margin:0 0 8px">성과 키워드</p>'
                    f'<p style="font-size:12px;color:#6b7280;margin:0 0 8px">상위 콘텐츠 캡션에서 자주 등장하는 키워드와 해시태그</p>'
                    f'<div>{tags_html}</div>'
                )), unsafe_allow_html=True)

            # 2) 포맷 & 구조 전략
            fmt_strategy = []
            if top_a["top_fmt"] == "릴스":
                fmt_strategy.append("**릴스 중심 전략**: 15-30초 숏폼 영상이 핵심 포맷입니다.")
                fmt_strategy.append("릴스 아이디어: 제품 사용법 타임랩스, Before/After 변화 과정, 트렌드 음원 활용 일상 브이로그, 빠른 팁 3가지")
            elif top_a["top_fmt"] == "캐러셀":
                fmt_strategy.append("**캐러셀 중심 전략**: 슬라이드형 정보 전달이 가장 효과적입니다.")
                fmt_strategy.append("캐러셀 아이디어: 단계별 가이드 (5-7장), 비교표/체크리스트, 미니 카드뉴스, 스토리텔링형 후기")
            elif top_a["top_fmt"] == "이미지":
                fmt_strategy.append("**이미지 중심 전략**: 한 장의 임팩트가 중요합니다.")
                fmt_strategy.append("이미지 아이디어: 감성 무드보드, 인용구/타이포 카드, 제품 플랫레이, 고퀄리티 디테일 샷")

            if style_storytelling > n_top * 0.3:
                fmt_strategy.append("스토리텔링형 캡션의 참여도가 높습니다. 경험담·후기·에피소드 형식을 유지하세요.")
            if style_list > n_top * 0.3:
                fmt_strategy.append("리스트형 캡션이 잘 먹힙니다. 정보를 넘버링하거나 체크 포인트로 정리하세요.")

            if fmt_strategy:
                fmt_items = "".join(f'<li style="margin-bottom:6px;font-size:13px">{s}</li>' for s in fmt_strategy)
                st.markdown(_card_accent.format(bg="#eff6ff", border="#bfdbfe", content=(
                    f'<p style="font-size:13px;font-weight:600;color:#1d4ed8;margin:0 0 8px">포맷 & 구조 전략</p>'
                    f'<ul style="padding-left:18px;margin:0">{fmt_items}</ul>'
                )), unsafe_allow_html=True)

            # 3) 추천 콘텐츠 주제
            ideas = []

            # 키워드 기반 주제 제안
            top_words = [w for w, _ in keyword_counts[:5]]
            if len(top_words) >= 2:
                ideas.append(f'키워드 **{"·".join(top_words[:3])}**이(가) 반복 등장 → 이 주제를 시리즈로 발전시켜 보세요 (예: "알아두면 좋은 {top_words[0]} 팁 시리즈")')

            # 참여 유형 기반 제안
            if top_saves > top_likes * 0.3:
                ideas.append("**저장률이 높은 계정**입니다 → 정보성/교육형 콘텐츠 (체크리스트, 가이드, 꿀팁 모음)를 꾸준히 제작하세요")
            if top_shares > top_likes * 0.15:
                ideas.append("**공유가 많은 계정**입니다 → 공감형/밈형 콘텐츠, 친구 태그 유도 게시물을 늘려보세요")
            if top_comments > top_likes * 0.1:
                ideas.append("**댓글 참여가 활발**합니다 → 투표/선택형 질문, 의견 요청 게시물로 소통을 강화하세요")

            # 포맷별 구체적 아이디어
            if top_a["top_fmt"] == "릴스":
                ideas.append("릴스 주제 제안: ① 하루 루틴 브이로그 ② 제품 리뷰 30초 요약 ③ 나만의 꿀팁 TOP 5 ④ 고객 후기 인터뷰")
            elif top_a["top_fmt"] == "캐러셀":
                ideas.append("캐러셀 주제 제안: ① 초보자를 위한 A to Z 가이드 ② 이번 달 추천 리스트 ③ FAQ 정리 ④ 전후 비교 사례")
            else:
                ideas.append("이미지 주제 제안: ① 비하인드 씬 공개 ② 고객 후기 카드 ③ 시즌 무드 비주얼 ④ 숫자/통계 인포그래픽")

            if top_a["question_pct"] > 30:
                ideas.append("질문형 게시물 아이디어: \"여러분은 어떤 쪽인가요?\", \"이 중 하나만 고른다면?\", \"경험 있으신 분?\" 등 열린 질문 활용")
            if top_a["cta_pct"] > 40:
                ideas.append("CTA 활용 아이디어: \"저장해두고 나중에 꺼내보세요\", \"필요한 친구 태그하기\", \"링크는 프로필에서 확인\"")

            ideas_items = "".join(f'<li style="margin-bottom:8px;font-size:13px">{d}</li>' for d in ideas)
            st.markdown(_card_accent.format(bg="#f0fdf4", border="#86efac", content=(
                f'<p style="font-size:13px;font-weight:600;color:#15803d;margin:0 0 8px">추천 콘텐츠 주제</p>'
                f'<ul style="padding-left:18px;margin:0">{ideas_items}</ul>'
            )), unsafe_allow_html=True)

            # 4) 트렌드 & 시즌 제안
            now = datetime.now()
            month = now.month
            season_tips = {
                1: ("새해/신년", "신년 목표 공유, 올해의 키워드, 작년 회고 콘텐츠, 겨울 감성 비주얼"),
                2: ("발렌타인/봄 준비", "발렌타인 기획전, 봄맞이 준비 콘텐츠, 셀프케어 루틴, 겨울→봄 전환 무드"),
                3: ("봄/새학기", "봄 시즌 제품 추천, 새학기·새출발 콘텐츠, 벚꽃 시즌 비주얼, 스프링 루틴"),
                4: ("봄 본격", "야외 활동 콘텐츠, 봄 코디·뷰티 추천, 나들이 가이드, 지구의 날 캠페인"),
                5: ("가정의 달", "어버이날·어린이날 기획, 가족 관련 콘텐츠, 초여름 준비, 감사 캠페인"),
                6: ("여름 시작", "여름 준비 체크리스트, 자외선 관리, 여행 준비 가이드, 상반기 결산"),
                7: ("한여름", "휴가 콘텐츠, 여름 아이템 추천, 시원한 비주얼, 워케이션 브이로그"),
                8: ("여름 마무리", "여름 돌아보기, 가을 신상 티저, 방학 콘텐츠, 휴가 후기"),
                9: ("가을 시작", "가을 무드 전환, 추석 기획, 가을 아이템 추천, 새학기 콘텐츠"),
                10: ("가을 본격", "할로윈 기획, 단풍 비주얼, 가을 추천 리스트, 연말 준비 시작"),
                11: ("연말 준비", "블프·연말 세일 기획, 크리스마스 준비, 올해의 베스트, 선물 가이드"),
                12: ("연말/크리스마스", "크리스마스 콘텐츠, 연말 결산, 올해의 하이라이트, 새해 예고"),
            }
            season_name, season_idea = season_tips.get(month, ("시즌", "계절에 맞는 콘텐츠를 기획하세요"))

            trends = []
            trends.append(f"**{month}월 시즌 ({season_name})**: {season_idea}")
            trends.append("**숏폼 우선 알고리즘**: 인스타그램이 릴스와 숏폼 콘텐츠의 도달을 우선 배분하고 있습니다. 기존 이미지/캐러셀 콘텐츠도 릴스 버전으로 재가공해 보세요.")
            trends.append("**저장·공유 가중치 상승**: 좋아요보다 저장·공유가 알고리즘 가중치가 높아지고 있습니다. \"저장해두세요\" 같은 유틸리티 콘텐츠가 유리합니다.")
            trends.append("**SEO형 캡션**: 인스타그램 검색 기능 강화로, 캡션에 검색 키워드를 자연스럽게 포함하는 것이 노출에 도움됩니다.")
            trends.append("**협업·UGC 활용**: 사용자 제작 콘텐츠(UGC) 리그램, 팔로워 참여형 챌린지가 신뢰도와 도달을 동시에 높여줍니다.")

            trends_items = "".join(f'<li style="margin-bottom:8px;font-size:13px">{t}</li>' for t in trends)
            st.markdown(_card_accent.format(bg="#fffbeb", border="#fde68a", content=(
                f'<p style="font-size:13px;font-weight:600;color:#92400e;margin:0 0 8px">트렌드 & 시즌 제안</p>'
                f'<ul style="padding-left:18px;margin:0">{trends_items}</ul>'
            )), unsafe_allow_html=True)

            # 5) 최적 게시 공식
            formula_parts = []
            formula_parts.append(f"포맷: **{top_a['top_fmt']}**")
            formula_parts.append(f"캡션: **{top_a['avg_cap']}자 내외**")
            formula_parts.append(f"게시일: **{top_a['top_day']}요일**")
            if top_a["hashtag_pct"] > 50:
                formula_parts.append("해시태그: **필수 포함**")
            if top_a["cta_pct"] > 30:
                formula_parts.append("CTA: **행동 유도 문구 포함**")
            if top_a["question_pct"] > 30:
                formula_parts.append("소통: **질문형 캡션 활용**")
            formula_html = " · ".join(formula_parts)

            st.markdown(_card_accent.format(bg="#f8fafc", border="#94a3b8", content=(
                f'<p style="font-size:13px;font-weight:600;color:#334155;margin:0 0 8px">최적 게시 공식</p>'
                f'<p style="font-size:13px;margin:0">{formula_html}</p>'
                f'<p style="font-size:12px;color:#64748b;margin:6px 0 0">이 공식을 기본으로 하되, 주 1회 실험적 콘텐츠를 섞어 새로운 성과 패턴을 발굴하세요.</p>'
            )), unsafe_allow_html=True)

    # ── 게시 시간 탭 ──
    with tab_time:
        if has_insights:
            # 시간별 참여도 계산
            hour_stats = defaultdict(lambda: {"count": 0, "eng": 0})
            dow_hour_stats = defaultdict(lambda: defaultdict(lambda: {"count": 0, "eng": 0}))
            day_names_kr = ["월", "화", "수", "목", "금", "토", "일"]

            for p in posts:
                ts = p.get("timestamp", "")
                if len(ts) < 13:
                    continue
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    # UTC → KST (+9)
                    kst_dt = dt + timedelta(hours=9)
                    h = kst_dt.hour
                    dow = kst_dt.weekday()  # 0=Mon
                except (ValueError, AttributeError):
                    continue

                eng = (p.get("like_count", 0) or 0) + (p.get("comments_count", 0) or 0) * 3
                ins = p.get("insights", {})
                eng += (ins.get("saved", 0) or 0) * 2 + (ins.get("shares", 0) or 0) * 3

                hour_stats[h]["count"] += 1
                hour_stats[h]["eng"] += eng
                dow_hour_stats[dow][h]["count"] += 1
                dow_hour_stats[dow][h]["eng"] += eng

            if hour_stats:
                # 요일×시간 히트맵
                st.markdown("**요일 × 시간대 참여도 히트맵**")
                st.caption("색이 진할수록 평균 참여도가 높은 시간대입니다 (KST)")

                # 최대값 계산
                max_eng_avg = 1
                heatmap_data = {}
                for dow in range(7):
                    for h in range(24):
                        s = dow_hour_stats[dow][h]
                        if s["count"] > 0:
                            avg = s["eng"] / s["count"]
                            heatmap_data[(dow, h)] = avg
                            if avg > max_eng_avg:
                                max_eng_avg = avg

                # 히트맵 HTML
                h_headers = "".join(
                    f'<th style="padding:2px 4px;font-size:10px;color:#9ca3af;text-align:center;min-width:28px">{h}</th>'
                    for h in range(24)
                )
                heatmap_rows = ""
                for dow in range(7):
                    cells = ""
                    for h in range(24):
                        avg = heatmap_data.get((dow, h), 0)
                        intensity = avg / max_eng_avg if max_eng_avg > 0 else 0
                        # 보라색 그라데이션
                        alpha = round(intensity * 0.85 + 0.05, 2) if avg > 0 else 0.02
                        count = dow_hour_stats[dow][h]["count"]
                        title = f"{day_names_kr[dow]} {h}시: 평균 {int(avg)} (게시 {count}건)" if count > 0 else ""
                        cells += (
                            f'<td style="padding:2px;text-align:center;background:rgba(99,102,241,{alpha});'
                            f'border-radius:3px;font-size:9px;color:{"#fff" if alpha > 0.5 else "#6b7280"}" '
                            f'title="{title}">'
                            f'{"●" if count > 0 else ""}</td>'
                        )
                    heatmap_rows += (
                        f'<tr><td style="padding:2px 6px;font-size:11px;font-weight:600;color:#374151;white-space:nowrap">'
                        f'{day_names_kr[dow]}</td>{cells}</tr>'
                    )

                heatmap_html = (
                    f'<div style="overflow-x:auto">'
                    f'<table style="border-collapse:separate;border-spacing:2px;width:100%">'
                    f'<thead><tr><th></th>{h_headers}</tr></thead>'
                    f'<tbody>{heatmap_rows}</tbody></table></div>'
                )
                st.markdown(heatmap_html, unsafe_allow_html=True)

                # TOP 3 최적 게시 시간
                st.markdown("")
                st.markdown("**TOP 3 최적 게시 시간**")

                slot_list = []
                for (dow, h), avg in heatmap_data.items():
                    cnt = dow_hour_stats[dow][h]["count"]
                    if cnt >= 1:
                        slot_list.append((avg, dow, h, cnt))
                slot_list.sort(reverse=True)

                if slot_list:
                    top_slots = slot_list[:3]
                    # session_state에 저장 (Step 2 추천 시간 힌트용)
                    st.session_state["best_posting_slots"] = [
                        {"day": day_names_kr[dow], "hour": h, "eng_avg": int(avg)}
                        for avg, dow, h, cnt in top_slots
                    ]

                    slot_cols = st.columns(min(len(top_slots), 3))
                    medals = ["🥇", "🥈", "🥉"]
                    for i, (avg, dow, h, cnt) in enumerate(top_slots):
                        with slot_cols[i]:
                            st.markdown(_card_accent.format(
                                bg="#f0f0ff", border="#c7d2fe",
                                content=(
                                    f'<div style="text-align:center">'
                                    f'<span style="font-size:24px">{medals[i]}</span>'
                                    f'<p style="font-size:15px;font-weight:700;margin:6px 0 2px;color:#4338ca">'
                                    f'{day_names_kr[dow]}요일 {h:02d}:00</p>'
                                    f'<p style="font-size:12px;color:#6b7280;margin:0">'
                                    f'평균 참여 {int(avg):,} · {cnt}건</p>'
                                    f'</div>'
                                ),
                            ), unsafe_allow_html=True)

                # 시간대 그룹별 분석
                st.markdown("**시간대 그룹별 분석**")
                time_groups = {
                    "🌅 아침 (6-9시)": range(6, 10),
                    "☀️ 점심 (11-13시)": range(11, 14),
                    "🌤️ 오후 (14-17시)": range(14, 18),
                    "🌆 저녁 (18-21시)": range(18, 22),
                    "🌙 밤 (22-1시)": list(range(22, 24)) + [0, 1],
                }
                tg_data = []
                for label, hours in time_groups.items():
                    g_count = sum(hour_stats[h]["count"] for h in hours)
                    g_eng = sum(hour_stats[h]["eng"] for h in hours)
                    g_avg = round(g_eng / g_count) if g_count > 0 else 0
                    tg_data.append({"시간대": label, "게시 수": g_count, "평균 참여": f"{g_avg:,}"})

                tg_cols = st.columns(len(tg_data))
                best_tg = max(tg_data, key=lambda x: int(x["평균 참여"].replace(",", ""))) if tg_data else None
                for i, tg in enumerate(tg_data):
                    is_best = (tg == best_tg)
                    with tg_cols[i]:
                        bg = "#eef2ff" if is_best else "#f8f9fa"
                        bd = "#818cf8" if is_best else "#e9ecef"
                        badge = ' <span style="font-size:10px;background:#4338ca;color:#fff;padding:1px 5px;border-radius:8px">BEST</span>' if is_best else ""
                        st.markdown(_card_accent.format(
                            bg=bg, border=bd,
                            content=(
                                f'<p style="font-size:12px;font-weight:600;margin:0 0 4px">{tg["시간대"]}{badge}</p>'
                                f'<p style="font-size:18px;font-weight:700;color:#374151;margin:0">{tg["평균 참여"]}</p>'
                                f'<p style="font-size:11px;color:#6b7280;margin:2px 0 0">평균 참여 · {tg["게시 수"]}건</p>'
                            ),
                        ), unsafe_allow_html=True)
            else:
                st.caption("게시 시간 데이터가 부족합니다.")
        else:
            st.caption("인사이트 데이터가 없어 게시 시간 분석을 할 수 없습니다.")

    # ── 릴스 탭 ──
    if tab_reels is not None:
        with tab_reels:
            st.caption(f"전체 {len(posts)}개 게시물 중 릴스 {len(reels_posts)}개")

            def _avg_metric(group, key):
                vals = [p.get("insights", {}).get(key, 0) or 0 for p in group]
                return round(sum(vals) / max(len(vals), 1), 1)

            # 릴스 vs 기타 포맷 비교
            if non_reels:
                cmp_cols = st.columns(6)
                cmp_labels = [("조회", "views"), ("도달", "reach"), ("좋아요", "likes"), ("댓글", "comments"), ("저장", "saved"), ("공유", "shares")]
                for col, (label, key) in zip(cmp_cols, cmp_labels):
                    r_val = _avg_metric(reels_posts, key)
                    o_val = _avg_metric(non_reels, key)
                    diff = round(r_val - o_val)
                    diff_str = f"+{diff:,}" if diff > 0 else f"{diff:,}"
                    col.metric(f"릴스 평균 {label}", f"{r_val:,.0f}", diff_str, help=f"기타 포맷 평균: {o_val:,.0f}")

            # 참여율 테이블
            reels_data = []
            for p in reels_posts:
                ins = p.get("insights", {})
                views = ins.get("views", 0) or 0
                likes = ins.get("likes", 0) or 0
                comments = ins.get("comments", 0) or 0
                saved = ins.get("saved", 0) or 0
                shares = ins.get("shares", 0) or 0
                eng = likes + comments + saved + shares
                eng_rate = round(eng / max(views, 1) * 100, 2)
                cap = (p.get("caption") or "")[:40]
                ts = p.get("timestamp", "")[:10]
                reels_data.append({
                    "날짜": ts, "캡션": cap + ("..." if len(p.get("caption", "") or "") > 40 else ""),
                    "조회": views, "참여": eng, "참여율": eng_rate,
                    "좋아요": likes, "댓글": comments, "저장": saved, "공유": shares,
                })
            reels_df = pd.DataFrame(reels_data).sort_values("참여율", ascending=False)
            avg_eng_rate = reels_df["참여율"].mean()

            st.markdown(_card.format(content=(
                f'<div style="display:flex;gap:24px;align-items:center">'
                f'<div><p style="font-size:12px;color:#6b7280;margin:0">릴스 평균 참여율</p>'
                f'<p style="font-size:28px;font-weight:700;color:#6366f1;margin:4px 0 0">{avg_eng_rate:.2f}%</p></div>'
                f'<div style="flex:1;font-size:12px;color:#6b7280">'
                f'참여율 = (좋아요+댓글+저장+공유) / 조회수 × 100</div>'
                f'</div>'
            )), unsafe_allow_html=True)

            st.dataframe(
                reels_df[["날짜", "캡션", "조회", "참여", "참여율"]],
                use_container_width=True, hide_index=True,
                column_config={"참여율": st.column_config.NumberColumn(format="%.2f%%")},
            )

            # 캡션 길이별 참여율
            cap_groups = {"짧은 (~50자)": [], "보통 (50~150자)": [], "긴 (150자~)": []}
            for p in reels_posts:
                cap_len = len(p.get("caption") or "")
                ins = p.get("insights", {})
                views = ins.get("views", 0) or 1
                eng = (ins.get("likes", 0) or 0) + (ins.get("comments", 0) or 0) + (ins.get("saved", 0) or 0) + (ins.get("shares", 0) or 0)
                rate = eng / max(views, 1) * 100
                if cap_len <= 50:
                    cap_groups["짧은 (~50자)"].append(rate)
                elif cap_len <= 150:
                    cap_groups["보통 (50~150자)"].append(rate)
                else:
                    cap_groups["긴 (150자~)"].append(rate)
            cap_html = ""
            for label, rates in cap_groups.items():
                if rates:
                    avg = round(sum(rates) / len(rates), 2)
                    cap_html += (
                        f'<div style="background:#f8f9fa;border-radius:8px;padding:12px 16px;margin-bottom:8px;'
                        f'display:flex;justify-content:space-between;align-items:center">'
                        f'<span style="font-size:13px;font-weight:500">{label}</span>'
                        f'<div style="text-align:right">'
                        f'<span style="font-size:15px;font-weight:700;color:#6366f1">{avg:.2f}%</span>'
                        f'<span style="font-size:11px;color:#9ca3af;margin-left:8px">({len(rates)}개)</span>'
                        f'</div></div>'
                    )
            if cap_html:
                st.markdown(_card.format(content=(
                    f'<p style="font-size:13px;font-weight:600;margin:0 0 10px">캡션 길이별 릴스 참여율</p>'
                    f'{cap_html}'
                )), unsafe_allow_html=True)

            # 해시태그 / CTA / 질문 비교
            def _grp_eng_rate(grp):
                rates = []
                for p in grp:
                    ins = p.get("insights", {})
                    v = ins.get("views", 0) or 1
                    e = (ins.get("likes", 0) or 0) + (ins.get("comments", 0) or 0) + (ins.get("saved", 0) or 0) + (ins.get("shares", 0) or 0)
                    rates.append(e / max(v, 1) * 100)
                return round(sum(rates) / len(rates), 2) if rates else 0

            with_ht = [p for p in reels_posts if "#" in (p.get("caption") or "")]
            without_ht = [p for p in reels_posts if "#" not in (p.get("caption") or "")]
            if with_ht and without_ht:
                ht_c1, ht_c2 = st.columns(2)
                ht_c1.metric("해시태그 O", f"{_grp_eng_rate(with_ht):.2f}%", f"{len(with_ht)}개")
                ht_c2.metric("해시태그 X", f"{_grp_eng_rate(without_ht):.2f}%", f"{len(without_ht)}개")

            pattern_items = []
            with_cta = [p for p in reels_posts if any(w in (p.get("caption") or "") for w in ["링크", "확인", "클릭", "바로가기", "구매", "신청", "DM", "댓글"])]
            with_q = [p for p in reels_posts if "?" in (p.get("caption") or "")]
            if with_cta and len(with_cta) < len(reels_posts):
                others_cta = [p for p in reels_posts if p not in with_cta]
                pattern_items.append(f"CTA 포함 릴스 **{_grp_eng_rate(with_cta):.2f}%** vs 미포함 **{_grp_eng_rate(others_cta):.2f}%** ({len(with_cta)}개 / {len(others_cta)}개)")
            if with_q and len(with_q) < len(reels_posts):
                others_q = [p for p in reels_posts if p not in with_q]
                pattern_items.append(f"질문형 릴스 **{_grp_eng_rate(with_q):.2f}%** vs 일반 **{_grp_eng_rate(others_q):.2f}%** ({len(with_q)}개 / {len(others_q)}개)")
            if pattern_items:
                pi_html = "".join(f'<li style="margin-bottom:6px;font-size:13px">{it}</li>' for it in pattern_items)
                st.markdown(_card.format(content=(
                    f'<p style="font-size:13px;font-weight:600;margin:0 0 8px">캡션 전략별 참여율</p>'
                    f'<ul style="padding-left:18px;margin:0">{pi_html}</ul>'
                )), unsafe_allow_html=True)

            # 릴스 종합 인사이트
            ri = []
            reels_pct = round(len(reels_posts) / max(len(posts), 1) * 100)
            ri.append(f"릴스 비중 **{reels_pct}%** ({len(reels_posts)}/{len(posts)})")
            if reels_pct < 30:
                ri.append("릴스 비중이 낮습니다. 알고리즘이 릴스 도달을 우선 배분하므로 비중을 높여보세요.")
            elif reels_pct > 70:
                ri.append("릴스 중심 계정입니다. 캐러셀이나 이미지로 간간이 변주를 주세요.")
            if non_reels:
                r_reach = _avg_metric(reels_posts, "reach")
                o_reach = _avg_metric(non_reels, "reach")
                if r_reach > o_reach * 1.3:
                    ri.append(f"릴스 도달({r_reach:,.0f})이 기타({o_reach:,.0f})보다 **{round(r_reach/max(o_reach,1)*100-100)}% 높음** → 릴스가 확산에 효과적")
                elif r_reach < o_reach * 0.7:
                    ri.append(f"릴스 도달({r_reach:,.0f})이 기타({o_reach:,.0f})보다 낮음 → 첫 3초 후킹 개선 필요")
            if reels_data:
                best = sorted(reels_data, key=lambda x: x["참여율"], reverse=True)[0]
                ri.append(f"TOP 릴스: **{best['참여율']:.2f}%** ({best['날짜']}) — \"{best['캡션']}\"")
            day_names_kr = ["월", "화", "수", "목", "금", "토", "일"]
            day_views = defaultdict(list)
            for p in reels_posts:
                ts = p.get("timestamp", "")[:10]
                if ts:
                    try:
                        wd = datetime.strptime(ts, "%Y-%m-%d").weekday()
                        day_views[day_names_kr[wd]].append(p.get("insights", {}).get("views", 0) or 0)
                    except ValueError:
                        pass
            if day_views:
                best_day = max(day_views.items(), key=lambda x: sum(x[1]) / len(x[1]))
                ri.append(f"릴스 최고 요일: **{best_day[0]}요일** (평균 {sum(best_day[1])//len(best_day[1]):,}회)")
            ri.append("")
            ri.append("**릴스 최적화 팁**")
            ri.append("첫 1~3초 후킹이 핵심 — 텍스트 오버레이나 임팩트 있는 장면으로 시작")
            ri.append("7~15초 릴스가 완주율이 높아 알고리즘에 유리")
            ri.append("트렌드 오디오를 활용하면 탐색 탭 노출 확률 상승")
            ri.append("마지막에 CTA(저장/공유/팔로우 유도)로 참여율 향상")
            ri_html = ""
            for it in ri:
                if it == "":
                    ri_html += '<div style="height:8px"></div>'
                elif it.startswith("**"):
                    ri_html += f'<p style="font-size:13px;font-weight:700;margin:8px 0 4px">{it.replace("**","")}</p>'
                else:
                    ri_html += f'<li style="margin-bottom:6px;font-size:13px">{it}</li>'
            st.markdown(_card_accent.format(bg="#faf5ff", border="#d8b4fe", content=(
                f'<p style="font-size:13px;font-weight:600;color:#7c3aed;margin:0 0 8px">릴스 종합 인사이트</p>'
                f'<ul style="padding-left:18px;margin:0">{ri_html}</ul>'
            )), unsafe_allow_html=True)

    # ── 게시물 목록 ──
    st.markdown("---")
    st.markdown("##### 게시물 목록")

    sort_options = {
        "최신순": None,
        "좋아요 많은 순": "likes",
        "댓글 많은 순": "comments",
        "저장 많은 순": "saved",
        "공유 많은 순": "shares",
        "조회 많은 순": "views",
        "도달 많은 순": "reach",
    }
    sort_choice = st.selectbox("정렬", list(sort_options.keys()), index=0, key="insights_sort", label_visibility="collapsed")
    sort_key = sort_options[sort_choice]
    if sort_key:
        posts = sorted(posts, key=lambda p: p.get("insights", {}).get(sort_key, 0) or 0, reverse=True)

    for row_start in range(0, len(posts), 3):
        row_posts = posts[row_start:row_start + 3]
        cols = st.columns(3)
        for col, post in zip(cols, row_posts):
            with col:
                is_video = post.get("media_type") == "VIDEO"
                is_reels = post.get("media_product_type") == "REELS"

                if is_video or is_reels:
                    video_url = post.get("media_url")
                    if video_url:
                        st.video(video_url)
                    else:
                        thumb = post.get("thumbnail_url")
                        if thumb:
                            st.image(thumb, use_container_width=True)
                        else:
                            st.caption("영상을 불러올 수 없습니다")
                else:
                    media_url = post.get("media_url") or post.get("thumbnail_url")
                    if media_url:
                        try:
                            st.image(media_url, use_container_width=True)
                        except Exception:
                            st.caption("이미지를 불러올 수 없습니다")
                    else:
                        st.caption("썸네일 없음")

                ts = post.get("timestamp", "")[:10]
                fmt = _fmt_type(post)
                st.caption(f"{ts} · {fmt}")

                ins = {k: v for k, v in post.get("insights", {}).items() if k != "_errors"}
                likes = ins.get("likes", "–")
                comments = ins.get("comments", "–")
                saves = ins.get("saved", "–")
                views = ins.get("views", "–")
                reach = ins.get("reach", "–")

                st.caption(f"좋아요 {likes} · 댓글 {comments} · 저장 {saves}")
                if isinstance(views, int):
                    st.caption(f"조회 {views:,} · 도달 {reach:,}")
                else:
                    st.caption(f"조회 {views} · 도달 {reach}")

                caption = post.get("caption") or ""
                if caption:
                    st.caption(caption[:80] + ("..." if len(caption) > 80 else ""))

                permalink = post.get("permalink", "")
                if permalink:
                    st.caption(f"[Instagram에서 보기]({permalink})")

    # ── 하단 CSV ──
    rows = []
    for post in posts:
        ins = {k: v for k, v in post.get("insights", {}).items() if k != "_errors"}
        rows.append({
            "날짜": post.get("timestamp", "")[:10],
            "타입": _fmt_type(post),
            "좋아요": ins.get("likes", ""),
            "댓글": ins.get("comments", ""),
            "저장": ins.get("saved", ""),
            "공유": ins.get("shares", ""),
            "조회수": ins.get("views", ""),
            "도달": ins.get("reach", ""),
            "캡션": (post.get("caption") or "")[:100],
            "링크": post.get("permalink", ""),
        })

    df = pd.DataFrame(rows)
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSV 다운로드", csv, "instagram_insights.csv", "text/csv")


# ── 페이지 설정 ───────────────────────────────────────────

st.set_page_config(
    page_title="Instagram Publisher",
    page_icon="📸",
    layout="wide",
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ── 사이드바: 계정 & 설정 ─────────────────────────────────

with st.sidebar:
    page = st.radio(
        "메뉴",
        ["게시물 발행", "카드뉴스 생성", "🎬 릴스 생성", "콘텐츠 인사이트"],
        horizontal=True,
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("##### 설정")

    accounts = load_accounts()

    if not accounts:
        st.warning("등록된 계정이 없습니다.")
    else:
        account_names = [a["name"] for a in accounts]
        selected_name = st.selectbox("Instagram 계정", account_names)
        selected_account = next(a for a in accounts if a["name"] == selected_name)

        expiry = selected_account.get("token_expiry", "")
        if expiry:
            try:
                exp_date = datetime.fromisoformat(expiry)
                days_left = (exp_date - datetime.now()).days
                if days_left <= 7:
                    st.error(f"⚠️ 토큰 만료 {days_left}일 남음!")
                elif days_left <= 30:
                    st.warning(f"토큰 만료: {expiry} ({days_left}일 남음)")
                else:
                    st.caption(f"토큰 만료: {expiry} ({days_left}일 남음)")
            except ValueError:
                pass

        # 토큰 갱신 버튼
        if st.button("토큰 갱신 (60일 연장)", use_container_width=True):
            with st.spinner("토큰 갱신 중..."):
                try:
                    result = TokenManager.refresh_long_lived_token(
                        selected_account["access_token"]
                    )
                    # accounts.json 업데이트
                    for a in accounts:
                        if a["name"] == selected_name:
                            a["access_token"] = result["access_token"]
                            a["token_expiry"] = result["token_expiry"]
                            break
                    save_accounts(accounts)
                    st.success(
                        f"토큰 갱신 완료! 새 만료일: {result['token_expiry']}"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"갱신 실패: {e}")

    st.divider()

    figma_file_key = st.text_input(
        "Figma 파일 키",
        value=os.getenv("FIGMA_FILE_KEY", ""),
        help="Figma URL에서 /file/ 뒤의 문자열",
    )

    pencil_gist_id = st.text_input(
        "Pencil Gist ID",
        value=os.getenv("PENCIL_GIST_ID", "8fe8dc21eb2e4c8a9dc2b8c48a559c36"),
        help="cardupload 스크립트가 생성한 GitHub Gist ID",
    )

    # Slack 설정 표시
    slack_url = get_slack_webhook()
    if slack_url:
        st.caption("Slack 알림: 연결됨")
    else:
        st.caption("Slack 알림: 미설정")

    st.divider()

    with st.expander("계정 관리"):
        # ── 토큰 발급 도우미 ──
        st.markdown("**토큰 발급 도우미**")
        st.markdown(
            "**단기 토큰**만 입력하면 장기 토큰 + Instagram User ID를 자동 조회합니다."
        )
        with st.popover("단기 토큰 받는 법"):
            st.markdown(
                "1. [Meta Graph API Explorer](https://developers.facebook.com/tools/explorer/) 접속\n"
                "2. 오른쪽 상단 **Meta App** 선택\n"
                "3. **User Token** 선택\n"
                "4. **Permissions** 추가:\n"
                "   - `pages_show_list`\n"
                "   - `instagram_basic`\n"
                "   - `instagram_content_publish`\n"
                "5. **Generate Access Token** 클릭\n"
                "6. 생성된 토큰 복사 → 아래에 붙여넣기"
            )

        short_token = st.text_input(
            "단기 토큰 붙여넣기",
            type="password",
            key="short_token",
            help="Graph API Explorer에서 발급받은 단기 토큰 (~1시간 유효)",
        )

        if st.button("자동 조회", use_container_width=True, disabled=not short_token):
            with st.spinner("토큰 교환 + 계정 조회 중..."):
                try:
                    # 1) 단기 → 장기 토큰 교환
                    token_result = TokenManager.exchange_for_long_lived(short_token)
                    long_token = token_result["access_token"]
                    expires_in = token_result["expires_in"]
                    new_expiry = (datetime.now() + timedelta(seconds=expires_in)).strftime("%Y-%m-%d")

                    # 2) 연결된 Facebook 페이지 조회
                    pages = TokenManager.get_page_access_token(long_token)

                    if not pages:
                        st.error("연결된 Facebook 페이지가 없습니다.")
                    else:
                        # 3) 각 페이지의 Instagram Business Account 조회
                        found_accounts = []
                        for page in pages:
                            try:
                                ig_id = TokenManager.get_ig_user_id(
                                    page["id"], page["access_token"]
                                )
                                found_accounts.append({
                                    "page_name": page["name"],
                                    "ig_user_id": ig_id,
                                })
                            except Exception:
                                pass

                        if not found_accounts:
                            st.error("Instagram Business 계정이 연결된 페이지가 없습니다.")
                        else:
                            st.session_state["_found_accounts"] = found_accounts
                            st.session_state["_long_token"] = long_token
                            st.session_state["_token_expiry"] = new_expiry
                            st.success(
                                f"✅ {len(found_accounts)}개 Instagram 계정 발견! 아래에서 추가하세요."
                            )
                except Exception as e:
                    st.error(f"조회 실패: {e}")

        # 조회 결과가 있으면 선택 UI 표시
        if st.session_state.get("_found_accounts"):
            found = st.session_state["_found_accounts"]
            long_token = st.session_state["_long_token"]
            token_expiry = st.session_state["_token_expiry"]

            for fa in found:
                col_info, col_add = st.columns([3, 1])
                with col_info:
                    st.text(fa['page_name'])
                    st.caption(f"IG ID: {fa['ig_user_id']}")
                with col_add:
                    already = any(
                        a["instagram_user_id"] == fa["ig_user_id"]
                        for a in accounts
                    )
                    if already:
                        st.caption("등록됨 ✓")
                    elif st.button("추가", key=f"add_{fa['ig_user_id']}"):
                        accounts.append({
                            "name": fa["page_name"],
                            "instagram_user_id": fa["ig_user_id"],
                            "access_token": long_token,
                            "token_expiry": token_expiry,
                        })
                        save_accounts(accounts)
                        st.success(f"'{fa['page_name']}' 추가 완료!")
                        st.rerun()

        st.divider()

        # ── 수동 계정 추가 ──
        with st.popover("수동으로 계정 추가"):
            new_name = st.text_input(
                "계정 이름",
                key="new_name",
                help="표시용 이름 (예: 수壽, 건강지킴이)",
            )
            new_ig_id = st.text_input(
                "Instagram User ID",
                key="new_ig_id",
                help="Instagram Business Account ID (숫자). Graph API Explorer에서 /me/accounts → instagram_business_account.id 로 확인",
            )
            new_token = st.text_input(
                "Access Token",
                key="new_token",
                type="password",
                help="장기 토큰 (60일 유효). 위 도우미로 자동 발급 권장",
            )
            new_expiry = st.text_input(
                "토큰 만료일 (YYYY-MM-DD)",
                key="new_expiry",
                help="장기 토큰 발급일 + 60일",
            )

            if st.button("계정 추가"):
                if new_name and new_ig_id and new_token:
                    accounts.append(
                        {
                            "name": new_name,
                            "instagram_user_id": new_ig_id,
                            "access_token": new_token,
                            "token_expiry": new_expiry,
                        }
                    )
                    save_accounts(accounts)
                    st.success(f"'{new_name}' 계정이 추가되었습니다.")
                    st.rerun()
                else:
                    st.error("이름, User ID, Token은 필수입니다.")

        if accounts:
            st.caption("계정 삭제")
            del_name = st.selectbox(
                "삭제할 계정",
                [a["name"] for a in accounts],
                key="del_account",
            )
            if st.button("삭제", type="secondary"):
                accounts = [a for a in accounts if a["name"] != del_name]
                save_accounts(accounts)
                st.success(f"'{del_name}' 계정이 삭제되었습니다.")
                st.rerun()

# ── 메인 콘텐츠 ──────────────────────────────────────────

if not accounts:
    st.info("사이드바에서 Instagram 계정을 먼저 추가해주세요.")
    st.stop()

# 페이지 라우팅
if page == "콘텐츠 인사이트":
    render_insights_page(selected_account)
    st.stop()
elif page == "카드뉴스 생성":
    render_cardnews_page()
    st.stop()
elif page == "🎬 릴스 생성":
    render_reels_page()
    st.stop()


# ── 메인: Step 1 - 콘텐츠 선택 ─────────────────────────────

st.markdown("##### Step 1. 콘텐츠 선택")

if "frames" not in st.session_state:
    st.session_state.frames = None
    st.session_state.frame_groups = None
    st.session_state.ungrouped = None

if "upload_series" not in st.session_state:
    st.session_state.upload_series = {}
if "url_series" not in st.session_state:
    st.session_state.url_series = {}
if "upload_counter" not in st.session_state:
    st.session_state.upload_counter = 0
if "url_counter" not in st.session_state:
    st.session_state.url_counter = 0
if "pencil_series" not in st.session_state:
    st.session_state.pencil_series = {}
if "pencil_manifest" not in st.session_state:
    st.session_state.pencil_manifest = None
tab_figma, tab_pencil, tab_upload, tab_url = st.tabs(["Figma", "Pencil.dev", "이미지 업로드", "URL 입력"])

figma_selected = {}  # Figma 탭에서 선택된 항목

# ── Tab 1: Figma ──
with tab_figma:
    col_load, col_info = st.columns([1, 3])
    with col_load:
        if st.button("불러오기", use_container_width=True, key="load_figma"):
            with st.spinner("Figma에서 콘텐츠를 가져오는 중..."):
                figma = FigmaClient()
                all_frames = figma.get_file_frames(figma_file_key)
                ig_frames = [
                    f for f in all_frames if "인스타그램" in f.get("page", "")
                ]
                if not ig_frames:
                    ig_frames = all_frames
                st.session_state.frames = ig_frames
                groups, ungrouped = group_frames_by_date(ig_frames)
                st.session_state.frame_groups = groups
                st.session_state.ungrouped = ungrouped

    with col_info:
        if st.session_state.frames:
            st.caption(
                f"총 {len(st.session_state.frames)}개 프레임, "
                f"{len(st.session_state.frame_groups or {})}개 이미지셋"
            )

    if st.session_state.frame_groups:
        groups = st.session_state.frame_groups

        selected_groups = st.multiselect(
            "이미지셋 선택 (여러 개 선택 가능, 최신순)",
            list(groups.keys()),
            format_func=lambda x: f"{x} ({len(groups[x])}장)",
        )

        if selected_groups:
            st.info(f"{len(selected_groups)}개 이미지셋 선택됨")

            for grp in selected_groups:
                group_frames = groups[grp]
                with st.expander(f"{grp} ({len(group_frames)}장)", expanded=True):
                    selected_frames = []
                    cols = st.columns(min(len(group_frames), 5))
                    for i, frame in enumerate(group_frames):
                        with cols[i % 5]:
                            checked = st.checkbox(
                                frame["name"],
                                value=True,
                                key=f"frame_{grp}_{frame['id']}",
                            )
                            if checked:
                                selected_frames.append(frame)
                    st.caption(f"{len(selected_frames)}장 선택" + (" (단일 이미지)" if len(selected_frames) == 1 else ""))
                    if len(selected_frames) >= 1:
                        figma_selected[grp] = [f["id"] for f in selected_frames]

# ── Tab 2: Pencil.dev ──
with tab_pencil:
    col_load, col_info = st.columns([1, 3])
    with col_load:
        if st.button("불러오기", use_container_width=True, key="load_pencil"):
            gist_id = pencil_gist_id.strip().rstrip("/") if pencil_gist_id.strip() else ""
            # "owner/gist_id" 형식이 아니면 마지막 segment만 추출
            if "/" not in gist_id:
                gist_id = gist_id.split("/")[-1]
            if not gist_id:
                st.error("사이드바에서 Pencil Gist ID를 먼저 설정해주세요.")
            else:
                with st.spinner("Pencil.dev에서 콘텐츠를 가져오는 중..."):
                    try:
                        pencil = PencilClient()
                        series_list = pencil.get_series(gist_id)
                        st.session_state.pencil_manifest = series_list
                    except Exception as e:
                        st.error(f"불러오기 실패: {e}")

    with col_info:
        if st.session_state.pencil_manifest:
            st.caption(
                f"총 {len(st.session_state.pencil_manifest)}개 이미지셋"
            )

    if st.session_state.pencil_manifest:
        series_list = st.session_state.pencil_manifest

        selected_pencil = st.multiselect(
            "이미지셋 선택 (여러 개 선택 가능, 최신순)",
            [s["name"] for s in series_list],
            format_func=lambda x: f"{x} ({next(s['count'] for s in series_list if s['name'] == x)}장)",
        )

        # 선택 해제된 이미지셋을 pencil_series에서 제거
        for old_name in list(st.session_state.pencil_series.keys()):
            if old_name not in selected_pencil:
                del st.session_state.pencil_series[old_name]

        if selected_pencil:
            st.info(f"{len(selected_pencil)}개 이미지셋 선택됨")

            for sname in selected_pencil:
                sdata = next(s for s in series_list if s["name"] == sname)
                images = sdata.get("images", [])
                with st.expander(f"{sname} ({len(images)}장)", expanded=True):
                    selected_images = []
                    cols = st.columns(min(len(images), 5))
                    for i, img in enumerate(images):
                        with cols[i % 5]:
                            checked = st.checkbox(
                                img["name"],
                                value=True,
                                key=f"pencil_{sname}_{i}",
                            )
                            try:
                                st.image(img["url"], use_container_width=True)
                            except Exception:
                                st.caption(f"{i+1}. {img['name']}")
                            if checked:
                                selected_images.append(img)
                    st.caption(f"{len(selected_images)}장 선택" + (" (단일 이미지)" if len(selected_images) == 1 else ""))
                    if selected_images:
                        st.session_state.pencil_series[sname] = [img["url"] for img in selected_images]
                    elif sname in st.session_state.pencil_series:
                        del st.session_state.pencil_series[sname]

# ── Tab 3: 이미지 업로드 ──
with tab_upload:
    st.caption("PC에서 이미지 파일을 직접 올려서 Instagram에 발행합니다.")

    upload_name = st.text_input(
        "시리즈 이름",
        placeholder="예: 0224-이벤트",
        key="upload_series_name",
    )

    uploaded_files = st.file_uploader(
        "이미지 파일 선택 (여러 장 가능)",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=f"upload_files_{st.session_state.upload_counter}",
    )

    if uploaded_files:
        preview_cols = st.columns(min(len(uploaded_files), 5))
        for i, uf in enumerate(uploaded_files):
            with preview_cols[i % 5]:
                st.image(uf, caption=uf.name, use_container_width=True)

        if st.button("시리즈 추가", key="add_upload_series"):
            name = upload_name.strip()
            if not name:
                st.error("시리즈 이름을 입력해주세요.")
            elif name in st.session_state.upload_series:
                st.error(f"'{name}' 이름이 이미 존재합니다. 다른 이름을 입력해주세요.")
            else:
                files_data = [{"name": uf.name, "bytes": uf.read()} for uf in uploaded_files]
                st.session_state.upload_series[name] = files_data
                st.session_state.upload_counter += 1
                st.success(f"'{name}' ({len(files_data)}장) 추가됨!")
                st.rerun()

    # 추가된 업로드 시리즈 목록
    if st.session_state.upload_series:
        st.divider()
        st.markdown("###### 추가된 시리즈")
        for sname, sfiles in list(st.session_state.upload_series.items()):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"**{sname}** — {len(sfiles)}장")
                mini_cols = st.columns(min(len(sfiles), 5))
                for i, f in enumerate(sfiles):
                    with mini_cols[i % 5]:
                        st.caption(f["name"])
            with col2:
                if st.button("삭제", key=f"del_upload_{sname}"):
                    del st.session_state.upload_series[sname]
                    st.rerun()

# ── Tab 3: URL 입력 ──
with tab_url:
    st.caption("공개 이미지 URL을 직접 입력하여 Instagram에 발행합니다.")

    url_name = st.text_input(
        "시리즈 이름",
        placeholder="예: 0224-프로모션",
        key="url_series_name",
    )

    url_text = st.text_area(
        "이미지 URL (한 줄에 하나씩)",
        placeholder="https://example.com/image1.png\nhttps://example.com/image2.png",
        height=120,
        key=f"url_input_{st.session_state.url_counter}",
    )

    parsed_urls = [u.strip() for u in url_text.strip().splitlines() if u.strip()] if url_text.strip() else []

    if parsed_urls:
        st.caption(f"{len(parsed_urls)}개 URL 감지됨")
        preview_cols = st.columns(min(len(parsed_urls), 5))
        for i, url in enumerate(parsed_urls):
            with preview_cols[i % 5]:
                try:
                    st.image(url, caption=f"{i+1}장", use_container_width=True)
                except Exception:
                    st.caption(f"{i+1}. {url[:40]}...")

        if st.button("시리즈 추가", key="add_url_series"):
            name = url_name.strip()
            if not name:
                st.error("시리즈 이름을 입력해주세요.")
            elif name in st.session_state.url_series:
                st.error(f"'{name}' 이름이 이미 존재합니다.")
            else:
                st.session_state.url_series[name] = parsed_urls
                st.session_state.url_counter += 1
                st.success(f"'{name}' ({len(parsed_urls)}장) 추가됨!")
                st.rerun()

    # 추가된 URL 시리즈 목록
    if st.session_state.url_series:
        st.divider()
        st.markdown("###### 추가된 시리즈")
        for sname, surls in list(st.session_state.url_series.items()):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"**{sname}** — {len(surls)}장")
            with col2:
                if st.button("삭제", key=f"del_url_{sname}"):
                    del st.session_state.url_series[sname]
                    st.rerun()

# ── 전체 소스 통합 ──
all_selected = {}

# Figma 항목
for grp, node_ids in figma_selected.items():
    all_selected[grp] = {"source": "figma", "node_ids": node_ids, "count": len(node_ids)}

# 업로드 항목
for sname, sfiles in st.session_state.upload_series.items():
    all_selected[sname] = {"source": "upload", "files": sfiles, "count": len(sfiles)}

# Pencil.dev 항목
for sname, surls in st.session_state.pencil_series.items():
    all_selected[sname] = {"source": "url", "urls": surls, "count": len(surls)}

# URL 항목
for sname, surls in st.session_state.url_series.items():
    all_selected[sname] = {"source": "url", "urls": surls, "count": len(surls)}

if all_selected:
    st.session_state.all_selected = all_selected
elif "all_selected" in st.session_state:
    del st.session_state.all_selected

# ── 메인: Step 2 - 시리즈별 발행 설정 ─────────────────────

if st.session_state.get("all_selected"):
    all_selected = st.session_state.all_selected

    st.markdown("---")
    st.markdown("##### Step 2. 발행 설정")

    # 시리즈별 설정 저장
    group_settings = {}  # {grp: {"caption": ..., "mode": ..., "scheduled_time": ...}}

    account_names = [a["name"] for a in accounts]

    for grp, grp_info in all_selected.items():
        with st.expander(f"{grp} — {grp_info['count']}장", expanded=True):
            # 소스별 미리보기
            preview_key = f"preview_{grp}"

            if grp_info["source"] == "figma":
                if st.button("미리보기", key=f"btn_preview_{grp}"):
                    with st.spinner("Figma에서 이미지 가져오는 중..."):
                        figma = FigmaClient()
                        urls = figma.export_images(grp_info["node_ids"], fmt="png", scale=1)
                        ordered = [urls[nid] for nid in grp_info["node_ids"] if urls.get(nid)]
                        st.session_state[preview_key] = ordered

                if st.session_state.get(preview_key):
                    preview_cols = st.columns(min(len(st.session_state[preview_key]), 5))
                    for i, url in enumerate(st.session_state[preview_key]):
                        with preview_cols[i % 5]:
                            st.image(url, caption=f"{i + 1}장", use_container_width=True)

            elif grp_info["source"] == "upload":
                preview_cols = st.columns(min(grp_info["count"], 5))
                for i, f in enumerate(grp_info["files"]):
                    with preview_cols[i % 5]:
                        st.image(f["bytes"], caption=f["name"], use_container_width=True)

            elif grp_info["source"] == "url":
                preview_cols = st.columns(min(grp_info["count"], 5))
                for i, url in enumerate(grp_info["urls"]):
                    with preview_cols[i % 5]:
                        try:
                            st.image(url, caption=f"{i + 1}장", use_container_width=True)
                        except Exception:
                            st.caption(f"{i + 1}. {url[:40]}...")

            grp_account = st.selectbox(
                "계정",
                account_names,
                key=f"account_{grp}",
            )

            # ── AI 캡션 생성 ──
            col_tone, col_ai_btn = st.columns([2, 1])
            with col_tone:
                ai_tone = st.selectbox(
                    "캡션 톤",
                    ["정보성", "감성", "유머", "전문적"],
                    key=f"tone_{grp}",
                )
            with col_ai_btn:
                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                ai_clicked = st.button("✨ AI 캡션 생성", key=f"ai_caption_{grp}", use_container_width=True)

            if ai_clicked:
                with st.spinner("캡션을 생성하고 있습니다..."):
                    try:
                        grp_info = all_selected[grp]

                        # 이미지에서 텍스트 추출 (OCR)
                        image_texts = []

                        # 1) Figma API 텍스트 레이어 추출 시도
                        if grp_info["source"] == "figma" and grp_info.get("node_ids"):
                            try:
                                text_map = figma.extract_texts(grp_info["node_ids"])
                                for nid in grp_info["node_ids"]:
                                    image_texts.extend(text_map.get(nid, []))
                            except Exception:
                                pass

                        # 2) 텍스트 레이어 없으면 OCR로 이미지에서 텍스트 인식
                        if not image_texts:
                            try:
                                import pytesseract
                                from PIL import Image
                                from io import BytesIO

                                img_urls_for_ocr = []
                                if grp_info["source"] == "figma" and st.session_state.get(f"preview_{grp}"):
                                    img_urls_for_ocr = st.session_state[f"preview_{grp}"][:5]
                                elif grp_info["source"] == "url":
                                    img_urls_for_ocr = grp_info["urls"][:5]

                                for img_url in img_urls_for_ocr:
                                    try:
                                        resp = req.get(img_url, timeout=10)
                                        img = Image.open(BytesIO(resp.content))
                                        text = pytesseract.image_to_string(img, lang="kor+eng")
                                        lines = [l.strip() for l in text.split("\n") if l.strip()]
                                        image_texts.extend(lines)
                                    except Exception:
                                        pass
                            except ImportError:
                                pass

                        # 인사이트 데이터에서 키워드/해시태그/캡션 추출
                        top_kw, top_ht, top_caps = [], [], []
                        posts = st.session_state.get("insights_posts", {}).get("data", [])
                        if posts:
                            scored = []
                            for p in posts:
                                eng = (p.get("like_count", 0)
                                       + p.get("comments_count", 0) * 3)
                                scored.append((eng, p))
                            scored.sort(key=lambda x: x[0], reverse=True)
                            top_caps = [
                                p.get("caption", "")
                                for _, p in scored[:5]
                                if p.get("caption")
                            ]
                            kw_counter = Counter()
                            ht_counter = Counter()
                            for _, p in scored[:15]:
                                cap = p.get("caption", "")
                                kw_counter.update(
                                    w for w in re.findall(r"[가-힣]{2,}", cap)
                                    if len(w) >= 2
                                )
                                ht_counter.update(
                                    re.findall(r"#([\w가-힣]+)", cap)
                                )
                            top_kw = [w for w, _ in kw_counter.most_common(10)]
                            top_ht = [t for t, _ in ht_counter.most_common(10)]

                        result = generate_caption(
                            image_texts=image_texts or None,
                            account_name=grp_account,
                            past_top_captions=top_caps or None,
                            top_keywords=top_kw or None,
                            top_hashtags=top_ht or None,
                            tone=ai_tone,
                        )
                        st.session_state[f"caption_{grp}"] = result["full"]
                        st.success("캡션이 생성되었습니다!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"캡션 생성 실패: {e}")

            caption = st.text_area(
                "캡션",
                placeholder="게시물 캡션을 입력하세요 (해시태그 포함 가능)",
                height=120,
                key=f"caption_{grp}",
            )

            mode = st.radio(
                "발행 모드",
                ["즉시 발행", "예약 발행"],
                horizontal=True,
                key=f"mode_{grp}",
            )

            scheduled_time = None
            if mode == "예약 발행":
                col_date, col_time = st.columns(2)
                with col_date:
                    pub_date = st.date_input(
                        "발행 날짜",
                        value=datetime.now() + timedelta(days=1),
                        key=f"date_{grp}",
                    )
                with col_time:
                    pub_time = st.time_input(
                        "발행 시간",
                        value=datetime.now().replace(hour=10, minute=0),
                        key=f"time_{grp}",
                    )
                kst = timezone(timedelta(hours=9))
                scheduled_time = datetime.combine(pub_date, pub_time).replace(tzinfo=kst)
                st.caption(f"예약 시간: {scheduled_time.isoformat()}")

                # 추천 시간 힌트
                best_slots = st.session_state.get("best_posting_slots", [])
                if best_slots:
                    hints = [f'{s["day"]} {s["hour"]:02d}:00' for s in best_slots[:3]]
                    st.info(f"📊 추천 게시 시간: {' / '.join(hints)} (인사이트 기반)")

            group_settings[grp] = {
                "caption": caption,
                "mode": mode,
                "scheduled_time": scheduled_time,
                "account": next(a for a in accounts if a["name"] == grp_account),
            }

    # ── Step 3: 발행 ──────────────────────────────────────

    st.markdown("---")
    st.markdown("##### Step 3. 발행")

    # 요약 테이블
    summary_data = []
    for grp, settings in group_settings.items():
        mode_label = "즉시" if settings["mode"] == "즉시 발행" else f"예약 ({settings['scheduled_time'].strftime('%m/%d %H:%M')})"
        summary_data.append({
            "시리즈": grp,
            "계정": settings["account"]["name"],
            "이미지": f"{all_selected[grp]['count']}장",
            "발행": mode_label,
            "캡션": settings["caption"][:30] + "..." if len(settings["caption"]) > 30 else settings["caption"],
        })
    st.table(summary_data)

    col_confirm, col_publish = st.columns([1, 1])
    with col_confirm:
        confirmed = st.checkbox("발행을 확인합니다")
    with col_publish:
        publish_clicked = st.button(
            f"{len(all_selected)}개 시리즈 발행",
            type="primary",
            disabled=not confirmed,
            use_container_width=True,
        )

    if publish_clicked and confirmed:
        # 캡션 검증
        empty_captions = [g for g, s in group_settings.items() if not s["caption"].strip()]
        if empty_captions:
            st.error(f"캡션을 입력해주세요: {', '.join(empty_captions)}")
        else:
            total = len(all_selected)

            # Slack 시작 알림
            start_summaries = [
                {"name": grp, "count": info["count"], "account": group_settings[grp]["account"]["name"]}
                for grp, info in all_selected.items()
            ]
            slack_err = send_slack_start(start_summaries)
            if slack_err:
                st.caption(f"⚠️ Slack 시작 알림 실패: {slack_err}")

            overall_progress = st.progress(0)
            results = []

            for idx, (grp, group_info) in enumerate(all_selected.items()):
                # 2번째 게시물부터 Instagram rate limit 방지를 위해 대기
                if idx > 0:
                    import time as _time
                    for sec in range(10, 0, -1):
                        st.caption(f"⏳ 다음 게시물까지 {sec}초 대기 (rate limit 방지)...")
                        _time.sleep(1)

                settings = group_settings[grp]
                status = st.status(f"[{idx + 1}/{total}] {grp} 발행 중...", expanded=True)

                result_info = publish_one_group(
                    group_name=grp,
                    group_info=group_info,
                    caption=settings["caption"],
                    scheduled_time=settings["scheduled_time"],
                    account=settings["account"],
                    status_container=status,
                )
                results.append(result_info)

                if result_info["success"]:
                    if result_info.get("media_id"):
                        status.update(label=f"✅ {grp} 발행 완료!", state="complete")
                    else:
                        status.update(label=f"⏰ {grp} 예약 완료!", state="complete")
                else:
                    status.update(label=f"❌ {grp} 실패: {result_info.get('error', '')[:80]}", state="error")

                overall_progress.progress((idx + 1) / total)

            # 결과 요약
            success_count = sum(1 for r in results if r["success"])
            fail_count = total - success_count

            if fail_count == 0:
                st.success(f"🎉 {success_count}개 시리즈 모두 발행 성공!")
                st.balloons()
            else:
                st.warning(f"완료: 성공 {success_count}개 / 실패 {fail_count}개")

            # Slack 완료 알림
            slack_err = send_slack_notification(results)
            if slack_err:
                st.caption(f"⚠️ Slack 완료 알림 실패: {slack_err}")
            elif get_slack_webhook():
                st.caption("🔔 Slack 알림 전송 완료")
