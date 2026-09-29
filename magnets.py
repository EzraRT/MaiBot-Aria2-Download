"""Extract BitTorrent magnets without losing tracker/name parameters."""

from __future__ import annotations

import base64
import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

# Chat punctuation and markup terminate a URI; punctuation inside values should be encoded.
MAGNET_PATTERN = re.compile(r"magnet:\?(?:(?!magnet:\?)[^\s<>\"'`\[\]{}（），。；！？、：])+", re.IGNORECASE)
BTIH_PATTERN = re.compile(r"urn:btih:([0-9a-f]{40}|[a-z2-7]{32})\Z", re.IGNORECASE)


@dataclass(frozen=True)
class Magnet:
    uri: str
    info_hash: str


def extract_magnets(texts: Iterable[str]) -> list[Magnet]:
    """Keep the first URI for each hash, treating hex and base32 hashes as equivalent."""
    found: dict[str, Magnet] = {}
    for text in texts:
        for match in MAGNET_PATTERN.finditer(html.unescape(text)):
            uri = match.group().rstrip(".,;!?:)")
            for key, value in parse_qsl(urlsplit(uri).query, keep_blank_values=True):
                if key.lower() != "xt" or not (btih := BTIH_PATTERN.fullmatch(value)):
                    continue
                digest = btih[1]
                if len(digest) == 32:
                    digest = base64.b32decode(digest.upper()).hex()
                digest = digest.lower()
                # aria2 expects the conventional scheme, even when chat text used uppercase.
                found.setdefault(digest, Magnet("magnet:" + uri[7:], digest))
                break
    return list(found.values())
