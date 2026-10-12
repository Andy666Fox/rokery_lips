"""Extract public usernames from external source documents."""

import re
from html import unescape

from random_telegram_bot.catalogue import normalize_channel
from random_telegram_bot.telegram_web import public_username

LINK = re.compile(r"(?<![\w./@-])(?:https?://)?(?:t\.me|telegram\.me)/[^\s<>\"')\]]+", re.I)
MENTION = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})(?![\w@])")
TGBOX_CHANNEL = re.compile(r"https://tgbox\.cc/en/detail/([A-Za-z0-9_]+)/", re.I)


def extract_usernames(document: str, *, mentions: bool = False) -> set[str]:
    """Bare @mentions are opt-in because documents also contain email/contact names."""
    result = {
        name
        for match in LINK.finditer(unescape(document))
        if (name := public_username(match[0])) is not None
    }
    if mentions:
        result.update(match[1].lower() for match in MENTION.finditer(document))
    return result


def markdown_section(document: str, name: str) -> str:
    section = re.search(
        r"^## " + re.escape(name) + r"\s*$\n(.*?)(?=^## |\Z)", document, re.M | re.S
    )
    if section is None:
        raise ValueError(f"Markdown section {name} missing; inspect source format before retrying")
    text = section.group(1)
    assert isinstance(text, str)
    return text


def extract_tgbox_channels(document: str) -> set[str]:
    """The licensed Markdown list labels channels, groups and bots separately."""
    section = markdown_section(document, "Channels")
    return {normalize_channel(match[1]) for match in TGBOX_CHANNEL.finditer(section)}
