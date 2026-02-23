import base64
import json
import os
import re
from collections import defaultdict
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

from figma_client import FigmaClient
from image_host import ImageHost
from instagram_client import InstagramClient
from pencil_client import PencilClient
from token_manager import TokenManager

ACCOUNTS_FILE = os.path.join(os.path.dirname(__file__), "accounts.json")


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


def render_insights_page(account):
    """콘텐츠 인사이트 페이지를 렌더링합니다."""
    st.header("📊 콘텐츠 인사이트")
    st.caption(f"계정: **{account['name']}** — 사이드바에서 변경 가능")

    col_fetch, col_limit = st.columns([2, 1])
    with col_limit:
        limit = st.selectbox("조회 수", [12, 25, 50], index=0, key="insights_limit")
    with col_fetch:
        fetch_clicked = st.button("📊 최근 게시물 조회", use_container_width=True)

    if fetch_clicked:
        ig = InstagramClient()
        ig.user_id = account["instagram_user_id"].strip()
        ig.access_token = account["access_token"].strip()

        with st.spinner("게시물 목록 조회 중..."):
            media_data = ig.get_media_list(limit=limit)
            posts = media_data.get("data", [])

        if not posts:
            st.info("게시물이 없습니다.")
            return

        progress = st.progress(0, text="인사이트 데이터 수집 중...")
        insight_errors = []
        for i, post in enumerate(posts):
            try:
                mtype = post.get("media_type", "IMAGE")
                # 릴스 판별: media_product_type이 REELS이면 릴스
                if post.get("media_product_type") == "REELS":
                    mtype = "REEL"
                post["_resolved_type"] = mtype
                post["insights"] = ig.get_media_insights(post["id"], media_type=mtype)
                # 첫 번째 에러만 수집 (진단용)
                if "_errors" in post["insights"] and not insight_errors:
                    insight_errors = post["insights"]["_errors"]
            except Exception as e:
                post["insights"] = {}
                if not insight_errors:
                    insight_errors.append(str(e))
            progress.progress((i + 1) / len(posts))
        progress.empty()

        if insight_errors:
            with st.expander("⚠️ 인사이트 조회 중 오류 발생 (클릭하여 상세 보기)"):
                for err in insight_errors:
                    st.code(err)
                st.info("instagram_manage_insights 권한이 필요합니다. "
                        "Meta 개발자 콘솔에서 권한을 확인하세요.")

        st.session_state.insights_posts = posts

    if not st.session_state.get("insights_posts"):
        st.info("'최근 게시물 조회' 버튼을 클릭하세요.")
        return

    posts = st.session_state.insights_posts

    # ── 요약 지표 ──
    def _safe_sum(key):
        return sum(p.get("insights", {}).get(key, 0) for p in posts
                   if isinstance(p.get("insights", {}).get(key, 0), (int, float)))

    total_likes = _safe_sum("likes")
    total_comments = _safe_sum("comments")
    total_saves = _safe_sum("saved")
    total_shares = _safe_sum("shares")
    total_views = _safe_sum("views")
    total_reach = _safe_sum("reach")

    # 인사이트 데이터가 하나라도 있는지 체크
    has_insights = any(
        p.get("insights", {}).get("reach") is not None
        for p in posts if "_errors" not in p.get("insights", {})
    )

    na = "–"  # 인사이트 없을 때 표시
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("❤️ 좋아요", f"{total_likes:,}" if has_insights else na)
    m2.metric("💬 댓글", f"{total_comments:,}" if has_insights else na)
    m3.metric("📌 저장", f"{total_saves:,}" if has_insights else na)
    m4.metric("🔄 공유", f"{total_shares:,}" if has_insights else na)
    m5.metric("👁️ 조회", f"{total_views:,}" if has_insights else na)
    m6.metric("📣 도달", f"{total_reach:,}" if has_insights else na)

    st.divider()

    # ── 게시물 카드 그리드 ──
    type_label = {"IMAGE": "📷 이미지", "VIDEO": "🎬 동영상", "CAROUSEL_ALBUM": "📑 캐러셀"}

    for row_start in range(0, len(posts), 3):
        row_posts = posts[row_start:row_start + 3]
        cols = st.columns(3)
        for col, post in zip(cols, row_posts):
            with col:
                # 릴스/동영상은 thumbnail_url 우선, 이미지는 media_url 우선
                is_video = post.get("media_type") == "VIDEO"
                is_reels = post.get("media_product_type") == "REELS"

                if is_video or is_reels:
                    media_url = post.get("thumbnail_url") or post.get("media_url")
                else:
                    media_url = post.get("media_url") or post.get("thumbnail_url")

                if media_url:
                    try:
                        st.image(media_url, use_container_width=True)
                    except Exception:
                        st.info("🖼️ 이미지 로드 불가")
                else:
                    st.info("🖼️ 썸네일 없음")

                ts = post.get("timestamp", "")[:10]
                if is_reels:
                    mtype = "🎬 릴스"
                else:
                    mtype = type_label.get(post.get("media_type", ""), "기타")
                st.caption(f"{ts} · {mtype}")

                ins = {k: v for k, v in post.get("insights", {}).items()
                       if k != "_errors"}
                likes = ins.get("likes", "–")
                comments = ins.get("comments", "–")
                saves = ins.get("saved", "–")
                shares = ins.get("shares", "–")
                views = ins.get("views", "–")
                reach = ins.get("reach", "–")

                st.markdown(f"❤️ **{likes}**  💬 **{comments}**  📌 **{saves}**  🔄 **{shares}**")
                st.caption(f"👁️ 조회 {views:,}  ·  📣 도달 {reach:,}" if isinstance(views, int) else f"👁️ 조회 {views}  ·  📣 도달 {reach}")

                caption = post.get("caption") or ""
                if caption:
                    st.caption(caption[:80] + ("..." if len(caption) > 80 else ""))

                permalink = post.get("permalink", "")
                if permalink:
                    st.markdown(f"[Instagram에서 보기]({permalink})")

    # ── CSV 다운로드 ──
    st.divider()
    import pandas as pd

    rows = []
    for post in posts:
        ins = {k: v for k, v in post.get("insights", {}).items() if k != "_errors"}
        is_reels = post.get("media_product_type") == "REELS"
        rows.append({
            "날짜": post.get("timestamp", "")[:10],
            "타입": "릴스" if is_reels else {"IMAGE": "이미지", "VIDEO": "동영상", "CAROUSEL_ALBUM": "캐러셀"}.get(post.get("media_type", ""), "기타"),
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
    st.download_button("📥 CSV 다운로드", csv, "instagram_insights.csv", "text/csv")


# ── 페이지 설정 ───────────────────────────────────────────

st.set_page_config(
    page_title="Instagram 게시물 올려줘!",
    page_icon="📸",
    layout="wide",
)

# ── 사이드바: 계정 & 설정 ─────────────────────────────────

with st.sidebar:
    page = st.radio(
        "메뉴",
        ["📸 게시물 발행", "📊 콘텐츠 인사이트"],
        horizontal=True,
        label_visibility="collapsed",
    )
    st.divider()
    st.header("설정")

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
        if st.button("🔄 토큰 갱신 (60일 연장)", use_container_width=True):
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
        st.caption("🔔 Slack 알림: 연결됨")
    else:
        st.caption("🔕 Slack 알림: 미설정")

    st.divider()

    with st.expander("계정 관리"):
        # ── 토큰 발급 도우미 ──
        st.subheader("🔑 토큰 발급 도우미")
        st.markdown(
            "**단기 토큰**만 입력하면 장기 토큰 + Instagram User ID를 자동 조회합니다."
        )
        with st.popover("📖 단기 토큰 받는 법"):
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

        if st.button("🔍 자동 조회", use_container_width=True, disabled=not short_token):
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
                    st.text(f"📄 {fa['page_name']}")
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
        with st.popover("✏️ 수동으로 계정 추가"):
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
if page == "📊 콘텐츠 인사이트":
    st.title("📊 콘텐츠 인사이트")
    render_insights_page(selected_account)
    st.stop()

st.title("📸 Instagram 게시물 올려줘!")

# ── 메인: Step 1 - 콘텐츠 선택 ─────────────────────────────

st.header("Step 1. 콘텐츠 선택")

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
tab_figma, tab_pencil, tab_upload, tab_url = st.tabs(["📐 Figma", "✏️ Pencil.dev", "📷 이미지 업로드", "🔗 URL 입력"])

figma_selected = {}  # Figma 탭에서 선택된 항목

# ── Tab 1: Figma ──
with tab_figma:
    col_load, col_info = st.columns([1, 3])
    with col_load:
        if st.button("🔄 피그마 읽어오기", use_container_width=True):
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
            st.info(f"✅ {len(selected_groups)}개 이미지셋 선택됨")

            for grp in selected_groups:
                group_frames = groups[grp]
                with st.expander(f"📁 {grp} ({len(group_frames)}장)", expanded=True):
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
        if st.button("🔄 Pencil.dev 읽어오기", use_container_width=True):
            gist_id = pencil_gist_id.strip().rstrip("/").split("/")[-1] if pencil_gist_id.strip() else ""
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

        if selected_pencil:
            st.info(f"✅ {len(selected_pencil)}개 이미지셋 선택됨")

            for sname in selected_pencil:
                sdata = next(s for s in series_list if s["name"] == sname)
                images = sdata.get("images", [])
                with st.expander(f"📁 {sname} ({len(images)}장)", expanded=True):
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

        if st.button("➕ 시리즈 추가", key="add_upload_series"):
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
        st.subheader("추가된 시리즈")
        for sname, sfiles in list(st.session_state.upload_series.items()):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"📷 **{sname}** — {len(sfiles)}장")
                mini_cols = st.columns(min(len(sfiles), 5))
                for i, f in enumerate(sfiles):
                    with mini_cols[i % 5]:
                        st.caption(f["name"])
            with col2:
                if st.button("❌ 삭제", key=f"del_upload_{sname}"):
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

        if st.button("➕ 시리즈 추가", key="add_url_series"):
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
        st.subheader("추가된 시리즈")
        for sname, surls in list(st.session_state.url_series.items()):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"🔗 **{sname}** — {len(surls)}장")
            with col2:
                if st.button("❌ 삭제", key=f"del_url_{sname}"):
                    del st.session_state.url_series[sname]
                    st.rerun()

