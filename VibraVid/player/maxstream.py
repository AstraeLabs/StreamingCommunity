# 29.05.26
# By @UrloMythus

import re
import base64
import logging

from bs4 import BeautifulSoup

from VibraVid.utils.http_client import create_client, get_userAgent


logger = logging.getLogger(__name__)
_M3U8_RE = re.compile(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*')
_CONTINUE_PARTS_RE = re.compile(
    r'decodedBaseUrl\s*=\s*atob\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)\s*;\s*'
    r'var\s+\w+\s*=\s*atob\(\s*["\']([A-Za-z0-9+/=]+)["\']\s*\)',
)


class MaxStreamSource:
    PLAYBACK_HEADERS = {
        'Referer': 'https://maxstream.video/',
        'Origin': 'https://maxstream.video',
    }

    def __init__(self, uprot_link: str, referer: str = ''):
        self.uprot_link = uprot_link
        self.referer    = referer

    def get_stream(self) -> tuple[str | None, dict]:
        """
        Returns (stream_url, playback_headers) or (None, {}) on failure.
        """
        mse_link = self.uprot_link.replace('/msf/', '/mse/')
        headers = {
            'User-Agent': get_userAgent(),
            'Referer': self.referer or 'https://uprot.net/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        client = create_client(headers=headers)

        try:
            resp = client.get(mse_link)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, 'html.parser')
            continue_url = None
            for a in soup.find_all('a'):
                if 'continue' in a.get_text(strip=True).lower():
                    continue_url = str(a.get('href', ''))
                    break

            if not continue_url:
                parts_match = _CONTINUE_PARTS_RE.search(resp.text)
                if parts_match:
                    try:
                        base_url = base64.b64decode(parts_match.group(1)).decode()
                        token = base64.b64decode(parts_match.group(2)).decode()
                        continue_url = base_url + token
                    except Exception as e:
                        logger.error(f"[MaxStream] Failed to decode Continue link parts: {e}")

            if not continue_url:
                logger.error(f"[MaxStream] No Continue link in mse page: {mse_link}")
                return None, {}

            resp2 = client.get(continue_url, headers={**headers, 'Referer': mse_link})
            resp2.raise_for_status()

        except Exception as e:
            logger.error(f"[MaxStream] Failed to resolve {self.uprot_link}: {e}")
            return None, {}
        
        finally:
            client.close()

        m = _M3U8_RE.search(resp2.text)
        if not m:
            logger.error(f"[MaxStream] No m3u8 found in page for {continue_url}")
            return None, {}

        return m.group(0), dict(self.PLAYBACK_HEADERS)