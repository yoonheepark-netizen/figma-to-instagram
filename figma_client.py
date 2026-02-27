import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from config import Config

logger = logging.getLogger(__name__)


class FigmaClient:
    def __init__(self):
        self.headers = {"X-FIGMA-TOKEN": Config.FIGMA_TOKEN}
        self.base_url = Config.FIGMA_BASE_URL

    def get_file_frames(self, file_key=None):
        """Figma 파일의 모든 프레임 목록을 반환합니다 (SECTION 내부 포함)."""
        file_key = file_key or Config.FIGMA_FILE_KEY
        url = f"{self.base_url}/files/{file_key}"
        resp = requests.get(
            url, headers=self.headers, params={"depth": 3}, timeout=60
        )
        resp.raise_for_status()

        frames = []
        for page in resp.json()["document"]["children"]:
            for child in page.get("children", []):
                if child["type"] == "FRAME":
                    frames.append(
                        {
                            "id": child["id"],
                            "name": child["name"],
                            "page": page["name"],
                        }
                    )
                elif child["type"] == "SECTION":
                    for sub in child.get("children", []):
                        if sub["type"] == "FRAME":
                            frames.append(
                                {
                                    "id": sub["id"],
                                    "name": sub["name"],
                                    "page": page["name"],
                                    "section": child["name"],
                                }
                            )
        return frames

    def export_images(self, node_ids=None, fmt="png", scale=2, batch_size=10):
        """지정된 노드들을 이미지로 export합니다. {node_id: temp_url} dict를 반환합니다.
        Figma API는 한 번에 최대 ~50개 노드를 처리할 수 있으므로
        batch_size=10으로 묶어 호출합니다.
        """
        node_ids = node_ids or Config.FIGMA_NODE_IDS
        url = f"{self.base_url}/images/{Config.FIGMA_FILE_KEY}"
        all_images = {}

        for i in range(0, len(node_ids), batch_size):
            batch = node_ids[i : i + batch_size]
            ids_str = ",".join(batch)
            params = {"ids": ids_str, "format": fmt, "scale": scale}
            logger.info(
                f"  배치 {i // batch_size + 1}: {len(batch)}개 노드 export 중..."
            )

            resp = requests.get(
                url, headers=self.headers, params=params, timeout=30
            )
            resp.raise_for_status()

            images = resp.json().get("images", {})
            all_images.update(images)

            # 배치가 2개 이상일 때만 짧은 대기
            if i + batch_size < len(node_ids):
                time.sleep(0.3)

        failed = [nid for nid, u in all_images.items() if u is None]
        if failed:
            logger.warning(f"export 실패한 노드: {failed}")
        return all_images

    def extract_texts(self, node_ids=None, file_key=None):
        """지정된 노드(프레임)에 포함된 모든 TEXT 노드의 문자열을 추출합니다.
        Returns: {node_id: [text1, text2, ...]}
        """
        file_key = file_key or Config.FIGMA_FILE_KEY
        node_ids = node_ids or Config.FIGMA_NODE_IDS
        ids_str = ",".join(node_ids)
        url = f"{self.base_url}/files/{file_key}/nodes"
        params = {"ids": ids_str}
        resp = requests.get(url, headers=self.headers, params=params, timeout=30)
        resp.raise_for_status()
        nodes = resp.json().get("nodes", {})

        result = {}
        for nid, node_data in nodes.items():
            texts = []
            doc = node_data.get("document")
            if doc:
                self._collect_texts(doc, texts)
            result[nid] = texts
        return result

    @staticmethod
    def _collect_texts(node, texts):
        """재귀적으로 TEXT 노드의 characters를 수집합니다."""
        if node.get("type") == "TEXT":
            chars = node.get("characters", "").strip()
            if chars:
                texts.append(chars)
        for child in node.get("children", []):
            FigmaClient._collect_texts(child, texts)

    def download_images(self, image_urls, output_dir="downloads"):
        """export된 임시 URL에서 로컬로 병렬 다운로드합니다."""
        os.makedirs(output_dir, exist_ok=True)

        def _download_one(item):
            node_id, url = item
            if url is None:
                return None
            safe_name = node_id.replace(":", "-")
            filepath = os.path.join(output_dir, f"frame_{safe_name}.png")
            resp = requests.get(url, stream=True, timeout=30)
            resp.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            logger.info(f"  다운로드 완료: {filepath}")
            return filepath

        downloaded = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_download_one, item): item
                for item in image_urls.items()
            }
            for future in as_completed(futures):
                result = future.result()
                if result:
                    downloaded.append(result)

        return downloaded
