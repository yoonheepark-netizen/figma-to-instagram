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

ACCOUNTS_FILE = os.path.join(os.path.dirname(__file__), "accounts.json")


# ── 계정 관리 ──────────────────────────────────────────────


def load_accounts():
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("accounts", [])
    try:
        if "accounts" in st.secrets:
            return [dict(a) for a in st.secrets["accounts"]]
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

    try:
        req.post(webhook_url, json={"blocks": blocks}, timeout=5)
    except Exception:
        pass


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


def publish_one_group(group_name, node_ids, caption, scheduled_time, account, status_container):
    """하나의 그룹을 Instagram 캐러셀로 발행합니다. 결과 dict를 반환합니다."""
    result_info = {"group": group_name, "count": len(node_ids), "caption": caption, "account_name": account["name"], "success": False}

    try:
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

        status_container.write(f"☁️ [{group_name}] 이미지 업로드 중...")
        host = ImageHost()
        public_urls = host.upload_batch(ordered_files, expiration=86400)

        status_container.write(f"📸 [{group_name}] Instagram에 발행 중...")
        ig = InstagramClient()
        ig.user_id = account["instagram_user_id"]
        ig.access_token = account["access_token"]

        result = ig.publish_carousel(public_urls, caption, scheduled_time)

        result_info["success"] = True
        if result["status"] == "published":
            result_info["media_id"] = result["media_id"]
        else:
            result_info["container_id"] = result["container_id"]

    except Exception as e:
        result_info["error"] = str(e)

    return result_info


# ── 페이지 설정 ───────────────────────────────────────────

st.set_page_config(
    page_title="카드뉴스 → Instagram",
    page_icon="📸",
    layout="wide",
)

st.title("📸 카드뉴스 Instagram 발행")

# ── 사이드바: 계정 & 설정 ─────────────────────────────────

with st.sidebar:
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
                else:
                    st.caption(f"토큰 만료: {expiry} ({days_left}일 남음)")
            except ValueError:
                pass

    st.divider()

    figma_file_key = st.text_input(
        "Figma 파일 키",
        value=os.getenv("FIGMA_FILE_KEY", ""),
        help="Figma URL에서 /file/ 뒤의 문자열",
    )

    # Slack 설정 표시
    slack_url = get_slack_webhook()
    if slack_url:
        st.caption("🔔 Slack 알림: 연결됨")
    else:
        st.caption("🔕 Slack 알림: 미설정")

    st.divider()

    with st.expander("계정 관리"):
        st.caption("새 계정 추가")
        new_name = st.text_input("계정 이름", key="new_name")
        new_ig_id = st.text_input("Instagram User ID", key="new_ig_id")
        new_token = st.text_input("Access Token", key="new_token", type="password")
        new_expiry = st.text_input("토큰 만료일 (YYYY-MM-DD)", key="new_expiry")

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

# ── 메인: Step 1 - 프레임 선택 (다중 그룹) ────────────────

if not accounts:
    st.info("사이드바에서 Instagram 계정을 먼저 추가해주세요.")
    st.stop()

st.header("Step 1. 프레임 선택")

if "frames" not in st.session_state:
    st.session_state.frames = None
    st.session_state.frame_groups = None
    st.session_state.ungrouped = None

col_load, col_info = st.columns([1, 3])
with col_load:
    if st.button("🔄 프레임 불러오기", use_container_width=True):
        with st.spinner("Figma에서 프레임 목록을 가져오는 중..."):
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
            f"{len(st.session_state.frame_groups or {})}개 날짜 그룹"
        )

if st.session_state.frame_groups:
    groups = st.session_state.frame_groups

    # 다중 그룹 선택 (multiselect)
    selected_groups = st.multiselect(
        "날짜 선택 (여러 개 선택 가능, 최신순)",
        list(groups.keys()),
        format_func=lambda x: f"{x} ({len(groups[x])}장)",
    )

    if selected_groups:
        st.info(f"✅ {len(selected_groups)}개 시리즈 선택됨")

        # 각 그룹의 프레임 표시 및 개별 선택
        all_selected = {}  # {group_name: [node_ids]}
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
                st.caption(f"{len(selected_frames)}장 선택")
                if len(selected_frames) >= 2:
                    all_selected[grp] = [f["id"] for f in selected_frames]
                elif len(selected_frames) == 1:
                    st.warning("캐러셀은 최소 2장 필요합니다.")

        st.session_state.all_selected = all_selected

# ── 메인: Step 2 - 시리즈별 발행 설정 ─────────────────────

if st.session_state.get("all_selected"):
    all_selected = st.session_state.all_selected

    st.divider()
    st.header("Step 2. 시리즈별 발행 설정")

    # 시리즈별 설정 저장
    group_settings = {}  # {grp: {"caption": ..., "mode": ..., "scheduled_time": ...}}

    account_names = [a["name"] for a in accounts]

    for grp in all_selected:
        with st.expander(f"📁 {grp} — {len(all_selected[grp])}장", expanded=True):
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
            "이미지": f"{len(all_selected[grp])}장",
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
            overall_progress = st.progress(0)
            results = []

            for idx, (grp, node_ids) in enumerate(all_selected.items()):
                settings = group_settings[grp]
                status = st.status(f"[{idx + 1}/{total}] {grp} 발행 중...", expanded=True)

                result_info = publish_one_group(
                    group_name=grp,
                    node_ids=node_ids,
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
                    status.update(label=f"❌ {grp} 실패: {result_info.get('error', '')[:50]}", state="error")

                overall_progress.progress((idx + 1) / total)

            # 결과 요약
            success_count = sum(1 for r in results if r["success"])
            fail_count = total - success_count

            if fail_count == 0:
                st.success(f"🎉 {success_count}개 시리즈 모두 발행 성공!")
                st.balloons()
            else:
                st.warning(f"완료: 성공 {success_count}개 / 실패 {fail_count}개")

            # Slack 알림
            send_slack_notification(results)
            if get_slack_webhook():
                st.caption("🔔 Slack 알림 전송 완료")
