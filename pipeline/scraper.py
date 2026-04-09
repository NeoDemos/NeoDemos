"""
Phase 1: Royalcast / Company Webcast Scraper
=============================================
Extracts meeting metadata, video stream URLs, agenda timestamps,
and WebVTT transcripts from the Company Webcast (Royalcast) platform.

Usage:
    from pipeline.scraper import RoyalcastScraper
    scraper = RoyalcastScraper()
    meta = scraper.fetch_meeting_metadata("gemeenterotterdam_20260107_1")
    vtt  = scraper.fetch_vtt(meta["uuid"])
"""

from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from pipeline.exceptions import (
    WebcastCodeExtractionError,
    MeetingCancelledError,
    MeetingUnavailableError,
    VideoUnavailableError,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://sdk.companywebcast.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/",
}


# ── Data Classes ──────────────────────────────────────────────────────

@dataclass
class AgendaTimestamp:
    """A single agenda item with its start offset in the video."""
    title: str
    start_seconds: float
    end_seconds: Optional[float] = None  # Filled in by post-processing


@dataclass
class SpeakerSegment:
    """A speaker segment extracted from iBabs HTML."""
    name: str
    party: Optional[str]
    role: Optional[str]
    start_seconds: float
    end_seconds: float
    raw_text: str
    agenda_item_title: Optional[str] = None


@dataclass
class VTTSegment:
    """A single VTT subtitle cue."""
    start_seconds: float
    end_seconds: float
    speaker_label: Optional[str]  # e.g. "spk1", "spk2"
    text: str


@dataclass
class MeetingMetadata:
    """Complete metadata for a single webcast."""
    webcast_code: str
    uuid: str
    label: str
    start_time: str
    duration: str
    duration_seconds: float
    mp4_url: Optional[str] = None
    mp3_url: Optional[str] = None
    hls_path: Optional[str] = None
    events_path: Optional[str] = None
    vtt_available: bool = False
    agenda_timestamps: List[AgendaTimestamp] = field(default_factory=list)
    vtt_segments: List[VTTSegment] = field(default_factory=list)
    speaker_segments: List[SpeakerSegment] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Scraper ───────────────────────────────────────────────────────────

