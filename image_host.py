import base64
import logging
import requests
from config import Config

logger = logging.getLogger(__name__)


class ImageHost:
    """imgbb를 사용한 이미지 호스팅 (Instagram은 공개 URL 필요)."""

    UPLOAD_URL = "https://api.imgbb.com/1/upload"

    def upload_image(self, image_path, expiration=86400):
        """로컬 이미지를 imgbb에 업로드하고 공개 URL을 반환합니다.

        Args:
            image_path: 로컬 이미지 파일 경로
            expiration: 자동 삭제까지 초 (기본 24시간)
        """
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "key": Config.IMGBB_API_KEY,
            "image": image_data,
            "expiration": expiration,
        }
        resp = requests.post(self.UPLOAD_URL, data=payload)
        resp.raise_for_status()
        result = resp.json()

        if not result.get("success"):
            raise RuntimeError(f"imgbb 업로드 실패: {result}")

        data = result["data"]
        public_url = data.get("display_url") or data.get("image", {}).get("url") or data["url"]
        logger.info(f"  업로드 완료: {image_path} → {public_url}")
        return public_url

    def upload_batch(self, image_paths, expiration=86400):
        """여러 이미지를 순차 업로드합니다. 공개 URL 리스트를 반환합니다."""
        urls = []
        for path in image_paths:
            url = self.upload_image(path, expiration)
            urls.append(url)
        return urls
