import logging
from datetime import datetime, timedelta
import requests
from config import Config

logger = logging.getLogger(__name__)


class TokenManager:
    """Facebook/Instagram 토큰 수명 주기를 관리합니다."""

    @staticmethod
    def exchange_for_long_lived(short_lived_token):
        """단기 토큰(~1시간)을 장기 토큰(~60일)으로 교환합니다."""
        url = f"{Config.GRAPH_BASE_URL}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": Config.META_APP_ID,
            "client_secret": Config.META_APP_SECRET,
            "fb_exchange_token": short_lived_token,
        }
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            f"장기 토큰 발급 완료 (만료: {data.get('expires_in', 5184000)}초)"
        )
        return {
            "access_token": data["access_token"],
            "expires_in": data.get("expires_in", 5184000),
        }

    @staticmethod
    def get_page_access_token(user_access_token):
        """Page Access Token을 조회합니다 (장기 사용자 토큰 기반 시 만료 없음)."""
        url = f"{Config.GRAPH_BASE_URL}/me/accounts"
        params = {"access_token": user_access_token}
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        pages = resp.json().get("data", [])
        for page in pages:
            logger.info(f"  페이지: {page['name']} (ID: {page['id']})")
        return pages

    @staticmethod
    def get_ig_user_id(page_id, page_access_token):
        """Facebook 페이지에 연결된 Instagram Business Account ID를 조회합니다."""
        url = f"{Config.GRAPH_BASE_URL}/{page_id}"
        params = {
            "fields": "instagram_business_account",
            "access_token": page_access_token,
        }
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        ig_id = resp.json()["instagram_business_account"]["id"]
        logger.info(f"  Instagram Business Account ID: {ig_id}")
        return ig_id

    @staticmethod
    def refresh_long_lived_token(existing_token):
        """장기 토큰을 갱신합니다 (만료 전에만 가능, 60일 연장)."""
        url = f"{Config.GRAPH_BASE_URL}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": Config.META_APP_ID,
            "client_secret": Config.META_APP_SECRET,
            "fb_exchange_token": existing_token,
        }
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        expires_in = data.get("expires_in", 5184000)
        new_expiry = datetime.now() + timedelta(seconds=expires_in)
        logger.info(f"토큰 갱신 완료 (새 만료일: {new_expiry.strftime('%Y-%m-%d')})")
        return {
            "access_token": data["access_token"],
            "expires_in": expires_in,
            "token_expiry": new_expiry.strftime("%Y-%m-%d"),
        }

    @staticmethod
    def is_token_expiring_soon(days_threshold=7):
        """저장된 토큰이 곧 만료되는지 확인합니다."""
        expiry_str = Config.IG_TOKEN_EXPIRY
        if not expiry_str:
            return True
        try:
            expiry = datetime.fromisoformat(expiry_str)
            return datetime.now() >= expiry - timedelta(days=days_threshold)
        except ValueError:
            return True
