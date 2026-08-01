# 22.02.25

import logging
import threading

from VibraVid.utils.vault.supa import supa_vault

logger = logging.getLogger(__name__)


class SupaTracker:
    def fire(self, title: str, media_type: str, site: str) -> None:
        """Fire-and-forget background thread to track a download in SupaVault."""
        try:
            threading.Thread(target=self._run, args=(title, media_type, site), daemon=False).start()
        except Exception:
            logger.debug("SupaTracker: failed to start background thread (ignored)")

    @staticmethod
    def _run(title: str, media_type: str, site: str) -> None:
        """Track a download in SupaVault.  Logs errors but never raises."""
        try:
            if not supa_vault:
                logger.warning("SupaTracker: supa_vault not initialized")
                return

            title_str = (title or "").strip() or "://generic"
            media_type_str = (media_type or "").strip()
            site_str = (site or "").strip().lower()
            logger.info(f"SupaTracker: title={title_str} type={media_type_str} service={site_str}")
            result = supa_vault.track_download(title_str, media_type_str, site_str)
            logger.info(f"SupaTracker result: {result}")
        except Exception as exc:
            logger.error(f"SupaTracker error: {exc}", exc_info=True)
