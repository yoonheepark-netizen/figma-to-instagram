import os
from dotenv import load_dotenv

load_dotenv()


def _get(key, default=""):
    """os.getenv 우선, 없으면 st.secrets에서 읽기 (Streamlit Cloud 호환)"""
    val = os.getenv(key)
    if val:
        return val
    try:
        import streamlit as st
        # st.secrets["api"]["KEY"] 또는 st.secrets["KEY"] 형태 모두 지원
        if "api" in st.secrets and key in st.secrets["api"]:
            return st.secrets["api"][key]
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default


class Config:
    # Figma
    FIGMA_TOKEN = _get("FIGMA_TOKEN")
    FIGMA_FILE_KEY = _get("FIGMA_FILE_KEY")
    FIGMA_NODE_IDS = [
        nid.strip()
        for nid in _get("FIGMA_NODE_IDS").split(",")
        if nid.strip()
    ]

    # imgbb
    IMGBB_API_KEY = _get("IMGBB_API_KEY")

    # Instagram / Facebook
    META_APP_ID = _get("META_APP_ID")
    META_APP_SECRET = _get("META_APP_SECRET")
    IG_USER_ID = _get("INSTAGRAM_USER_ID")
    IG_ACCESS_TOKEN = _get("INSTAGRAM_ACCESS_TOKEN")
    IG_TOKEN_EXPIRY = _get("INSTAGRAM_TOKEN_EXPIRY")

    # Publishing
    DEFAULT_CAPTION = _get("DEFAULT_CAPTION")
    PUBLISH_MODE = _get("PUBLISH_MODE", "immediate")
    SCHEDULED_TIME = _get("SCHEDULED_TIME")

    # GitHub (Gist 매니페스트)
    GITHUB_TOKEN = _get("GITHUB_TOKEN")
    PENCIL_GIST_ID = _get("PENCIL_GIST_ID")

    # AI 캡션 생성 (Groq 무료 → Anthropic 폴백)
    GROQ_API_KEY = _get("GROQ_API_KEY")
    ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")

    # 이미지 소싱
    UNSPLASH_ACCESS_KEY = _get("UNSPLASH_ACCESS_KEY")
    GOOGLE_API_KEY = _get("GOOGLE_API_KEY")

    # GIF / 영상 소싱
    GIPHY_API_KEY = _get("GIPHY_API_KEY")
    PEXELS_API_KEY = _get("PEXELS_API_KEY")

    # API versions
    GRAPH_API_VERSION = "v21.0"
    GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    FIGMA_BASE_URL = "https://api.figma.com/v1"
