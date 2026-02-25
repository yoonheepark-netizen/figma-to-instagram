"""미디어 소싱 — GIF·영상·이미지 (1분건강톡 릴스용).

소싱 우선순위:
  1) Tenor GIF (기존 Gemini Google API 키 활용, mp4 형식)
  2) GIPHY GIF (별도 키 필요)
  3) Pexels 영상 클립
  4) Unsplash 정적 이미지 (폴백)

Tenor API 활성화 (1클릭):
  https://console.developers.google.com/apis/api/tenor.googleapis.com/overview
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from typing import Literal

import requests

logger = logging.getLogger(__name__)

# ── API 키 ───────────────────────────────────────────────
_TENOR_KEY = os.getenv("GEMINI_API_KEY", "")  # Google API 키 = Tenor API 키
_GIPHY_KEY = os.getenv("GIPHY_API_KEY", "")  # developers.giphy.com에서 무료 발급
_PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
_UNSPLASH_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

MediaType = Literal["gif", "video", "image"]


# ═════════════════════════════════════════════════════════
# Tenor GIF (Google API 키로 작동)
# ═════════════════════════════════════════════════════════

def search_tenor(query: str, limit: int = 8) -> list[dict]:
    """Tenor에서 GIF 검색. mp4 URL 포함."""
    if not _TENOR_KEY:
        return []
    url = "https://tenor.googleapis.com/v2/search"
    params = {
        "q": query,
        "key": _TENOR_KEY,
        "client_key": "1min_health_tok",
        "limit": limit,
        "media_filter": "mp4,gif,tinygif",
        "contentfilter": "medium",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            logger.debug(f"Tenor API 실패 [{resp.status_code}]: {resp.text[:200]}")
            return []
        results = []
        for item in resp.json().get("results", []):
            media = item.get("media_formats", {})
            mp4_info = media.get("mp4", {})
            gif_info = media.get("gif", {})
            tiny_info = media.get("tinygif", {})
            results.append({
                "type": "gif",
                "source": "tenor",
                "mp4_url": mp4_info.get("url", ""),
                "gif_url": gif_info.get("url", ""),
                "preview_url": tiny_info.get("url", ""),
                "width": mp4_info.get("dims", [0, 0])[0],
                "height": mp4_info.get("dims", [0, 0])[1],
                "duration": mp4_info.get("duration", 0),
                "title": item.get("title", ""),
            })
        return [r for r in results if r["mp4_url"] or r["gif_url"]]
    except Exception as e:
        logger.debug(f"Tenor 검색 실패: {e}")
        return []


# ═════════════════════════════════════════════════════════
# GIPHY GIF
# ═════════════════════════════════════════════════════════

def search_giphy(query: str, limit: int = 8) -> list[dict]:
    """GIPHY에서 GIF 검색. mp4 URL 포함."""
    if not _GIPHY_KEY:
        return []
    url = "https://api.giphy.com/v1/gifs/search"
    params = {
        "api_key": _GIPHY_KEY,
        "q": query,
        "limit": limit,
        "rating": "g",
        "lang": "ko",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        results = []
        for item in resp.json().get("data", []):
            images = item.get("images", {})
            original = images.get("original", {})
            results.append({
                "type": "gif",
                "source": "giphy",
                "mp4_url": original.get("mp4", ""),
                "gif_url": original.get("url", ""),
                "preview_url": images.get("fixed_width", {}).get("url", ""),
                "width": int(original.get("width", 0)),
                "height": int(original.get("height", 0)),
                "title": item.get("title", ""),
            })
        return [r for r in results if r["mp4_url"] or r["gif_url"]]
    except Exception as e:
        logger.debug(f"GIPHY 검색 실패: {e}")
        return []


# ═════════════════════════════════════════════════════════
# Pexels Video
# ═════════════════════════════════════════════════════════

def search_pexels_video(query: str, limit: int = 5) -> list[dict]:
    """Pexels에서 영상 클립 검색 (portrait 우선)."""
    if not _PEXELS_KEY:
        return []
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": _PEXELS_KEY}
    params = {
        "query": query,
        "per_page": limit,
        "orientation": "portrait",
        "size": "medium",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        results = []
        for v in resp.json().get("videos", []):
            files = v.get("video_files", [])
            # HD portrait 우선
            best = None
            for f in sorted(files, key=lambda x: x.get("height", 0), reverse=True):
                if f.get("quality") in ("hd", "sd") and f.get("height", 0) >= 720:
                    best = f
                    break
            if not best and files:
                best = files[0]
            if best:
                results.append({
                    "type": "video",
                    "source": "pexels",
                    "url": best["link"],
                    "width": best.get("width", 0),
                    "height": best.get("height", 0),
                    "duration": v.get("duration", 0),
                    "preview_url": v.get("image", ""),
                })
        return results
    except Exception as e:
        logger.debug(f"Pexels 검색 실패: {e}")
        return []


# ═════════════════════════════════════════════════════════
# Unsplash 이미지 (폴백)
# ═════════════════════════════════════════════════════════

def search_unsplash_portrait(query: str, limit: int = 4) -> list[dict]:
    """Unsplash에서 세로(portrait) 이미지 검색."""
    if not _UNSPLASH_KEY:
        return []
    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query,
        "per_page": limit,
        "orientation": "portrait",
        "content_filter": "high",
        "client_id": _UNSPLASH_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        results = []
        for item in resp.json().get("results", []):
            urls = item.get("urls", {})
            raw = urls.get("raw", "")
            if raw:
                # Imgix 파라미터로 세로 크롭 (9:16)
                cropped = f"{raw}&w=1080&h=1920&fit=crop&crop=entropy&q=85&fm=jpg"
            else:
                cropped = urls.get("regular", "")
            results.append({
                "type": "image",
                "source": "unsplash",
                "url": cropped,
                "preview_url": urls.get("small", ""),
                "photographer": item.get("user", {}).get("name", ""),
            })
        return [r for r in results if r["url"]]
    except Exception as e:
        logger.debug(f"Unsplash 검색 실패: {e}")
        return []


# ═════════════════════════════════════════════════════════
# 통합 검색 (폴백 체인)
# ═════════════════════════════════════════════════════════

def search_media(query: str, preferred_type: MediaType = "gif") -> dict | None:
    """미디어 통합 검색. 소싱 우선순위에 따라 폴백.

    Returns: 미디어 메타데이터 dict 또는 None
    """
    if preferred_type == "gif":
        # 1) Tenor GIF
        results = search_tenor(query, limit=5)
        if results:
            return results[0]
        # 2) GIPHY GIF
        results = search_giphy(query, limit=5)
        if results:
            return results[0]

    if preferred_type == "video":
        # 3) Pexels Video
        results = search_pexels_video(query, limit=3)
        if results:
            return results[0]

    # 4) Unsplash 이미지 폴백
    # GIF/영상 검색어를 이미지용으로 변환 (반응 키워드 제거)
    img_query = _clean_query_for_image(query)
    results = search_unsplash_portrait(img_query, limit=3)
    if results:
        return results[0]

    return None


def _clean_query_for_image(query: str) -> str:
    """GIF 검색어에서 리액션 키워드 제거 → 이미지 검색용으로 변환."""
    reaction_words = {
        "reaction", "meme", "funny", "lol", "shocked", "surprised",
        "omg", "mind blown", "laugh", "relatable", "mood", "same",
        "gif", "cute", "dramatic", "cringe",
    }
    words = query.lower().split()
    cleaned = [w for w in words if w not in reaction_words]
    return " ".join(cleaned) if cleaned else "health wellness lifestyle"


# ═════════════════════════════════════════════════════════
# 다운로드
# ═════════════════════════════════════════════════════════

def download_media(media: dict, timeout: int = 30) -> bytes | None:
    """미디어 메타데이터에서 파일 다운로드.

    GIF → mp4_url 우선, 없으면 gif_url
    Video → url
    Image → url
    """
    if media["type"] == "gif":
        url = media.get("mp4_url") or media.get("gif_url") or media.get("url", "")
    else:
        url = media.get("url", "")

    if not url:
        return None

    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        if resp.status_code == 200:
            data = resp.content
            logger.info(f"미디어 다운로드 완료: {media['type']}/{media.get('source', '?')} "
                        f"({len(data) / 1024:.0f} KB)")
            return data
        logger.debug(f"미디어 다운로드 실패 [{resp.status_code}]: {url[:80]}")
    except Exception as e:
        logger.debug(f"미디어 다운로드 실패: {e}")
    return None


def search_and_download(query: str, preferred_type: MediaType = "gif") -> tuple[bytes | None, dict | None]:
    """검색 + 다운로드 통합. (bytes, metadata) 반환."""
    media = search_media(query, preferred_type)
    if not media:
        return None, None
    data = download_media(media)
    return data, media


# ═════════════════════════════════════════════════════════
# API 상태 확인
# ═════════════════════════════════════════════════════════

def get_available_sources() -> dict[str, bool]:
    """설정된 API 키 기반 사용 가능한 소스 확인."""
    return {
        "tenor": bool(_TENOR_KEY),
        "giphy": bool(_GIPHY_KEY),
        "pexels": bool(_PEXELS_KEY),
        "unsplash": bool(_UNSPLASH_KEY),
    }


def check_api_status() -> dict[str, str]:
    """각 API의 실제 동작 여부 확인."""
    status = {}

    # Tenor
    if _TENOR_KEY:
        try:
            r = requests.get("https://tenor.googleapis.com/v2/search",
                             params={"q": "test", "key": _TENOR_KEY, "limit": 1},
                             timeout=5)
            status["tenor"] = "active" if r.status_code == 200 else f"error ({r.status_code})"
        except Exception:
            status["tenor"] = "timeout"
    else:
        status["tenor"] = "no_key"

    # GIPHY
    if _GIPHY_KEY:
        try:
            r = requests.get("https://api.giphy.com/v1/gifs/search",
                             params={"api_key": _GIPHY_KEY, "q": "test", "limit": 1},
                             timeout=5)
            status["giphy"] = "active" if r.status_code == 200 else f"error ({r.status_code})"
        except Exception:
            status["giphy"] = "timeout"
    else:
        status["giphy"] = "no_key"

    # Pexels
    if _PEXELS_KEY:
        try:
            r = requests.get("https://api.pexels.com/v1/search",
                             headers={"Authorization": _PEXELS_KEY},
                             params={"query": "test", "per_page": 1},
                             timeout=5)
            status["pexels"] = "active" if r.status_code == 200 else f"error ({r.status_code})"
        except Exception:
            status["pexels"] = "timeout"
    else:
        status["pexels"] = "no_key"

    # Unsplash
    if _UNSPLASH_KEY:
        status["unsplash"] = "active (fallback)"
    else:
        status["unsplash"] = "no_key"

    return status
