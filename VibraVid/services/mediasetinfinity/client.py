# 16.03.25

import base64
import json
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from rich.console import Console

from VibraVid.services._base.login_status import ACCOUNT, ANONYMOUS, print_login
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers, get_userAgent

from .regions import get_region, region_conf

logger = logging.getLogger(__name__)
console = Console()
_api_by_region = {}


def _decode_jwt_payload(token: str) -> dict:
    """Decode a JWT payload without signature verification."""
    try:
        part = token.split(".")[1]
        part += "=" * (4 - len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _is_token_valid(token: str) -> bool:
    """Check if a JWT token is still valid (with 5-minute buffer)."""
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp", 0)
    return exp > time.time() + 300


class MediasetAPI:
    def __init__(self, conf: dict):
        self.conf = conf
        self.client_id = str(uuid.uuid4())
        self.sid = self.client_id
        self.headers = get_headers()
        self.app_name = self.get_app_name()

        # Optional stored login token for this region.
        self.adminBeToken = None
        self.account_id = None
        self.is_anonymous = True
        login_token = config_manager.login.get(conf["login_key"], "adminBeToken", default=None)

        if login_token:
            if _is_token_valid(login_token):
                self.adminBeToken = login_token
                self.account_id = _decode_jwt_payload(login_token).get("oid")
                self.is_anonymous = False
            else:
                console.print("[yellow]Login adminBeToken expired, falling back to anonymous token...")
                self.adminBeToken = self.generate_betoken()
                self.account_id = _decode_jwt_payload(self.adminBeToken).get("oid")
        else:
            self.adminBeToken = self.generate_betoken()
            self.account_id = _decode_jwt_payload(self.adminBeToken).get("oid")

        # Passed as a value, not a resolver: this name is decoded from the token with no network
        # call, so it must not be gated behind DEFAULT.get_me like the ones that cost a request.
        print_login(
            ANONYMOUS if self.is_anonymous else ACCOUNT,
            user="" if self.is_anonymous else self._account_name(),
        )

        self.sha256Hash = None
        self._hash_attempted = False

    def _account_name(self) -> str:
        """
        Account name carried by the adminBeToken itself - no network call needed.

        The address lives at ctx.attributes.email; the sibling ctx.email and ctx.fullName are
        present but come back empty, so reading those alone would look like a missing account.
        """
        if not self.adminBeToken:
            return ""

        try:
            ctx = json.loads(_decode_jwt_payload(self.adminBeToken).get("ctx") or "{}")
        except (TypeError, ValueError):
            return self.account_id or ""

        attributes = ctx.get("attributes") or {}
        return attributes.get("email") or ctx.get("fullName") or self.account_id or ""

    def get_app_name(self):
        soup = BeautifulSoup(self.fetch_html(head_only=True), "html.parser")
        meta_tag = soup.find("meta", attrs={"name": "app-name"})
        return meta_tag.get("content") if meta_tag else "web//mediasetplay-web/1.3.0"

    def getHash256(self):
        if self.sha256Hash is None and not self._hash_attempted:
            self._hash_attempted = True
            try:
                self.sha256Hash = self.getHash2c()
            except Exception as e:
                logger.debug(f"getHash2c unavailable for region {get_region()}: {e}")
        return self.sha256Hash

    def getBearerToken(self):
        return self.adminBeToken

    def get_basic_token(self):
        raw = f":{self.adminBeToken}"
        return base64.b64encode(raw.encode('utf-8')).decode('utf-8').strip()

    def getAccountId(self):
        return self.account_id

    def generate_betoken(self):
        json_data = {"appName": self.app_name, "client_id": self.client_id}
        with create_client(headers=self.headers) as client:
            response = client.post(self.conf["login_url"], json=json_data)
            data = response.json()["response"]
            self.sid = data.get("sid", self.client_id)
            return data["beToken"]

    def fetch_html(self, head_only: bool = False):
        headers = dict(self.headers)
        if head_only:
            headers["Range"] = "bytes=0-300000"
        with create_client(headers=headers) as client:
            return client.get(self.conf["home_url"]).text

    def find_relevant_script(self, html):
        soup = BeautifulSoup(html, "html.parser")
        return [s.get_text() for s in soup.find_all("script") if "imageEngines" in s.get_text()]

    def extract_pairs_from_scripts(self, scripts):
        relevant_part = scripts[0].replace('\\"', "").split("...Option")[1].split("imageEngines")[0]
        pairs = {}
        for match in re.finditer(r"([a-f0-9]{64}):\$(\w+)", relevant_part):
            pairs[match.group(1)] = f"${match.group(2)}"
        return pairs

    def getHash2c(self):
        from .regions import REGIONS

        it_home = REGIONS["it"]["home_url"]
        with create_client(headers=self.headers) as client:
            html = client.get(it_home).text

        scripts = self.find_relevant_script(html)[0:1]
        if not scripts:
            return None

        pairs = self.extract_pairs_from_scripts(scripts)
        return list(pairs.keys())[-5]

    def generate_request_headers(self):
        return {
            "authorization": self.adminBeToken,
            "user-agent": self.headers["user-agent"],
            "x-m-device-id": self.client_id,
            "x-m-platform": "WEB",
            "x-m-property": self.conf["property"],
            "x-m-sid": self.sid,
        }


def get_client():
    """Get or create the region-specific MediasetAPI singleton."""
    region = get_region()
    api = _api_by_region.get(region)
    if api is None or not _is_token_valid(api.getBearerToken()):
        if api is not None:
            console.print("[yellow]adminBeToken expired, re-authenticating...")
        api = MediasetAPI(region_conf())
        _api_by_region[region] = api
    return api


def get_playback_url(CONTENT_ID):
    conf = region_conf()
    api = get_client()

    headers = get_headers()
    headers["authorization"] = f"Bearer {api.getBearerToken()}"
    headers["origin"] = conf["origin"]
    headers["referer"] = conf["origin"] + "/"

    json_data = {"contentId": CONTENT_ID, "streamType": "VOD"}
    params = None
    if conf.get("playback_api") == "v3":
        json_data.update({"delivery": "Streaming", "createDevice": True, "overrideAppName": api.app_name})
        params = {"sid": api.sid}
    elif conf.get("playback_api") == "v2":
        params = {"sid": api.sid}

    try:
        with create_client(headers=headers) as client:
            response = client.post(conf["playback_url"], json=json_data, params=params)
            response.raise_for_status()
            resp_json = response.json()

        err = resp_json.get("error") if isinstance(resp_json, dict) else None
        if err:
            code = err.get("code")
            if code == "PL022":
                raise RuntimeError("Infinity+ required for this content.")
            if code == "PL402":
                raise RuntimeError("Content available for rental: you must rent it first.")
            if code == "PL053":
                raise RuntimeError("Content has no available purchasable rights")
            raise RuntimeError(f"{code}: {err.get('message')}")

        return resp_json["response"]["mediaSelector"]

    except Exception as e:
        raise RuntimeError(f"Failed to get playback URL error: {e}") from e


def get_metadata_by_guid(guid, feed):
    """Fetch a single entry's metadata from a theplatform feed by GUID."""
    conf = region_conf()
    url = f"https://feed.entertainment.tv.theplatform.eu/f/{conf['feed_public_id']}/{feed}"
    try:
        with create_client(headers={"user-agent": get_userAgent()}) as client:
            response = client.get(url, params={"byGuid": guid})
            response.raise_for_status()
            entries = response.json().get("entries", [])
            return entries[0] if entries else None
    except Exception as e:
        console.print(f"[red]Failed to fetch metadata for guid '{guid}': {e}")
        return None


def parse_smil_for_media_info(smil_xml):
    """Extract video streams and subtitle streams from a theplatform SMIL."""
    root = ET.fromstring(smil_xml)
    ns = {"smil": root.tag.split("}")[0].strip("{")}

    videos = []
    subtitles_raw = []

    for par in root.findall(".//smil:par", ns):
        ref_elem = par.find(".//smil:ref", ns)
        if ref_elem is not None:
            url = ref_elem.attrib.get("src")
            title = ref_elem.attrib.get("title", "")

            tracking_data = {}
            for param in ref_elem.findall(".//smil:param", ns):
                if param.attrib.get("name") == "trackingData":
                    tracking_value = param.attrib.get("value", "")
                    tracking_data = dict(item.split("=", 1) for item in tracking_value.split("|") if "=" in item)
                    break

            if url and url.endswith(".mpd"):
                videos.append({"url": url, "title": title, "tracking_data": tracking_data})

        for textstream in par.findall(".//smil:textstream", ns):
            sub_url = textstream.attrib.get("src")
            lang = textstream.attrib.get("lang", "unknown")
            sub_type = textstream.attrib.get("type", "unknown")
            sub_format = "srt" if sub_type == "text/srt" else "vtt"
            if sub_url:
                subtitles_raw.append({"url": sub_url, "language": lang, "format": sub_format})

    subtitles_by_lang = {}
    for sub in subtitles_raw:
        subtitles_by_lang.setdefault(sub["language"], []).append(sub)

    subtitles = []
    for _lang, subs in subtitles_by_lang.items():
        vtt_subs = [s for s in subs if s["format"] == "vtt"]
        srt_subs = [s for s in subs if s["format"] == "srt"]
        if vtt_subs:
            subtitles.append(vtt_subs[0])
        elif srt_subs:
            subtitles.append(srt_subs[0])

    return {"videos": videos, "subtitles": subtitles}


def _build_asset_types(conf, is_anonymous):
    quals = conf["qualities_anon"] if is_anonymous else conf["qualities_full"]
    parts = []
    for q in quals:
        parts.append(f"{q},browser,widevine,{conf['geo']}")
        parts.append(f"{q},browser,{conf['geo']}")
    return ":".join(parts)


def get_tracking_info(PLAYBACK_JSON):
    """Fetch the SMIL for a mediaSelector and return videos + subtitles."""
    conf = region_conf()
    api = get_client()

    asset_types = _build_asset_types(conf, api.is_anonymous)

    params = {
        "format": PLAYBACK_JSON.get("format", "SMIL"),
        "auth": api.getBearerToken(),
        "formats": PLAYBACK_JSON.get("formats", "MPEG-DASH"),
        "assetTypes": PLAYBACK_JSON.get("assetTypes", asset_types),
        "balance": PLAYBACK_JSON.get("balance", "true"),
        "auto": PLAYBACK_JSON.get("auto", "true"),
        "tracking": PLAYBACK_JSON.get("tracking", "true"),
        "delivery": PLAYBACK_JSON.get("delivery", "Streaming"),
    }
    if "publicUrl" in PLAYBACK_JSON:
        params["publicUrl"] = PLAYBACK_JSON["publicUrl"]

    try:
        with create_client(headers={"user-agent": get_userAgent()}) as client:
            response = client.get(PLAYBACK_JSON["url"], params=params)
            response.raise_for_status()
            return parse_smil_for_media_info(response.text)
    except Exception as e:
        console.print(f"[red]Error fetching tracking info: {e}")
        return None


def generate_license_url(tracking_info):
    """Build the theplatform Widevine license URL + params (shared IT/ES)."""
    api = get_client()
    account_id = tracking_info["tracking_data"].get("aid", "") or api.getAccountId()

    params = {
        "releasePid": tracking_info["tracking_data"].get("pid"),
        "account": f"http://access.auth.theplatform.com/data/Account/{account_id}",
        "schema": "1.0",
    }
    headers = {
        "authorization": f"Basic {api.get_basic_token()}",
        "user-agent": get_userAgent(),
    }
    return "https://widevine.entitlement.theplatform.eu/wv/web/ModularDrm/getRawWidevineLicense", params, headers
