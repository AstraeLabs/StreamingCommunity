# 16.03.25

import re
import time
import json
import logging
from urllib.parse import urlparse, quote

from bs4 import BeautifulSoup

from VibraVid.utils.http_client import create_client_curl, get_userAgent, get_headers
from VibraVid.services._base.object import SeasonManager, Episode, Season


logger = logging.getLogger(__name__)


class GetSerieInfo:
    BAD_WORDS = [
        'Trailer', 'Promo', 'Teaser', 'Clip', 'Backstage', 'Le interviste', 'BALLETTI', 'Anteprime web', 'I servizi', 'Video trend', 'Extra', 'Le trame della settimana', 'Esclusive',
        'INTERVISTE', 'SERVIZI', 'Gossip', 'Prossimi appuntamenti tv', 'DAYTIME', 'Ballo', 'Canto', 'Band', 'Senza ADV', 'Il serale'
    ]

    def __init__(self, url):
        """
        Initialize the GetSerieInfo class for scraping TV series information.
        
        Args:
            - url (str): The URL of the streaming site.
        """
        self.headers = get_headers()
        self.url = url
        self.client = create_client_curl()
        self.seasons_manager = SeasonManager()
        self.serie_id = None
        self.public_id = None
        self.series_name = ""
        self.stagioni_disponibili = []

    def _extract_serie_id(self):
        """Extract the series ID from the starting URL"""
        try:
            after = self.url.split('SE', 1)[1]
            after = after.split(',')[0].strip()
            self.serie_id = f"SE{after}"
            return self.serie_id
        except Exception as e:
            logger.error(f"Failed to extract serie id from url {self.url}: {e}")
            self.serie_id = None
            return None

    def _get_public_id(self):
        """Get the public ID for API calls"""
        self.public_id = "PR1GhC"
        return self.public_id

    def _get_series_data(self):
        """Get series data through the API"""
        try:
            params = {'byGuid': self.serie_id}
            url = f'https://feed.entertainment.tv.theplatform.eu/f/{self.public_id}/mediaset-prod-all-series-v2'
            response = self.client.get(url, params=params, headers=self.headers)
            if response.status_code == 200 and response.text.strip().startswith('{'):
                return response.json()
            else:
                logger.error(f"Unexpected response from series API: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Failed to get series data with error: {str(e)}")
            return None

    def _process_available_seasons(self, data):
        """Process available seasons from series data"""
        if not data or not data.get('entries'):
            logger.error("No series data found in API")
            return []

        entry = data['entries'][0]
        self.series_name = entry.get('title', '')
        
        seriesTvSeasons = entry.get('seriesTvSeasons', [])
        availableTvSeasonIds = entry.get('availableTvSeasonIds', [])

        stagioni_disponibili = []

        for url in availableTvSeasonIds:
            season = next((s for s in seriesTvSeasons if s['id'] == url), None)
            if season:
                stagioni_disponibili.append({
                    'tvSeasonNumber': season['tvSeasonNumber'],
                    'title': season.get('title', ''),
                    'url': url,
                    'id': str(url).split("/")[-1],
                    'guid': season['guid']
                })
            else:
                logger.error(f"Season URL not found: {url}")

        # Sort seasons from oldest to newest
        stagioni_disponibili.sort(key=lambda s: s['tvSeasonNumber'])
        
        return stagioni_disponibili

    def _fallback_homepage_scrape(self):
        """Fallback: Scrape carousels directly from the homepage if no seasons are found via API"""
        print(f"Fallback: Scraping homepage directly from {self.url}")
        dummy_season = {
            'tvSeasonNumber': 1,
            'title': 'Stagione 1',
            'url': None,
            'id': self.serie_id,
            'guid': self.serie_id,
            'page_url': self.url
        }
        
        # Try to extract sb IDs from the homepage URL
        self._extract_season_sb_ids([dummy_season])
        
        if dummy_season.get('categories'):
            self.stagioni_disponibili = [dummy_season]
            return True
            
        return False

    def _build_season_page_urls(self, stagioni_disponibili):
        """Build season page URLs"""
        parsed_url = urlparse(self.url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        series_slug = parsed_url.path.strip('/').split('/')[-1].split('_')[0]

        for season in stagioni_disponibili:
            page_url = f"{base_url}/fiction/{series_slug}/{series_slug}{season['tvSeasonNumber']}_{self.serie_id},{season['guid']}"
            season['page_url'] = page_url

    def _extract_season_sb_ids(self, stagioni_disponibili):
        """Extract sb IDs from season pages"""
        for season in stagioni_disponibili:
            if not season.get('page_url'):
                continue
                
            response_page = self.client.get(season['page_url'], headers={'User-Agent': get_userAgent()})
            
            if not response_page or response_page.status_code != 200:
                logger.error(f"Failed to fetch season page: {season.get('page_url')}")
                continue
                
            print("Response for _extract_season_sb_ids:", response_page.status_code, " Season:", season['tvSeasonNumber'])
            time.sleep(0.5)
            soup = BeautifulSoup(response_page.text, 'html.parser')
            
            # Check for titleCarousel links (multiple categories)
            carousel_links = soup.find_all('a', class_='titleCarousel')
            
            if carousel_links:
                print(f"Found {len(carousel_links)} titleCarousel categories")
                season['categories'] = []
                
                for carousel_link in carousel_links:
                    if carousel_link.has_attr('href'):
                        category_title = carousel_link.find('h2')
                        category_name = category_title.text.strip() if category_title else 'Unnamed'
                        if any(w.lower() in category_name.lower() for w in self.BAD_WORDS):
                            continue
                            
                        href = carousel_link['href']
                        if ',' in href:
                            sb_id = href.split(',')[-1]
                        else:
                            sb_id = href.split('_')[-1]

                        season['categories'].append({
                            'name': category_name,
                            'sb': sb_id
                        })
            else:
                logger.error(f"No titleCarousel categories found for season {season['tvSeasonNumber']}")

    def _get_season_episodes(self, season, sb_id, category_name):
        """Get episodes for a specific season"""
        print("Getting episodes for season", season['tvSeasonNumber'], "category:", category_name, "sb_id:", sb_id)
        
        if any(w.lower() in category_name.lower() for w in self.BAD_WORDS):
            return []

        # If the category is the full listing (site paginates "Tutti gli episodi"), use the programs feed
        if 'tutti' in category_name.lower() or category_name.lower().startswith('all'):
            episodes = self._get_all_season_episodes(season)
        elif sb_id.startswith('sb'):
            episodes = self._get_episodes_from_feed_api(sb_id, season['tvSeasonNumber'])
        else:
            # try RSC extraction first; if this is the "Tutti" collection but only the first page
            # is returned, fall back to the full programs feed
            episodes = self._extract_episodes_from_rsc_text(sb_id, season['tvSeasonNumber'], category_name, season.get('guid'))
            if 'tutti' in category_name.lower() and len(episodes) <= 24:
                fallback = self._get_all_season_episodes(season)
                if fallback:
                    episodes = fallback
        
        print(f"Found {len(episodes)} episodes for season {season['tvSeasonNumber']} ({category_name})")
        return episodes

    def _get_all_season_episodes(self, season):
        """Fetch the full programs feed for the season and return a list of Episode objects for all entries."""
        print("Getting all episodes for season", season['tvSeasonNumber'], " v2")
        time.sleep(1)
        
        try:
            programs_url = f"https://feed.entertainment.tv.theplatform.eu/f/{self.public_id}/mediaset-prod-all-programs-v2"
            params = {
                'byTvSeasonId': season.get('url') or season.get('id'),
                'range': '0-699',
                'sort': ':publishInfo_lastPublished|asc,tvSeasonEpisodeNumber|asc'
            }
            data = self.client.get(programs_url, params=params, headers={'user-agent': get_userAgent()}).json()
            if not data:
                return []

            episodes = []
            for entry in data.get('entries', []):
                duration = int(entry.get('mediasetprogram$duration', 0) / 60) if entry.get('mediasetprogram$duration') else 0
                if duration < 10:
                    continue

                ep_num = entry.get('tvSeasonEpisodeNumber') or entry.get('mediasetprogram$episodeNumber')
                try:
                    ep_num = int(ep_num) if ep_num else 0
                except Exception:
                    ep_num = 0

                episode = Episode(
                    id=entry.get('guid'),
                    name=entry.get('title'),
                    url=entry.get('media')[0].get('publicUrl'),
                    duration=duration,
                    number=ep_num,
                    category=entry.get('mediasetprogram$category', 'programs_feed'),
                    description=entry.get('description', ''),
                    season_number=season.get('tvSeasonNumber')
                )
                episodes.append(episode)

            return episodes
        except Exception as e:
            logger.error(f"_get_all_season_episodes failed for season {season.get('tvSeasonNumber')}: {e}")
            return []

    def _extract_episodes_from_rsc_text(self, sb_id, season_number, category_name, guid=None):
        """Extract episodes from RSC response text"""
        episodes = []
        
        # Standard browse URL for RSC extraction
        href = f"/browse/{category_name.lower().replace(' ', '-')}_{sb_id}"
        
        browse_url = f"https://mediasetinfinity.mediaset.it{href}"
        print("Constructed browse URL for RSC:", browse_url)
        
        # Create the router state
        url_path = browse_url.split('mediasetinfinity.mediaset.it/')[1] if 'mediasetinfinity.mediaset.it/' in browse_url else browse_url
        state = ["", {"children": [["path", url_path, "c"], {"children": ["__PAGE__", {}, None, "refetch"]}, None, None]}, None, None]
        router_state_tree = quote(json.dumps(state, separators=(',', ':')))

        rsc_headers = {
            'rsc': '1',
            'next-router-state-tree': router_state_tree,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        for attempt in range(3):
            try:
                episode_response = self.client.get(browse_url, headers=rsc_headers)
                status = getattr(episode_response, 'status_code', None)
                if status and status >= 400:
                    episode_response.raise_for_status()

                text = episode_response.text
                pattern = r'"__typename":"VideoItem".*?"url":"https://mediasetinfinity\.mediaset\.it/video/[^"]*?"'
                
                for match in re.finditer(pattern, text, re.DOTALL):
                    block = match.group(0)
                    ep = {}
                    fields = {
                        'title': r'"cardTitle":"([^"]*?)"',
                        'description': r'"description":"([^"]*?)"',
                        'duration': r'"duration":(\d+)',
                        'guid': r'"guid":"([^"]*?)"',
                        'url': r'"url":"(https://mediasetinfinity\.mediaset\.it/video/[^"]*?)"'
                    }
                    
                    for key, regex in fields.items():
                        m = re.search(regex, block)
                        if m:
                            ep[key] = int(m.group(1)) if key == 'duration' else m.group(1)
                    
                    if ep:
                        duration = int(ep.get('duration', 0) / 60) if ep.get('duration') else 0
                        if duration < 10:
                            continue

                        episode = Episode(
                            id=ep.get('guid', ''),
                            name=ep.get('title', ''),
                            url=ep.get('url', ''),
                            duration=duration,
                            number=0,  # Will be set later
                            category=category_name,
                            description=ep.get('description', ''),
                            season_number=season_number
                        )
                        episodes.append(episode)
                
                if episodes:
                    return episodes
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Attempt {attempt+1} failed for season {season_number} with error: {e}")
                time.sleep(1)
        
        return episodes

    def _get_episodes_from_feed_api(self, sb_id, season_number):
        """Get episodes from programs feed API for sb-prefixed IDs"""
        episodes = []
        try:
            clean_sb_id = sb_id[2:] if sb_id.startswith('sb') else sb_id
            params = {
                'byCustomValue': "{subBrandId}{" + str(clean_sb_id) + "}",
                'sort': ':publishInfo_lastPublished|asc,tvSeasonEpisodeNumber|asc',
                'range': '0-699',
            }
            episode_url = f"https://feed.entertainment.tv.theplatform.eu/f/{self.public_id}/mediaset-prod-all-programs-v2"
            response = self.client.get(episode_url, params=params, headers={'user-agent': get_userAgent()})
            
            if response.status_code == 200:
                data = response.json()
                for entry in data.get('entries', []):
                    duration = int(entry.get('mediasetprogram$duration', 0) / 60) if entry.get('mediasetprogram$duration') else 0
                    if duration < 10:
                        continue
                    
                    ep_num = entry.get('tvSeasonEpisodeNumber') or entry.get('mediasetprogram$episodeNumber', 0)
                    try:
                        ep_num = int(ep_num)
                    except Exception:
                        ep_num = 0
                    
                    episode = Episode(
                        id=entry.get('guid', ''),
                        name=entry.get('title', ''),
                        duration=duration,
                        url=entry.get('media', [{}])[0].get('publicUrl') if entry.get('media') else '',
                        number=ep_num,
                        category='programs_feed',
                        description=entry.get('description', ''),
                        season_number=season_number
                    )
                    episodes.append(episode)
        except Exception as e:
            logger.error(f"Error fetching episodes from feed API for sb_id {sb_id}: {e}")
        
        return episodes

    def collect_season(self) -> None:
        """
        Retrieve all episodes for all seasons using the new Mediaset Infinity API.
        """
        try:
            # Step 1: Extract serie ID from URL
            self._extract_serie_id()
            
            # Step 2: Get public ID
            if not self._get_public_id():
                logger.error("Failed to get public ID")
                return
                
            # Step 3: Get series data
            data = self._get_series_data()
            if data:
                # Step 4: Process available seasons
                self.stagioni_disponibili = self._process_available_seasons(data)
                
            # Fallback if no seasons found or API failed
            if not self.stagioni_disponibili:
                logger.info("No seasons found via API. Attempting fallback homepage scrape...")
                self._fallback_homepage_scrape()
            
            if not self.stagioni_disponibili:
                logger.error("No seasons found even after fallback")
                return
                
            # Step 5: Build season page URLs - ONLY for seasons coming from API
            api_seasons = [s for s in self.stagioni_disponibili if s.get('url')]
            if api_seasons:
                self._build_season_page_urls(api_seasons)
            
            # Step 6: Extract sb IDs from season pages (if not already extracted by fallback)
            seasons_to_extract = [s for s in self.stagioni_disponibili if 'categories' not in s]
            if seasons_to_extract:
                self._extract_season_sb_ids(seasons_to_extract)

            # Step 7: Collect episodes from categories
            for season in self.stagioni_disponibili:
                season['episodes'] = []
                
                if 'categories' in season:
                    for category in season['categories']:
                        if any(w.lower() in category['name'].lower() for w in self.BAD_WORDS):
                            continue
                        print(f"Processing category: {category['name']} for season {season['tvSeasonNumber']}")
                        episodes = self._get_season_episodes(season, category['sb'], category['name'])
                        
                        existing_ids = {ep.id for ep in season['episodes']}
                        for ep in episodes:
                            if ep.id not in existing_ids:
                                season['episodes'].append(ep)
                                existing_ids.add(ep.id)
            
            # Step 8: Populate seasons manager
            self._populate_seasons_manager()

        except Exception as e:
            logger.error(f"Error in collect_season: {str(e)}")

    def _populate_seasons_manager(self):
        """Populate the seasons_manager with collected data - ONLY for seasons with episodes"""
        seasons_with_episodes = 0
        
        for season_data in self.stagioni_disponibili:
            
            # Add season to manager ONLY if it has episodes
            if season_data.get('episodes') and len(season_data['episodes']) > 0:
                season_obj = self.seasons_manager.add(Season(
                    number=season_data['tvSeasonNumber'],
                    name=f"Season {season_data['tvSeasonNumber']}",
                    id=season_data.get('id')
                ))
                
                if season_obj:
                    for ep in season_data['episodes']:
                        season_obj.episodes.add(ep)
                    seasons_with_episodes += 1
    
    
    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the series.
        """
        if not self.seasons_manager.seasons:
            self.collect_season()
            
        return len(self.seasons_manager.seasons)
    
    def getEpisodeSeasons(self, season_number: int) -> list:
        """
        Get all episodes for a specific season.
        """
        if not self.seasons_manager.seasons:
            self.collect_season()

        season = self.seasons_manager.get_season_by_number(season_number)
        if season:
            return season.episodes.episodes

        available_numbers = [s.number for s in self.seasons_manager.seasons]
        logger.error(f"Season {season_number} not found. Available seasons: {available_numbers}")
        return []