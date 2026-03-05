# 06.06.25

from django.db import models
from django.utils import timezone


class WatchlistItem(models.Model):
    name = models.CharField(max_length=255)
    source_alias = models.CharField(max_length=100)
    item_payload = models.TextField()
    is_movie = models.BooleanField(default=False)
    poster_url = models.URLField(max_length=500, null=True, blank=True)
    tmdb_id = models.CharField(max_length=50, null=True, blank=True)
    num_seasons = models.IntegerField(default=0)
    last_season_episodes = models.IntegerField(default=0)

    auto_enabled = models.BooleanField(default=False)
    auto_season = models.IntegerField(null=True, blank=True)
    auto_last_episode_count = models.IntegerField(default=0)
    auto_last_checked_at = models.DateTimeField(null=True, blank=True)
    auto_last_downloaded_at = models.DateTimeField(null=True, blank=True)
    
    # Metadata for tracking changes
    added_at = models.DateTimeField(default=timezone.now)
    last_checked_at = models.DateTimeField(default=timezone.now)
    
    # Flags to indicate new content
    has_new_seasons = models.BooleanField(default=False)
    has_new_episodes = models.BooleanField(default=False)

    class Meta:
        ordering = ['-added_at']
        unique_together = ('name', 'source_alias')

    def __str__(self):
        return f"{self.name} ({self.source_alias})"