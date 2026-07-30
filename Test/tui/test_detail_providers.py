import pytest
from VibraVid.tui.screens.detail import TitleDetailScreen

class MockItem:
    def __init__(self, name: str):
        self.name = name
        self.is_movie = False

def test_detail_screen_multi_provider_init():
    item1 = MockItem("Breaking Bad")
    item2 = MockItem("Breaking Bad")
    providers = [("animeworld", item1), ("streamingcommunity", item2)]
    
    screen = TitleDetailScreen(site="animeworld", item=item1, providers=providers)
    assert len(screen._providers) == 2
    assert screen._current_site == "animeworld"