# ── 전체 소스 통합 ──
all_selected = {}

# Figma 항목
for grp, node_ids in figma_selected.items():
    all_selected[grp] = {"source": "figma", "node_ids": node_ids, "count": len(node_ids)}

# 업로드 항목
for sname, sfiles in st.session_state.upload_series.items():
    all_selected[f"📷 {sname}"] = {"source": "upload", "files": sfiles, "count": len(sfiles)}

# Pencil.dev 항목
for sname, surls in st.session_state.pencil_series.items():
    all_selected[f"✏️ {sname}"] = {"source": "url", "urls": surls, "count": len(surls)}

# URL 항목
for sname, surls in st.session_state.url_series.items():
    all_selected[f"🔗 {sname}"] = {"source": "url", "urls": surls, "count": len(surls)}

if all_selected:
    st.session_state.all_selected = all_selected
elif "all_selected" in st.session_state:
    del st.session_state.all_selected

# ── 메인: Step 2 - 시리즈별 발행 설정 ─────────────────────

if st.session_state.get("all_selected"):
    all_selected = st.session_state.all_selected

    st.divider()
    st.header("Step 2. 시리즈별 발행 설정")

    # 시리즈별 설정 저장
    group_settings = {}  # {grp: {"caption": ..., "mode": ..., "scheduled_time": ...}}

    account_names = [a["name"] for a in accounts]

    for grp, grp_info in all_selected.items():
        with st.expander(f"📁 {grp} — {grp_info['count']}장", expanded=True):
            # 소스별 미리보기
            preview_key = f"preview_{grp}"

            if grp_info["source"] == "figma":
                if st.button("👁️ 미리보기", key=f"btn_preview_{grp}"):
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

            caption = st.text_area(
                "캡션",
                placeholder="게시물 캡션을 입력하세요 (해시태그 포함 가능)",
                height=80,
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

            group_settings[grp] = {
                "caption": caption,
                "mode": mode,
                "scheduled_time": scheduled_time,
                "account": next(a for a in accounts if a["name"] == grp_account),
            }

    # ── Step 3: 발행 ──────────────────────────────────────

    st.divider()
    st.header("Step 3. 발행")

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
            f"🚀 {len(all_selected)}개 시리즈 발행하기",
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
