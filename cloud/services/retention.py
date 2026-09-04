"""
cloud/services/retention.py — 上傳原圖的保留天數清理

CLOUD_IMAGE_RETENTION_DAYS > 0 時，啟動跑一次、之後每 24 小時掃一次，
刪除 mtime 超過 N 天的檔案。預設 0（不刪）。

⚠️ 只掃 media/{CLOUD_MEDIA_SUBDIR}/（預設 media/cloud/），絕不碰 media/ 根目錄——
那是 app/ 那條管線的圖，誤刪會破壞既有紀錄。這正是雲端圖片要放獨立子目錄的主因。

順帶解決 deploy/hybrid-scaling-plan.md §5.3 提到的儲存成長問題
（原圖約 0.7MB/張，長期累積會吃滿磁碟）。
"""

import logging
import os
import threading
import time

from cloud import config

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_SECONDS = 24 * 60 * 60


def cloud_media_path() -> str:
    """雲端管線專用的圖片目錄（media/cloud/）。"""
    return os.path.join(config.media_dir(), config.media_subdir())


def sweep_once(days=None) -> int:
    """刪除超過保留天數的檔案，回傳刪除數量。days<=0 直接跳過。"""
    days = config.image_retention_days() if days is None else days
    if days <= 0:
        return 0

    root = cloud_media_path()
    if not os.path.isdir(root):
        return 0

    cutoff = time.time() - days * 86400
    removed = 0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            logger.warning("Retention sweep could not remove %s", path)

    if removed:
        logger.info("Retention sweep removed %d file(s) older than %d day(s) from %s",
                    removed, days, root)
    return removed


def start_background_sweeper() -> None:
    """背景 daemon thread：啟動掃一次，之後每 24 小時一次。失敗不影響服務。"""
    if config.image_retention_days() <= 0:
        logger.info("Image retention disabled (CLOUD_IMAGE_RETENTION_DAYS=0)")
        return

    def _loop():
        while True:
            try:
                sweep_once()
            except Exception:
                logger.exception("Retention sweep failed (non-fatal)")
            time.sleep(_SWEEP_INTERVAL_SECONDS)

    threading.Thread(target=_loop, name="cloud-retention", daemon=True).start()
    logger.info("Retention sweeper started (%d days, dir=%s)",
                config.image_retention_days(), cloud_media_path())
