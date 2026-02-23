import time
import logging
from datetime import datetime, timezone
import requests
from config import Config

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [10, 30, 60]  # 재시도 간 대기(초): 10s → 30s → 60s


class InstagramClient:
    def __init__(self):
        self.base_url = Config.GRAPH_BASE_URL
        self.user_id = Config.IG_USER_ID
        self.access_token = Config.IG_ACCESS_TOKEN

    @staticmethod
    def _check_response(resp):
        """API 응답을 확인하고, 에러 시 상세 메시지를 포함하여 예외를 발생시킵니다."""
        if resp.status_code >= 400:
            try:
                error_data = resp.json().get("error", {})
                msg = error_data.get("message", resp.text)
                code = error_data.get("code", "")
                subcode = error_data.get("error_subcode", "")
                raise RuntimeError(
                    f"Instagram API 에러 [{resp.status_code}]: {msg} (code={code}, subcode={subcode})"
                )
            except (ValueError, KeyError):
                resp.raise_for_status()

    @staticmethod
    def _is_retryable(error):
        """재시도 가능한 에러인지 판단합니다."""
        err_str = str(error)
        # Instagram 서버 타임아웃 (code=-2), rate limit, 서버 에러
        return any(keyword in err_str for keyword in [
            "Timeout", "code=-2", "code=2", "code=4",
            "temporarily unavailable", "try again",
            "500", "502", "503",
        ])

    def _post_with_retry(self, url, params):
        """POST 요청을 재시도 로직과 함께 실행합니다."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(url, data=params)
                self._check_response(resp)
                return resp.json()
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1 and self._is_retryable(e):
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(f"  재시도 {attempt + 1}/{MAX_RETRIES} ({delay}초 대기): {e}")
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _create_child_container(self, image_url):
        """Step 1: 개별 캐러셀 아이템 컨테이너를 생성합니다."""
        url = f"{self.base_url}/{self.user_id}/media"
        params = {
            "image_url": image_url,
            "is_carousel_item": "true",
            "access_token": self.access_token,
        }
        data = self._post_with_retry(url, params)
        container_id = data["id"]
        logger.info(f"  child container 생성: {container_id}")
        return container_id

    def _wait_for_container(self, container_id, max_wait=180, interval=5):
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

        data = self._post_with_retry(url, params)
        carousel_id = data["id"]
        logger.info(f"  carousel container 생성: {carousel_id}")
        return carousel_id

    def _publish(self, carousel_container_id):
        """Step 3: 캐러셀을 즉시 발행합니다."""
        url = f"{self.base_url}/{self.user_id}/media_publish"
        params = {
            "creation_id": carousel_container_id,
            "access_token": self.access_token,
        }
        data = self._post_with_retry(url, params)
        media_id = data["id"]
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

        data = self._post_with_retry(url, params)
        container_id = data["id"]
        logger.info(f"  single container 생성: {container_id}")

        self._wait_for_container(container_id)

        if scheduled_time:
            logger.info(f"  예약 발행 설정 완료 (container: {container_id})")
            return {"status": "scheduled", "container_id": container_id}

        media_id = self._publish(container_id)
        return {"status": "published", "media_id": media_id}

    def publish_carousel(self, image_urls, caption, scheduled_time=None):
        """Figma 이미지 URL들을 Instagram 캐러셀로 발행합니다."""
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
            time.sleep(2)

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
        self._check_response(resp)
        return resp.json()

    def get_account_info(self):
        """계정 프로필 정보를 조회합니다 (팔로워 수, 게시물 수 등)."""
        url = f"{self.base_url}/{self.user_id}"
        params = {
            "fields": "followers_count,follows_count,media_count,username,name,biography,profile_picture_url",
            "access_token": self.access_token,
        }
        resp = requests.get(url, params=params)
        self._check_response(resp)
        return resp.json()

    def get_account_insights(self, metric, period="day", since=None, until=None):
        """계정 수준 인사이트를 조회합니다 (도달, 팔로워 증감 등)."""
        url = f"{self.base_url}/{self.user_id}/insights"
        params = {
            "metric": metric,
            "period": period,
            "access_token": self.access_token,
        }
        if since:
            params["since"] = int(since)
        if until:
            params["until"] = int(until)
        resp = requests.get(url, params=params)
        self._check_response(resp)
        return resp.json()

    def get_follower_demographics(self):
        """팔로워 인구통계 데이터를 조회합니다 (도시, 국가, 연령·성별)."""
        url = f"{self.base_url}/{self.user_id}/insights"
        metrics = "follower_demographics"
        breakdowns = ["city", "country", "age,gender"]
        results = {}
        for bd in breakdowns:
            try:
                params = {
                    "metric": metrics,
                    "period": "lifetime",
                    "metric_type": "total_value",
                    "timeframe": "this_month",
                    "breakdown": bd,
                    "access_token": self.access_token,
                }
                resp = requests.get(url, params=params)
                self._check_response(resp)
                data = resp.json().get("data", [])
                if data:
                    total_val = data[0].get("total_value", {})
                    results[bd.replace(",", "_")] = total_val.get("breakdowns", [{}])[0].get("results", [])
            except Exception as e:
                results[f"_error_{bd}"] = str(e)
        return results

    def get_daily_follower_metrics(self, since=None, until=None):
        """일별 팔로워 증감·도달·프로필 조회 데이터를 가져옵니다."""
        url = f"{self.base_url}/{self.user_id}/insights"
        metrics = "reach,follower_count,profile_views"
        params = {
            "metric": metrics,
            "period": "day",
            "access_token": self.access_token,
        }
        if since:
            params["since"] = int(since)
        if until:
            params["until"] = int(until)
        resp = requests.get(url, params=params)
        self._check_response(resp)
        return resp.json()

    def get_media_list(self, limit=25):
        """최근 게시물 목록을 조회합니다 (릴스/동영상 포함)."""
        url = f"{self.base_url}/{self.user_id}/media"
        params = {
            "fields": "id,caption,media_type,media_url,thumbnail_url,timestamp,like_count,comments_count,permalink,media_product_type",
            "limit": limit,
            "access_token": self.access_token,
        }
        resp = requests.get(url, params=params)
        self._check_response(resp)
        return resp.json()

    def get_media_insights(self, media_id, media_type="IMAGE"):
        """게시물의 인사이트 데이터를 조회합니다. v22.0+ 메트릭 사용."""
        url = f"{self.base_url}/{media_id}/insights"

        # v22.0+ 지원 메트릭 (impressions/plays 삭제됨)
        metrics = ["reach", "saved", "shares", "likes", "comments", "views"]

        result = {"_errors": []}

        # 1차: 전체 메트릭 한 번에 시도
        try:
            params = {
                "metric": ",".join(metrics),
                "access_token": self.access_token,
            }
            resp = requests.get(url, params=params)
            self._check_response(resp)
            data = resp.json().get("data", [])
            for item in data:
                result[item["name"]] = item["values"][0]["value"]
            result.pop("_errors", None)
            return result
        except Exception as e:
            result["_errors"].append(f"일괄조회 실패: {e}")

        # 2차: 개별 메트릭 하나씩 시도
        for metric in metrics:
            try:
                params = {
                    "metric": metric,
                    "access_token": self.access_token,
                }
                resp = requests.get(url, params=params)
                self._check_response(resp)
                data = resp.json().get("data", [])
                for item in data:
                    result[item["name"]] = item["values"][0]["value"]
            except Exception as e:
                result["_errors"].append(f"{metric}: {e}")

        if not result["_errors"]:
            result.pop("_errors", None)
        return result
