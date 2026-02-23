"""GitHub Gist API 래퍼 — Pencil 매니페스트 관리."""

import json
import logging

import requests

from config import Config

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "pencil_manifest.json"


def new_manifest():
    """빈 매니페스트 구조를 반환합니다."""
    return {"version": 1, "updated_at": "", "series": {}}


class GistManager:
    """GitHub Gist의 pencil_manifest.json을 생성/조회/갱신합니다."""

    API_BASE = "https://api.github.com/gists"

    def __init__(self, token=None):
        self.token = token or Config.GITHUB_TOKEN
        if not self.token:
            raise ValueError(
                "GitHub 토큰이 필요합니다. "
                "GITHUB_TOKEN 환경변수 또는 .env에 설정하세요."
            )
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def get_manifest(self, gist_id):
        """Gist에서 매니페스트 JSON을 가져옵니다."""
        resp = requests.get(f"{self.API_BASE}/{gist_id}", headers=self.headers, timeout=15)
        resp.raise_for_status()
        files = resp.json().get("files", {})
        manifest_file = files.get(MANIFEST_FILENAME)
        if manifest_file and manifest_file.get("content"):
            return json.loads(manifest_file["content"])
        return new_manifest()

    def create_gist(self, manifest, description="Pencil card news manifest"):
        """새 Gist를 생성합니다. gist_id를 반환합니다."""
        payload = {
            "description": description,
            "public": False,
            "files": {
                MANIFEST_FILENAME: {
                    "content": json.dumps(manifest, ensure_ascii=False, indent=2)
                }
            },
        }
        resp = requests.post(self.API_BASE, headers=self.headers, json=payload, timeout=15)
        resp.raise_for_status()
        gist_id = resp.json()["id"]
        logger.info(f"Gist 생성 완료: {gist_id}")
        return gist_id

    def update_gist(self, gist_id, manifest):
        """기존 Gist의 매니페스트를 갱신합니다."""
        payload = {
            "files": {
                MANIFEST_FILENAME: {
                    "content": json.dumps(manifest, ensure_ascii=False, indent=2)
                }
            }
        }
        resp = requests.patch(
            f"{self.API_BASE}/{gist_id}",
            headers=self.headers,
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Gist 업데이트 완료: {gist_id}")

    def upsert_series(self, gist_id, series_name, series_data):
        """시리즈를 추가/교체합니다. gist_id가 None이면 새 Gist를 생성합니다."""
        if gist_id:
            manifest = self.get_manifest(gist_id)
        else:
            manifest = new_manifest()

        manifest["series"][series_name] = series_data
        manifest["updated_at"] = series_data.get("uploaded_at", "")

        if gist_id:
            self.update_gist(gist_id, manifest)
            return gist_id
        else:
            return self.create_gist(manifest)

    def list_series(self, gist_id):
        """Gist에 있는 모든 시리즈 이름과 메타정보를 반환합니다."""
        manifest = self.get_manifest(gist_id)
        return [
            {"name": k, "count": v.get("count", 0), "uploaded_at": v.get("uploaded_at", "")}
            for k, v in manifest.get("series", {}).items()
        ]
