# 17.07.26
# by @danpro00

import logging

from VibraVid.services._base.object import Season, SeasonManager
from VibraVid.provider.amazon import amazon_music


logger = logging.getLogger(__name__)


class AmazonAlbumScraper:
    """Resolve an Amazon Music album into the shared season/episode model."""

    def __init__(self, album_id: str) -> None:
        self.album_id = str(album_id).strip()
        self.title: str = ""
        self.artist: str = ""
        self.year: str = ""
        self.genre: str = ""
        self.cover_url: str = ""
        self.series_name: str = ""
        self.series_display_name: str = ""

        self.seasons_manager: SeasonManager = SeasonManager()
        self._tracks: list[dict] = []

    def fetch(self) -> None:
        data = amazon_music.get_album(self.album_id)
        if not data:
            raise ValueError(f"Amazon Music album not found: {self.album_id}")

        self.title = data.get("name") or "Unknown Album"
        self.artist = (data.get("artist") or {}).get("name", "")
        release_date = data.get("release_date") or ""
        self.year = release_date[-4:] if len(release_date) >= 4 else ""
        self.cover_url = data.get("image") or ""

        self._tracks = []
        for i, t in enumerate(data.get("songs") or [], start=1):
            self._tracks.append({
                "id": t.get("id"),
                "name": t.get("name") or "Unknown Track",
                "number": i,
                "url": t.get("url", ""),
                "duration": t.get("duration") or 0,
                "artist": (t.get("artist") or {}).get("name", "") or self.artist,
                "cover": t.get("image") or self.cover_url,
            })

        self.series_name = self.title
        self.series_display_name = self.title
        self.seasons_manager = SeasonManager()
        self.seasons_manager.add(Season(id=1, number=1, name=self.title))
        logger.info(f"[monochrome] AmazonAlbumScraper '{self.title}' by {self.artist}: {len(self._tracks)} track(s)")

    def getEpisodeSeasons(self, season_number: int) -> list[dict]:
        """Return the tracklist as episode dicts for the shared managers."""
        episodes = []
        for t in self._tracks:
            episodes.append({
                "id": t["id"],
                "name": t["name"],
                "number": t["number"],
                "url": t["url"],
                "duration_seconds": t["duration"],
                "artist": t["artist"],
                "album": self.title,
                "year": self.year,
                "cover": t["cover"],
            })
        return episodes