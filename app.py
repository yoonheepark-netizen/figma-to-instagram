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


def _fmt_type(post):
    """게시물 포맷 텍스트를 반환합니다."""
    if post.get("media_product_type") == "REELS":
        return "릴스"
    return {"IMAGE": "이미지", "VIDEO": "동영상", "CAROUSEL_ALBUM": "캐러셀"}.get(post.get("media_type", ""), "기타")


def render_insights_page(account):
    """콘텐츠 인사이트 페이지를 렌더링합니다."""
    from datetime import datetime, date, timedelta
    from collections import defaultdict
    import pandas as pd
    import csv, io

    st.markdown("## 콘텐츠 인사이트")
    st.caption(f"계정: **{account['name']}**")

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

    # ── 콘텐츠 분석 ──
    st.markdown("---")
    st.markdown("##### 콘텐츠 분석")

    tab_fmt, tab_cap, tab_day, tab_rank = st.tabs(["포맷별", "캡션 길이별", "요일별", "TOP / WORST"])

    with tab_fmt:
        format_stats = defaultdict(lambda: {"count": 0, "likes": 0, "comments": 0, "saved": 0, "shares": 0, "views": 0, "reach": 0})
        for p in posts:
            fmt = _fmt_type(p)
            ins = p.get("insights", {})
            format_stats[fmt]["count"] += 1
            for k in ["likes", "comments", "saved", "shares", "views", "reach"]:
                format_stats[fmt][k] += (ins.get(k, 0) or 0)

        if format_stats:
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
            st.info(f"참여 최고: **{best_engage[0]}** · 도달 최고: **{best_reach[0]}**")

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

        cap_rows = [{"길이": k, "게시물": len(v), "평균 참여": round(sum(v) / len(v))} for k, v in buckets.items() if v]
        if cap_rows:
            st.dataframe(pd.DataFrame(cap_rows).set_index("길이"), use_container_width=True)

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

        day_rows = []
        for day in day_names:
            if day in day_stats:
                s = day_stats[day]
                cnt = s["count"]
                day_rows.append({"요일": day, "게시물": cnt, "평균 좋아요": round(s["likes"] / cnt), "평균 도달": round(s["reach"] / cnt), "평균 참여": round(s["engagement"] / cnt)})
        if day_rows:
            st.dataframe(pd.DataFrame(day_rows).set_index("요일"), use_container_width=True)
            best_day = max(day_rows, key=lambda x: x["평균 참여"])
            st.info(f"**{best_day['요일']}요일** 게시물의 평균 참여가 가장 높습니다.")

    with tab_rank:
        ranked = sorted(posts, key=lambda p: (p.get("insights", {}).get("likes", 0) or 0) + (p.get("insights", {}).get("comments", 0) or 0) + (p.get("insights", {}).get("saved", 0) or 0), reverse=True)

        def _rank_row(p):
            ins = p.get("insights", {})
            eng = (ins.get("likes", 0) or 0) + (ins.get("comments", 0) or 0) + (ins.get("saved", 0) or 0)
            cap = (p.get("caption") or "")[:50]
            ts = p.get("timestamp", "")[:10]
            link = p.get("permalink", "")
            fmt = _fmt_type(p)
            text = f"{ts} · {fmt} · 참여 **{eng:,}** (좋아요 {ins.get('likes',0) or 0} / 댓글 {ins.get('comments',0) or 0} / 저장 {ins.get('saved',0) or 0})"
            if cap:
                text += f"  \n{cap}{'...' if len(p.get('caption','') or '') > 50 else ''}"
            if link:
                text += f" [링크]({link})"
            return text

        if len(ranked) >= 3:
            st.markdown("###### TOP 3")
            for i, p in enumerate(ranked[:3], 1):
                st.markdown(f"**{i}.** {_rank_row(p)}")

            st.markdown("###### WORST 3")
            for i, p in enumerate(reversed(ranked[-3:]), 1):
                st.markdown(f"**{i}.** {_rank_row(p)}")

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

            st.markdown("---")
            st.markdown("###### 콘텐츠 인사이트")

            # Do's
            dos = []
            if top_a["top_fmt_pct"] >= 50:
                dos.append(f"**{top_a['top_fmt']}** 포맷이 상위 콘텐츠의 {top_a['top_fmt_pct']}%를 차지합니다. 이 포맷을 주력으로 활용하세요.")
            if top_a["avg_cap"] > worst_a["avg_cap"] + 30:
                dos.append(f"상위 콘텐츠의 평균 캡션 길이는 **{top_a['avg_cap']}자**로, 하위({worst_a['avg_cap']}자)보다 깁니다. 충분한 맥락을 담아주세요.")
            elif top_a["avg_cap"] < worst_a["avg_cap"] - 30:
                dos.append(f"상위 콘텐츠의 평균 캡션 길이는 **{top_a['avg_cap']}자**로, 하위({worst_a['avg_cap']}자)보다 짧습니다. 간결한 메시지가 효과적입니다.")
            if top_a["hashtag_pct"] > worst_a["hashtag_pct"] + 15:
                dos.append(f"상위 콘텐츠의 **{top_a['hashtag_pct']}%**가 해시태그를 사용합니다. 관련 해시태그를 적극 활용하세요.")
            if top_a["cta_pct"] > worst_a["cta_pct"] + 15:
                dos.append(f"상위 콘텐츠의 **{top_a['cta_pct']}%**에 행동 유도 문구(CTA)가 포함되어 있습니다. 댓글·DM·링크 등 CTA를 넣어보세요.")
            if top_a["question_pct"] > worst_a["question_pct"] + 15:
                dos.append(f"상위 콘텐츠의 **{top_a['question_pct']}%**에 질문이 포함되어 있습니다. 팔로워와의 소통을 유도하세요.")
            if top_a["top_day"]:
                dos.append(f"상위 콘텐츠는 **{top_a['top_day']}요일**에 집중되어 있습니다. 이 요일에 게시하는 것을 권장합니다.")

            if not dos:
                dos.append(f"상위 콘텐츠의 주요 포맷은 **{top_a['top_fmt']}**, 평균 캡션 **{top_a['avg_cap']}자**, 주요 게시 요일 **{top_a['top_day']}요일**입니다.")

            # Don'ts
            donts = []
            if worst_a["top_fmt_pct"] >= 50 and worst_a["top_fmt"] != top_a["top_fmt"]:
                donts.append(f"하위 콘텐츠의 {worst_a['top_fmt_pct']}%가 **{worst_a['top_fmt']}** 포맷입니다. 이 포맷의 비중을 줄여보세요.")
            if worst_a["hashtag_pct"] < top_a["hashtag_pct"] - 15:
                donts.append(f"하위 콘텐츠의 해시태그 사용률이 **{worst_a['hashtag_pct']}%**로 낮습니다. 해시태그 없이 올리지 마세요.")
            if worst_a["cta_pct"] < top_a["cta_pct"] - 15:
                donts.append(f"하위 콘텐츠의 CTA 포함률이 **{worst_a['cta_pct']}%**로 낮습니다. 행동 유도 없는 단순 게시는 참여를 떨어뜨립니다.")
            if worst_a["avg_cap"] > top_a["avg_cap"] + 50:
                donts.append(f"하위 콘텐츠의 캡션이 평균 **{worst_a['avg_cap']}자**로 너무 깁니다. 핵심만 전달하세요.")
            elif worst_a["avg_cap"] < 20:
                donts.append(f"하위 콘텐츠의 캡션이 평균 **{worst_a['avg_cap']}자**로 너무 짧습니다. 최소한의 설명을 덧붙이세요.")
            if worst_a["top_day"] and worst_a["top_day"] != top_a["top_day"]:
                donts.append(f"하위 콘텐츠는 **{worst_a['top_day']}요일**에 집중되어 있습니다. 이 요일은 피하는 것이 좋습니다.")

            if not donts:
                donts.append(f"하위 콘텐츠의 주요 포맷은 **{worst_a['top_fmt']}**, 평균 캡션 **{worst_a['avg_cap']}자**, 주요 게시 요일 **{worst_a['top_day']}요일**입니다.")

            col_do, col_dont = st.columns(2)
            with col_do:
                st.markdown("**Do's**")
                for d in dos:
                    st.markdown(f"- {d}")
            with col_dont:
                st.markdown("**Don'ts**")
                for d in donts:
                    st.markdown(f"- {d}")

            # 방향성 제안
            st.markdown("###### 콘텐츠 방향성 제안")
            directions = []
            if top_a["top_fmt"] == "릴스":
                directions.append("릴스가 높은 참여를 이끌어내고 있습니다. 숏폼 영상 비중을 늘리고, 트렌드 음원이나 빠른 편집을 활용해 보세요.")
            elif top_a["top_fmt"] == "캐러셀":
                directions.append("캐러셀이 가장 효과적입니다. 정보를 슬라이드로 나눠 전달하고, 마지막 장에 CTA를 배치하세요.")
            elif top_a["top_fmt"] == "이미지":
                directions.append("단일 이미지가 잘 먹히는 계정입니다. 비주얼 퀄리티에 집중하고, 한 장으로 시선을 끄는 썸네일을 만들어 보세요.")

            if top_a["avg_reach"] > 0:
                reach_ratio = top_a["avg_reach"] / max(worst_a["avg_reach"], 1)
                if reach_ratio >= 2:
                    directions.append(f"상위 콘텐츠의 평균 도달({top_a['avg_reach']:,})이 하위({worst_a['avg_reach']:,})의 {reach_ratio:.1f}배입니다. 알고리즘 확산이 잘 되는 콘텐츠의 패턴을 반복하세요.")

            if top_a["question_pct"] > 30:
                directions.append("질문형 캡션이 참여에 효과적입니다. '여러분은 어떤가요?', '어떻게 생각하세요?' 같은 열린 질문을 꾸준히 넣어주세요.")

            if top_a["cta_pct"] > 40:
                directions.append("CTA가 포함된 게시물의 성과가 좋습니다. 저장·공유 유도, 댓글 참여 요청 등 구체적인 행동을 제안하세요.")

            if not directions:
                directions.append(f"**{top_a['top_fmt']}** 포맷 + **{top_a['avg_cap']}자 내외 캡션** + **{top_a['top_day']}요일 게시**를 기본 공식으로 삼고, 매주 실험적 콘텐츠 1개를 섞어보세요.")

            for d in directions:
                st.markdown(f"- {d}")

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

                st.markdown(
                    f"<span style='font-size:13px'>"
                    f"좋아요 **{likes}** · 댓글 **{comments}** · 저장 **{saves}**"
                    f"</span>", unsafe_allow_html=True,
                )
                if isinstance(views, int):
                    st.caption(f"조회 {views:,} · 도달 {reach:,}")
                else:
                    st.caption(f"조회 {views} · 도달 {reach}")

                caption = post.get("caption") or ""
                if caption:
                    st.caption(caption[:80] + ("..." if len(caption) > 80 else ""))

                permalink = post.get("permalink", "")
                if permalink:
                    st.markdown(f"<a href='{permalink}' target='_blank' style='font-size:12px'>Instagram에서 보기</a>", unsafe_allow_html=True)

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
