import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Figma
    FIGMA_TOKEN = os.getenv("FIGMA_TOKEN")
    FIGMA_FILE_KEY = os.getenv("FIGMA_FILE_KEY")
    FIGMA_NODE_IDS = [
        nid.strip()
        for nid in os.getenv("FIGMA_NODE_IDS", "").split(",")
        if nid.strip()
    ]

    # imgbb
    IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")

    # Instagram / Facebook
    META_APP_ID = os.getenv("META_APP_ID")
    META_APP_SECRET = os.getenv("META_APP_SECRET")
    IG_USER_ID = os.getenv("INSTAGRAM_USER_ID")
    IG_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    IG_TOKEN_EXPIRY = os.getenv("INSTAGRAM_TOKEN_EXPIRY")

    # Publishing
    DEFAULT_CAPTION = os.getenv("DEFAULT_CAPTION", "")
    PUBLISH_MODE = os.getenv("PUBLISH_MODE", "immediate")
    SCHEDULED_TIME = os.getenv("SCHEDULED_TIME")

    # GitHub (Gist 매니페스트)
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    PENCIL_GIST_ID = os.getenv("PENCIL_GIST_ID")

    # Anthropic (캡션 생성)
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

    # API versions
    GRAPH_API_VERSION = "v21.0"
    GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    FIGMA_BASE_URL = "https://api.figma.com/v1"
