import logging
import requests

logger = logging.getLogger(__name__)


class PencilClient:
    """GitHub Gist 매니페스트를 통한 Pencil.dev 이미지 클라이언트.

    Pencil.dev는 데스크톱 앱(클라우드 API 없음)이므로,
    로컬 `cardupload` 스크립트가 생성한 GitHub Gist를 브릿지로 사용합니다.

    사용 흐름:
        1. 로컬: Pencil.dev에서 카드뉴스 export → ~/Downloads/카드뉴스/
        2. 로컬: `cardupload` 실행 → imgbb 업로드 + Gist 매니페스트 생성
        3. 앱:   PencilClient.get_series(gist_id) → 시리즈 목록 반환
    """

    GIST_API_URL = "https://api.github.com/gists"
    MANIFEST_FILE = "pencil_manifest.json"

    def get_series(self, gist_id):
        """Gist 매니페스트에서 모든 시리즈를 가져옵니다.

        Returns:
            list[dict]: 각 시리즈 정보. 키: name, count, uploaded_at, images
                        images는 [{"name": str, "url": str}, ...] 형태
        """
        manifest = self._fetch_manifest(gist_id)
        raw_series = manifest.get("series", {})

        series_list = []
        for name, data in raw_series.items():
            series_list.append({
                "name": name,
                "count": data.get("count", len(data.get("images", []))),
                "uploaded_at": data.get("uploaded_at", ""),
                "images": data.get("images", []),
            })

        # 최신순 정렬
        series_list.sort(key=lambda s: s.get("uploaded_at", ""), reverse=True)
        return series_list

    def get_series_images(self, gist_id, series_name):
        """특정 시리즈의 이미지 URL 목록을 반환합니다.

        Returns:
            list[str]: imgbb 공개 URL 리스트 (순서 보장)
        """
        manifest = self._fetch_manifest(gist_id)
        series_data = manifest.get("series", {}).get(series_name)
        if not series_data:
            raise ValueError(f"시리즈 '{series_name}'을(를) 찾을 수 없습니다.")
        return [img["url"] for img in series_data.get("images", [])]

    def _fetch_manifest(self, gist_id):
        """GitHub Gist API에서 매니페스트 JSON을 가져옵니다."""
        url = f"{self.GIST_API_URL}/{gist_id}"
        logger.info(f"  Gist 매니페스트 로드: {gist_id}")

        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        gist_data = resp.json()
        manifest_file = gist_data.get("files", {}).get(self.MANIFEST_FILE)
        if not manifest_file:
            raise ValueError(f"Gist에 {self.MANIFEST_FILE} 파일이 없습니다.")

        import json
        return json.loads(manifest_file["content"])