class RoyalcastScraper:
    """Scraper for the Company Webcast (Royalcast) platform."""

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)

    # ── Public API ────────────────────────────────────────────────────

    def extract_webcast_code_from_ibabs(self, ibabs_url: str) -> Optional[str]:
        """
        Given an iBabs meeting URL, extract the Royalcast webcast code
        from the embedded iframe.

        Example input:
            https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/4f944c7c-...
        Example output:
            gemeenterotterdam_20230118_3
        """
        logger.info(f"Fetching iBabs page: {ibabs_url}")
        resp = self.session.get(ibabs_url, timeout=30)
        if resp.status_code == 403:
            raise MeetingUnavailableError(f"Access Forbidden (403): {ibabs_url}")
        resp.raise_for_status()

        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        # ── Refined Cancellation Check ────────────────────────────────────
        # we specifically check the main header container (.box-header)
        # to avoid picking up 'VERVALLEN' labels from the sidebar.
        is_cancelled = False
        
        main_header = soup.find(class_="box-header")
        if main_header:
            # Check for 'VERVALLEN', 'geannuleerd', etc. in the main header text
            header_text = main_header.text.lower()
            if any(k in header_text for k in ["vervallen", "geannuleerd", "afgemeld"]):
                is_cancelled = True

        if is_cancelled:
            raise MeetingCancelledError(f"Meeting is cancelled: {ibabs_url}")

        if "niet toegestaan" in html.lower() or "geen toegang" in html.lower():
            raise MeetingUnavailableError(f"Access Restricted: {ibabs_url}")

        # 1. Look for classic iframes
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "")
            if "companywebcast.com" in src or "royalcast" in src:
                match = re.search(r"[?&]id=([^&]+)", src)
                if match:
                    code = match.group(1)
                    logger.info(f"Found Royalcast code: {code}")
                    return code
            elif "connectlive.ibabs.eu" in src:
                # For ConnectLive, the GUID is often the last part of the path
                # https://connectlive.ibabs.eu/Player/Player/b531ae85-6b4c-4d09-a0ad-d7a231363897
                path_parts = src.split("/")
                if len(path_parts) > 0:
                    code = path_parts[-1].split("?")[0]
                    logger.info(f"Found ConnectLive GUID: {code}")
                    return code

        # Fallback 1: search for standard Royalcast ID pattern anywhere in HTML
        match = re.search(r'gemeenterotterdam[/_]\d{8}_\d+', html)
        if match:
            code = match.group(0).replace("/", "_")
            logger.info(f"Found webcast code in HTML: {code}")
            return code

        # Fallback 2: search in script tags for data-id or similar
        for script in soup.find_all("script"):
            text = script.string or ""
            # Some iBabs pages use: id: "..." or webcastId: "..."
            match = re.search(r'id:\s*["\']([^"\']+)["\']', text)
            if match and "gemeenterotterdam" in match.group(1):
                code = match.group(1).replace("/", "_")
                logger.info(f"Found webcast code in JS object: {code}")
                return code

        raise WebcastCodeExtractionError(f"Could not find webcast code on iBabs page: {ibabs_url}")

    def fetch_notulen_pdf_url(self, ibabs_url: str) -> Optional[str]:
        """
        Locate the 'Notulen' or 'Besluitenlijst' PDF URL on the iBabs page.
        """
        logger.info(f"Searching for Notulen PDF on {ibabs_url}")
        resp = self.session.get(ibabs_url, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        
        # Look for links containing 'notulen' or 'besluitenlijst' and ending in .pdf
        # iBabs links often look like /Document/Details/12345 or /Document/View/12345
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a['href'].lower()
            
            if "notulen" in text or "besluitenlijst" in text:
                # If it's a direct PDF link or an iBabs document detail link
                if "/document/" in href:
                    full_url = urljoin(ibabs_url, a['href'])
                    logger.info(f"Found potential Notulen document: {full_url}")
                    return full_url
        
        logger.warning("No Notulen PDF found on iBabs page")
        return None

    def fetch_meeting_metadata(self, webcast_code: str) -> MeetingMetadata:
        """
        Fetch full meeting metadata from the Royalcast /info endpoint.

        Args:
            webcast_code: e.g. "gemeenterotterdam_20260107_1" 
                          or "gemeenterotterdam/21190116_2"
        """
        # Normalize code format: The API usually expects underscores for the ID part
        # but the path might vary. We try both common patterns.
        player_code = webcast_code.replace("/", "_")
        api_code = webcast_code.replace("_", "/", 1) if "/" not in webcast_code else webcast_code

        # Try multiple common info endpoints
        urls_to_try = [
            f"{BASE_URL}/players/{player_code}/info",
            f"{BASE_URL}/players/{api_code}/info",
            f"{BASE_URL}/sdk/player/{api_code}/info"
        ]

        data = None
        last_err = None
        for url in urls_to_try:
            try:
                logger.info(f"Fetching metadata from: {url}")
                # Ensure Referer is the iBabs site for these requests
                resp = self.session.get(url, timeout=30, headers={"Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"})
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"✅ Metadata fetch successful from: {url}")
                break
            except Exception as e:
                last_err = e
                continue

        if not data:
            if "-" in webcast_code and len(webcast_code) > 20: # Likely a GUID (ConnectLive)
                logger.warning(f"Metadata fetch failed for {webcast_code} (likely ConnectLive). Returning skeleton metadata.")
                return MeetingMetadata(
                    webcast_code=webcast_code,
                    uuid=webcast_code,
                    label="ConnectLive Meeting",
                    start_time="",
                    duration="00:00:00",
                    duration_seconds=0.0,
                    vtt_available=False
                )
            raise VideoUnavailableError(f"Failed to fetch metadata for {webcast_code}: {last_err}")

        uuid = data.get("id", "unknown")
        duration_str = data.get("duration", "00:00:00")
        duration_secs = self._parse_duration(duration_str)

        # Extract Media URLs
        mp4_url = None
        mp3_url = None
        # Older SDKs store downloads in a 'downloads' array
        for dl in data.get("downloads", []):
            ext = dl.get("extension", "").lower()
            if ext == "mp4":
                raw = dl.get("url", "")
                mp4_url = raw if raw.startswith("http") else f"{BASE_URL}{raw}"
            elif ext == "mp3":
                raw = dl.get("url", "")
                mp3_url = raw if raw.startswith("http") else f"{BASE_URL}{raw}"

        # Extract HLS and events paths
        # Usually from source: "src": "/players/UUID/stream/hls"
        raw_hls_src = None
        for source in data.get("sources", []):
            if source.get("streamType") == "hls":
                raw_hls_src = source.get("src")
                events_path = source.get("events")
                if events_path and not events_path.startswith("http"):
                    events_path = urljoin(BASE_URL, events_path)
                    metadata_events_path = events_path # save for later
                break

        # Archive Recovery Strategy: Get Signed URL components for HLS
        is_legacy = data.get("videoSettings", {}).get("videoPipeline") == "legacy"
        webcast_id = webcast_code.split('/')[-1] if '/' in webcast_code else webcast_code.split('_', 1)[-1]
        customer = data.get("customer", {}).get("code", "gemeenterotterdam")

        hls_path = None
        
        try:
            tokens_dict = self.fetch_access_tokens(uuid)
            if tokens_dict:
                from urllib.parse import urlencode
                players_tokens = tokens_dict.get("players")
                playlist_tokens = tokens_dict.get("playlist")
                
                if raw_hls_src and players_tokens and playlist_tokens:
                    logger.info("🎬 Attempting dynamic HLS playlist resolution...")
                    info_url = urljoin(BASE_URL, raw_hls_src)
                    info_url += f"?{urlencode(players_tokens)}"
                    r = self.session.get(info_url, timeout=15)
                    r.raise_for_status()
                    inner = r.json()
                    if isinstance(inner, list): 
                        inner = inner[0]
                    resolved_src = inner.get("src")
                    if resolved_src:
                        hls_path = urljoin(BASE_URL, resolved_src) + f"?{urlencode(playlist_tokens)}"
                        logger.info(f"✅ Dynamic HLS URL successfully resolved for {uuid}")
                        
                if not hls_path and is_legacy:
                    logger.info("🎬 Dynamic resolution failed/unavailable. Using VODS3 fallback...")
                    vods3_path = f"https://sdk.companywebcast.com/vods3/_definst_/mp4:amazons3/clientdataprivate-eu-bv/{customer}/webcasts/{webcast_id}/mp4/bb_nl.mp4/playlist.m3u8"
                    fallback_tokens = playlist_tokens or players_tokens or tokens_dict[list(tokens_dict.keys())[0]] if tokens_dict else None
                    if fallback_tokens:
                        hls_path = f"{vods3_path}?{urlencode(fallback_tokens)}"
                        logger.info(f"✅ VODS3 HLS URL constructed for {uuid}")
                        
        except Exception as e:
            logger.warning(f"⚠️  Could not fetch/resolve signed HLS URL for {uuid}: {e}")

        # Check VTT availability
        vtt_available = self._check_vtt_available(uuid)

        meta = MeetingMetadata(
            webcast_code=player_code,
            uuid=uuid,
            label=data.get("label", ""),
            start_time=data.get("start", ""),
            duration=duration_str,
            duration_seconds=duration_secs,
            mp4_url=mp4_url,
            mp3_url=mp3_url,
            hls_path=hls_path,
            events_path=events_path,
            vtt_available=vtt_available,
        )

        # Apply year fix to the label and metadata if it contains the legacy typo
        if "2118" in meta.webcast_code or "2118" in meta.label:
            logger.info(f"Adjusting metadata year from 2118 to 2018 for {uuid}")
            meta.webcast_code = meta.webcast_code.replace("2118", "2018")
            meta.label = meta.label.replace("2118", "2018")

        # ConnectLive Fallback: check if VTT is available via the direct path
        if not vtt_available and "-" in uuid and len(uuid) > 30:
            logger.info("Checking for ConnectLive VTT transcript...")
            vtt_url = f"https://connectlive.ibabs.eu/Player/File/{uuid}/-subtitle.vtt"
            try:
                vtt_resp = self.session.head(vtt_url, timeout=10)
                if vtt_resp.status_code == 200:
                    meta.vtt_available = True
                    logger.info("✅ ConnectLive VTT found!")
            except:
                pass

        if mp4_url is None:
            logger.warning(f"No MP4 download available for {api_code}")

        logger.info(f"Meeting: {meta.label} | UUID: {uuid} | Duration: {duration_str} | VTT: {vtt_available}")
        return meta

    def fetch_vtt(self, uuid: str, lang: str = "nl") -> List[VTTSegment]:
        """
        Fetch and parse the WebVTT transcript for a meeting.
        """
        if "-" in uuid and len(uuid) > 30: # ConnectLive GUID
            return self.fetch_connectlive_vtt(uuid)

        url = f"{BASE_URL}/players/{uuid}/vtt/public/{lang}"
        logger.info(f"Fetching VTT: {url}")

        resp = self.session.get(url, timeout=60)
        resp.raise_for_status()

        segments = self._parse_vtt(resp.text)
        logger.info(f"Parsed {len(segments)} VTT segments")
        return segments

    def fetch_connectlive_vtt(self, guid: str) -> List[VTTSegment]:
        """
        Fetch and parse the WebVTT transcript for a ConnectLive meeting.
        Path: https://connectlive.ibabs.eu/Player/File/{guid}/-subtitle.vtt
        """
        url = f"https://connectlive.ibabs.eu/Player/File/{guid}/-subtitle.vtt"
        logger.info(f"Fetching ConnectLive VTT: {url}")
        
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200 and "WEBVTT" in resp.text:
                segments = self._parse_vtt(resp.text)
                logger.info(f"Parsed {len(segments)} ConnectLive VTT segments")
                return segments
            else:
                logger.warning(f"ConnectLive VTT not found at {url} (Status: {resp.status_code})")
        except Exception as e:
            logger.error(f"Error fetching ConnectLive VTT: {e}")
            
        return []

    def fetch_agenda_timestamps(self, metadata: MeetingMetadata) -> List[AgendaTimestamp]:
        """
        Attempt to fetch agenda item timestamps from the events API.
        Falls back to VTT-based detection if the events API returns nothing.
        """
        timestamps = []

        # Try events API first
        if metadata.events_path:
            try:
                url = metadata.events_path if metadata.events_path.startswith("http") else f"{BASE_URL}{metadata.events_path}"
                logger.info(f"Fetching events: {url}")
                resp = self.session.get(url, timeout=30)
                if resp.ok and resp.text.strip():
                    events = resp.json()
                    if isinstance(events, list):
                        for evt in events:
                            title = evt.get("title") or evt.get("name", "Unknown")
                            offset = evt.get("offset", 0)
                            # offset may be in milliseconds or seconds
                            secs = offset / 1000.0 if offset > 86400 else float(offset)
                            timestamps.append(AgendaTimestamp(title=title, start_seconds=secs))
            except Exception as e:
                logger.warning(f"Events API failed: {e}")

        if not timestamps:
            logger.info("No events from API; timestamps will be inferred from VTT or skipped")

        # Fill in end_seconds from the next item's start
        for i, ts in enumerate(timestamps):
            if i + 1 < len(timestamps):
                ts.end_seconds = timestamps[i + 1].start_seconds
            else:
                ts.end_seconds = metadata.duration_seconds

        return timestamps
    def scrape_ibabs_speakers(self, ibabs_url: str) -> List[SpeakerSegment]:
        """
        Scrape speaker segments directly from the iBabs meeting page.
        """
        logger.info(f"Scraping speakers from iBabs: {ibabs_url}")
        try:
            resp = self.session.get(ibabs_url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch iBabs page for speakers: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        segments = []

        # iBabs stores speakers in divs with class "offset" inside a "speakers" container
        # These are often inside a collapse panel that corresponds to an agenda item.
        speaker_divs = soup.find_all("div", class_="offset")
        
        for div in speaker_divs:
            if not div.has_attr("data-off"):
                continue
                
            raw_text = div.get_text(strip=True)
            # Example: "00:37:44 - 00:41:08 - C.J. (Carola) Schouten Voorzitter"
            
            # Use regex to split timestamps from text
            match = re.search(r'(\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2})\s*-\s*(.*)', raw_text)
            if not match:
                continue
                
            start_str, end_str, speaker_info = match.groups()
            
            try:
                ms_offset = int(div["data-off"])
                start_sec = ms_offset / 1000.0
            except:
                start_sec = self._vtt_time_to_seconds(start_str)
            
            end_sec = self._vtt_time_to_seconds(end_str)
            
            name, role, party = self._parse_speaker_info(speaker_info)
            
            # Try to find the agenda item title this speaker belongs to
            agenda_item_title = None
            agenda_item = div.find_parent("div", class_="agenda-item")
            if agenda_item:
                title_elem = agenda_item.find("span", class_="panel-title-label")
                if title_elem:
                    agenda_item_title = title_elem.get_text(strip=True)
            
            # Fallback for some layouts
            if not agenda_item_title:
                parent_panel = div.find_parent("div", class_="panel")
                if parent_panel:
                    title_elem = parent_panel.find("h4")
                    if title_elem:
                        agenda_item_title = title_elem.get_text(strip=True)

            segments.append(SpeakerSegment(
                name=name,
                party=party,
                role=role,
                start_seconds=start_sec,
                end_seconds=end_sec,
                raw_text=speaker_info,
                agenda_item_title=agenda_item_title
            ))

        segments.sort(key=lambda x: x.start_seconds)
        logger.info(f"Extracted {len(segments)} speaker segments from iBabs")
        return segments

    def _parse_speaker_info(self, text: str) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Parse "Name Role Party" into components.
        Example: "P.J.H.D. (Ellen) Verkoelen Raadslid Groep Verkoelen"
        """
        # Very rough heuristic: Role is often "Raadslid", "Voorzitter", "Wethouder", "Commissielid"
        roles = ["Raadslid", "Voorzitter", "Wethouder", "Commissielid", "Burgerraadslid"]
        
        name = text
        role = None
        party = None
        
        for r in roles:
            if r in text:
                parts = text.split(r, 1)
                name = parts[0].strip()
                rem = parts[1].strip()
                role = r
                if rem:
                    party = rem
                break
        
        # If no role found, check for known parties at the end
        if not party:
            # Common Rotterdam parties
            parties = ["Leefbaar Rotterdam", "GroenLinks", "VVD", "D66", "PvdA", "DENK", "Volt", "Partij voor de Dieren", "50PLUS", "ChristenUnie-SGP", "BIJ1", "SP", "Groep Verkoelen", "FVD"]
            for p in parties:
                if text.endswith(p):
                    party = p
                    name = text.replace(p, "").strip()
                    break
                    
        return name, role, party

    # ── Private Helpers ───────────────────────────────────────────────

    def _check_vtt_available(self, uuid: str) -> bool:
        """Check if VTT subtitles exist for this meeting."""
        url = f"{BASE_URL}/players/{uuid}/vtt/"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.ok:
                tracks = resp.json()
                return isinstance(tracks, list) and len(tracks) > 0
        except Exception:
            pass
        return False

    def _parse_vtt(self, vtt_text: str) -> List[VTTSegment]:
        """
        Parse raw WebVTT text into VTTSegment objects.

        Handles the Royalcast format:
            0:05:52.580 --> 0:05:54.140
            <v spk1>Goedemiddag
        """
        segments = []
        lines = vtt_text.strip().split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Look for timestamp line: "H:MM:SS.mmm --> H:MM:SS.mmm"
            match = re.match(
                r'(\d+:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d+:\d{2}:\d{2}\.\d{3})',
                line
            )
            if match:
                start = self._vtt_time_to_seconds(match.group(1))
                end = self._vtt_time_to_seconds(match.group(2))

                # Collect text lines until blank line or next timestamp
                text_lines = []
                speaker = None
                i += 1
                while i < len(lines) and lines[i].strip():
                    text_line = lines[i].strip()

                    # Extract speaker label: <v spk1>text
                    spk_match = re.match(r'<v\s+(\w+)>(.*)', text_line)
                    if spk_match:
                        speaker = spk_match.group(1)
                        text_lines.append(spk_match.group(2))
                    else:
                        text_lines.append(text_line)
                    i += 1

                full_text = " ".join(text_lines).strip()
                if full_text:
                    segments.append(VTTSegment(
                        start_seconds=start,
                        end_seconds=end,
                        speaker_label=speaker,
                        text=full_text,
                    ))
            else:
                i += 1

        return segments

    @staticmethod
    def _vtt_time_to_seconds(time_str: str) -> float:
        """Convert VTT timestamp 'H:MM:SS.mmm' to seconds."""
        parts = time_str.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(time_str)

    @staticmethod
    def _parse_duration(duration_str: str) -> float:
        """Parse duration 'HH:MM:SS' to total seconds."""
        parts = duration_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return 0.0

    def fetch_access_tokens(self, uuid: str) -> Optional[Dict[str, Dict[str, str]]]:
        """
        Fetch signed CloudFront tokens (Signature, Policy, Key-Pair-Id) 
        from the legacy accessrules endpoint and return them mapped by path.
        """
        rules_url = f"{BASE_URL}/accessrules/{uuid}"
        
        # Step 1: Request without token to get the identificationToken
        try:
            resp = self.session.get(rules_url, headers=HEADERS, timeout=15)
            # The server returns 401 but includes the token in the body
            data = resp.json()
            id_token = data.get("identificationToken")
            if not id_token:
                logger.warning(f"No identificationToken in 401 response for {uuid}")
                return None
            
            # Step 2: Request with the token in x-authorization header
            auth_headers = HEADERS.copy()
            auth_headers["x-authorization"] = id_token
            resp = self.session.get(rules_url, headers=auth_headers, timeout=15)
            resp.raise_for_status()
            
            rules_data = resp.json()
            ssl_tokens = rules_data.get("readTokens", {}).get("ssl", {})
            
            def get_actual_tokens(block):
                if not block: return None
                if "Signature" in block:
                    return block
                for v in block.values():
                    if isinstance(v, dict):
                        if "Signature" in v:
                            return v
                        elif "CloudFront-Signature" in v:
                            return {
                                "Signature": v["CloudFront-Signature"],
                                "Policy": v["CloudFront-Policy"],
                                "Key-Pair-Id": v["CloudFront-Key-Pair-Id"]
                            }
                return None

            result = {}
            if "players" in ssl_tokens:
                result["players"] = get_actual_tokens(ssl_tokens["players"])
            if "playlist" in ssl_tokens:
                result["playlist"] = get_actual_tokens(ssl_tokens["playlist"])
                
            if not result:
                for k, v in ssl_tokens.items():
                    toks = get_actual_tokens(v if isinstance(v, dict) else {k:v})
                    if toks:
                        result[k] = toks
            
            logger.info("✅ Tokens successfully extracted from accessrules")
            return result
            
        except Exception as e:
            logger.debug(f"Failed to fetch access tokens for {uuid}: {e}")
        
        return None


# ── CLI for standalone testing ────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python -m pipeline.scraper <webcast_code_or_ibabs_url>")
        print("  Example: python -m pipeline.scraper gemeenterotterdam_20230118_3")
        sys.exit(1)

    arg = sys.argv[1]
    scraper = RoyalcastScraper()

    # If it's a URL, extract the code first
    if arg.startswith("http"):
        code = scraper.extract_webcast_code_from_ibabs(arg)
        if not code:
            print("ERROR: Could not extract webcast code from URL")
            sys.exit(1)
    else:
        code = arg

    # Fetch metadata
    meta = scraper.fetch_meeting_metadata(code)
    print(f"\n{'='*60}")
    print(f"Meeting: {meta.label}")
    print(f"UUID:    {meta.uuid}")
    print(f"Duration: {meta.duration} ({meta.duration_seconds:.0f}s)")
    print(f"MP4:     {meta.mp4_url}")
    print(f"MP3:     {meta.mp3_url}")
    print(f"VTT:     {'Available' if meta.vtt_available else 'Not available'}")

    # Fetch VTT if available
    if meta.vtt_available:
        segments = scraper.fetch_vtt(meta.uuid)
        meta.vtt_segments = segments
        print(f"\nVTT Segments: {len(segments)}")
        # Show first 5 segments
        for seg in segments[:5]:
            print(f"  [{seg.start_seconds:.1f}s - {seg.end_seconds:.1f}s] "
                  f"({seg.speaker_label}): {seg.text[:80]}...")

    # Fetch agenda timestamps
    timestamps = scraper.fetch_agenda_timestamps(meta)
    meta.agenda_timestamps = timestamps
    if timestamps:
        print(f"\nAgenda Items: {len(timestamps)}")
        for ts in timestamps:
            print(f"  [{ts.start_seconds:.0f}s - {ts.end_seconds:.0f}s] {ts.title}")
    else:
        print("\nNo agenda timestamps available from events API")

    print(f"\n{'='*60}")
