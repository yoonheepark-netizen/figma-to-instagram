import time
import logging
from datetime import datetime, timezone
import requests
from config import Config

logger = logging.getLogger(__name__)


class InstagramClient:
    def __init__(self):
        self.base_url = Config.GRAPH_BASE_URL
        self.user_id = Config.IG_USER_ID
        self.access_token = Config.IG_ACCESS_TOKEN

    def _create_child_container(self, image_url):
        """Step 1: 개별 캐러셀 아이템 컨테이너를 생성합니다."""
        url = f"{self.base_url}/{self.user_id}/media"
        params = {
            "image_url": image_url,
            "is_carousel_item": "true",
            "access_token": self.access_token,
        }
        resp = requests.post(url, data=params)
        resp.raise_for_status()
        container_id = resp.json()["id"]
        logger.info(f"  child container 생성: {container_id}")
        return container_id

    def _wait_for_container(self, container_id, max_wait=60, interval=5):
        """컨테이너 상태가 FINISHED가 될 때까지 폴링합니다."""
        url = f"{self.base_url}/{container_id}"
        params = {"fields": "status_code", "access_token": self.access_token}

        for _ in range(max_wait // interval):
            resp = requests.get(url, params=params)
            status = resp.json().get("status_code")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                raise RuntimeError(
                    f"컨테이너 {container_id} 에러 상태: {resp.json()}"
                )
            time.sleep(interval)

        raise TimeoutError(
            f"컨테이너 {container_id}가 {max_wait}초 내에 완료되지 않았습니다"
        )

    def _create_carousel_container(self, child_ids, caption, scheduled_time=None):
        """Step 2: 캐러셀 부모 컨테이너를 생성합니다."""
        url = f"{self.base_url}/{self.user_id}/media"
        params = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": self.access_token,
        }

        if scheduled_time:
            if isinstance(scheduled_time, datetime):
                ts = int(scheduled_time.timestamp())
            else:
                ts = int(scheduled_time)
            params["scheduled_publish_time"] = ts

        resp = requests.post(url, data=params)
        resp.raise_for_status()
        carousel_id = resp.json()["id"]
        logger.info(f"  carousel container 생성: {carousel_id}")
        return carousel_id

    def _publish(self, carousel_container_id):
        """Step 3: 캐러셀을 즉시 발행합니다."""
        url = f"{self.base_url}/{self.user_id}/media_publish"
        params = {
            "creation_id": carousel_container_id,
            "access_token": self.access_token,
        }
        resp = requests.post(url, data=params)
        resp.raise_for_status()
        media_id = resp.json()["id"]
        logger.info(f"  발행 완료! media_id: {media_id}")
        return media_id

    def publish_single(self, image_url, caption, scheduled_time=None):
        """단일 이미지를 Instagram에 발행합니다."""
        url = f"{self.base_url}/{self.user_id}/media"
        params = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        }

        if scheduled_time:
            if isinstance(scheduled_time, datetime):
                ts = int(scheduled_time.timestamp())
            else:
                ts = int(scheduled_time)
            params["scheduled_publish_time"] = ts

        resp = requests.post(url, data=params)
        resp.raise_for_status()
        container_id = resp.json()["id"]
        logger.info(f"  single container 생성: {container_id}")

        self._wait_for_container(container_id)

        if scheduled_time:
            logger.info(f"  예약 발행 설정 완료 (container: {container_id})")
            return {"status": "scheduled", "container_id": container_id}

        media_id = self._publish(container_id)
        return {"status": "published", "media_id": media_id}

    def publish_carousel(self, image_urls, caption, scheduled_time=None):
        """Figma 이미지 URL들을 Instagram 캐러셀로 발행합니다.

        Args:
            image_urls: 공개 이미지 URL 리스트 (2~10장)
            caption: 게시물 캡션
            scheduled_time: 예약 시간 (datetime 또는 Unix timestamp, 선택)

        Returns:
            dict: {"status": "published"|"scheduled", "media_id"|"container_id": ...}
        """
        if len(image_urls) < 2:
            raise ValueError("캐러셀은 최소 2장의 이미지가 필요합니다")
        if len(image_urls) > 10:
            raise ValueError("캐러셀은 최대 10장까지 지원합니다")

        # Step 1: child container 생성
        logger.info(f"  {len(image_urls)}개 child container 생성 중...")
        child_ids = []
        for img_url in image_urls:
            cid = self._create_child_container(img_url)
            self._wait_for_container(cid)
            child_ids.append(cid)
            time.sleep(1)

        # Step 2: carousel container 생성
        logger.info("  carousel container 생성 중...")
        carousel_id = self._create_carousel_container(
            child_ids, caption, scheduled_time
        )
        self._wait_for_container(carousel_id)

        # Step 3: 발행 (예약 발행 시에는 Instagram이 자동 처리)
        if scheduled_time:
            logger.info(f"  예약 발행 설정 완료 (container: {carousel_id})")
            return {"status": "scheduled", "container_id": carousel_id}

        media_id = self._publish(carousel_id)
        return {"status": "published", "media_id": media_id}

    def check_publishing_limit(self):
        """현재 발행 rate limit 상태를 확인합니다."""
        url = f"{self.base_url}/{self.user_id}/content_publishing_limit"
        params = {
            "fields": "config,quota_usage",
            "access_token": self.access_token,
        }
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
