#!/usr/bin/env python3
"""Pencil 카드뉴스 이미지를 imgbb에 업로드하고 GitHub Gist 매니페스트를 갱신합니다.

사용법:
    # 디렉토리의 이미지를 업로드 (기존 Gist에 시리즈 추가)
    python cardupload.py --series "0223-봄이벤트" --dir ./exports/ --gist-id abc123

    # 첫 실행 시 새 Gist 자동 생성
    python cardupload.py --series "0223-봄이벤트" --images slide1.png slide2.png

    # 시리즈 목록 확인
    python cardupload.py --list-series --gist-id abc123
"""

import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import Config
from gist_manager import GistManager
from image_host import ImageHost

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def natural_sort_key(path):
    """자연 정렬 키 — slide2.png가 slide10.png보다 앞에 오도록."""
    return [
        int(s) if s.isdigit() else s.lower()
        for s in re.split(r"(\d+)", path.name)
    ]


def collect_from_dir(dir_path):
    """디렉토리에서 이미지 파일을 수집합니다 (자연 정렬)."""
    d = Path(dir_path)
    if not d.is_dir():
        logger.error(f"디렉토리를 찾을 수 없습니다: {dir_path}")
        sys.exit(1)
    files = sorted(
        [f for f in d.iterdir() if f.suffix.lower() in IMAGE_EXTS],
        key=natural_sort_key,
    )
    if not files:
        logger.error(f"이미지 파일이 없습니다: {dir_path}")
        sys.exit(1)
    return files


def collect_from_paths(paths):
    """명시적 파일 경로들을 검증하고 반환합니다."""
    files = []
    for p in paths:
        f = Path(p)
        if not f.is_file():
            logger.error(f"파일을 찾을 수 없습니다: {p}")
            sys.exit(1)
        files.append(f)
    return files


def upload_and_build_entry(image_paths, series_name, expiration):
    """이미지를 imgbb에 업로드하고 매니페스트 시리즈 엔트리를 생성합니다."""
    host = ImageHost()
    str_paths = [str(p) for p in image_paths]

    logger.info(f"[2/3] imgbb 업로드 중... ({len(str_paths)}개)")
    urls = host.upload_batch(str_paths, expiration=expiration)

    return {
        "count": len(urls),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "images": [
            {"name": p.stem, "url": u}
            for p, u in zip(image_paths, urls)
        ],
    }


def cmd_upload(args):
    """이미지 업로드 + Gist 매니페스트 갱신."""
    # 1. 이미지 수집
    if args.dir:
        image_paths = collect_from_dir(args.dir)
    elif args.images:
        image_paths = collect_from_paths(args.images)
    else:
        logger.error("--dir 또는 --images 중 하나를 지정하세요.")
        sys.exit(1)

    logger.info(f"[1/3] 이미지 수집: {len(image_paths)}개")
    for p in image_paths:
        logger.info(f"  - {p.name}")

    # 2. imgbb 업로드 + 엔트리 생성
    series_data = upload_and_build_entry(image_paths, args.series, args.expiration)

    if args.dry_run:
        logger.info("\n[dry-run] Gist 갱신을 건너뜁니다.")
        for img in series_data["images"]:
            logger.info(f"  {img['name']} → {img['url']}")
        return

    # 3. Gist 매니페스트 갱신
    logger.info("[3/3] Gist 매니페스트 갱신 중...")
    gist_id = args.gist_id or Config.PENCIL_GIST_ID
    gm = GistManager()
    result_id = gm.upsert_series(gist_id, args.series, series_data)

    logger.info(f"\n완료! Gist ID: {result_id}")
    logger.info("Streamlit 앱의 Pencil Gist ID에 위 값을 입력하세요.")


def cmd_list(args):
    """Gist의 시리즈 목록을 출력합니다."""
    gist_id = args.gist_id or Config.PENCIL_GIST_ID
    if not gist_id:
        logger.error("--gist-id 또는 PENCIL_GIST_ID 환경변수를 지정하세요.")
        sys.exit(1)

    gm = GistManager()
    series_list = gm.list_series(gist_id)

    if not series_list:
        logger.info("시리즈가 없습니다.")
        return

    logger.info(f"Gist {gist_id} 시리즈 목록:")
    for s in series_list:
        logger.info(f"  [{s['count']}장] {s['name']}  ({s['uploaded_at']})")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Pencil 카드뉴스 → imgbb → GitHub Gist 매니페스트"
    )
    parser.add_argument("--series", help="시리즈 이름 (예: 0223-봄이벤트)")
    parser.add_argument("--dir", help="이미지 디렉토리 경로")
    parser.add_argument("--images", nargs="+", help="이미지 파일 경로들")
    parser.add_argument("--gist-id", help="기존 Gist ID (없으면 새로 생성)")
    parser.add_argument(
        "--expiration", type=int, default=604800,
        help="imgbb 만료 시간(초) (기본: 604800 = 7일)",
    )
    parser.add_argument("--dry-run", action="store_true", help="업로드만 하고 Gist 갱신 안 함")
    parser.add_argument("--list-series", action="store_true", help="시리즈 목록 출력")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list_series:
        cmd_list(args)
        return

    if not args.series:
        logger.error("--series 이름을 지정하세요.")
        sys.exit(1)

    cmd_upload(args)


if __name__ == "__main__":
    main()
