import logging
import os
import requests

logger = logging.getLogger(__name__)

def _github_token():
    """GITHUB_TOKEN을 환경변수 또는 st.secrets에서 가져옵니다."""
    val = os.getenv("GITHUB_TOKEN", "")
    if val:
        return val
    try:
        import streamlit as st
        if "api" in st.secrets and "GITHUB_TOKEN" in st.secrets["api"]:
            return str(st.secrets["api"]["GITHUB_TOKEN"])
    except Exception:
        pass
    return ""

# 모듈 레벨 캐시: {gist_id: manifest_dict}
_manifest_cache = {}
# owner 캐시: {gist_id: owner_login}
_owner_cache = {}


class PencilClient:
    """GitHub Gist 매니페스트를 통한 Pencil.dev 이미지 클라이언트.

    Pencil.dev는 데스크톱 앱(클라우드 API 없음)이므로,
    로컬 `cardupload` 스크립트가 생성한 GitHub Gist를 브릿지로 사용합니다.

    사용 흐름:
        1. 로컬: Pencil.dev에서 카드뉴스 export → ~/Downloads/카드뉴스/
        2. 로컬: `cardupload` 실행 → imgbb 업로드 + Gist 매니페스트 생성
        3. 앱:   PencilClient.get_series(gist_id) → 시리즈 목록 반환
    """

    GIST_RAW_BASE = "https://gist.githubusercontent.com"
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

    def clear_cache(self, gist_id=None):
        """캐시를 초기화합니다. gist_id 지정 시 해당 항목만 삭제."""
        if gist_id:
            _manifest_cache.pop(gist_id, None)
        else:
            _manifest_cache.clear()

    def _fetch_manifest(self, gist_id):
        """GitHub Gist에서 매니페스트 JSON을 가져옵니다.

        gist_id 형식:
            - "gist_id" → GitHub API로 owner를 자동 조회
            - "owner/gist_id" → 직접 raw URL 생성

        캐싱: 동일 gist_id는 세션 내 재요청하지 않습니다.
        Rate limit 대응: GitHub API 403 시 캐시된 owner로 재시도합니다.
        """
        # 캐시 히트
        if gist_id in _manifest_cache:
            logger.info(f"  Gist 매니페스트 캐시 히트: {gist_id}")
            return _manifest_cache[gist_id]

        if "/" in gist_id:
            owner, gid = gist_id.split("/", 1)
            url = f"{self.GIST_RAW_BASE}/{owner}/{gid}/raw/{self.MANIFEST_FILE}"
        else:
            owner = _owner_cache.get(gist_id)
            if not owner:
                try:
                    api_url = f"https://api.github.com/gists/{gist_id}"
                    headers = {}
                    token = _github_token()
                    if token:
                        headers["Authorization"] = f"token {token}"
                    api_resp = requests.get(api_url, headers=headers, timeout=10)
                    api_resp.raise_for_status()
                    owner = api_resp.json().get("owner", {}).get("login", "")
                    _owner_cache[gist_id] = owner
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code in (403, 401):
                        # rate limit 또는 인증 실패 → 기본 owner로 폴백
                        logger.warning("GitHub API 실패, 기본 owner로 폴백")
                        owner = "yoonheepark-netizen"
                        _owner_cache[gist_id] = owner
                    else:
                        raise
            url = f"{self.GIST_RAW_BASE}/{owner}/{gist_id}/raw/{self.MANIFEST_FILE}"

        logger.info(f"  Gist 매니페스트 로드: {gist_id}")
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        manifest = resp.json()

        # 캐시 저장
        _manifest_cache[gist_id] = manifest
        return manifest
