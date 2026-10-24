"""One moderation policy for suggestions, background checks, discovery and /help."""

import re
import threading
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langdetect.detector_factory import PROFILES_DIRECTORY, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

from random_telegram_bot.messages import message
from random_telegram_bot.telegram_web import Preview

MAX_AGE_DAYS = 14
MAX_SUBSCRIBERS = 1500
TEXT_POSTS = 10
POLICY_VERSION = 1
RECHECK_SECONDS = 30 * 86400
ALLOWED_LANGUAGES = frozenset({"ru", "en"})
MIN_LANGUAGE_CONFIDENCE = 0.9
# These scripts are rejected before detection, so their profiles need no RAM.
_OTHER_SCRIPTS = frozenset(
    {
        "ar",
        "bn",
        "el",
        "fa",
        "gu",
        "he",
        "hi",
        "ja",
        "kn",
        "ko",
        "ml",
        "mr",
        "ne",
        "pa",
        "ta",
        "te",
        "th",
        "ur",
        "zh-cn",
        "zh-tw",
    }
)
_RUSSIAN_LETTERS = frozenset("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
_factory: DetectorFactory | None = None
_factory_lock = threading.Lock()


class InvalidChannel(Exception):
    """The observation does not establish an eligible public channel."""

    def __init__(self, reason: str = "not_public_channel") -> None:
        self.reason = reason
        super().__init__(reason)


def criteria_text() -> str:
    return message(
        "moderation_criteria",
        max_age_days=MAX_AGE_DAYS,
        max_subscribers=MAX_SUBSCRIBERS,
        sample_posts=TEXT_POSTS,
    )


def language_factory() -> DetectorFactory:
    global _factory
    with _factory_lock:
        if _factory is None:
            factory = DetectorFactory()
            factory.set_seed(0)
            profiles = [
                path.read_text(encoding="utf-8")
                for path in sorted(Path(PROFILES_DIRECTORY).iterdir())
                if path.is_file() and path.name not in _OTHER_SCRIPTS
            ]
            factory.load_json_profile(profiles)
            _factory = factory
        return _factory


def check_text(text: str) -> None:
    # URLs/usernames do not establish the language of a post's actual text.
    text = re.sub(r"https?://\S+|(?:t\.me/|@)[A-Za-z0-9_]+", " ", text)
    letters = [letter for letter in text if letter.isalpha()]
    if not letters:
        raise InvalidChannel("language_unknown")
    unsupported = sum(
        letter.lower() not in _RUSSIAN_LETTERS and "LATIN" not in unicodedata.name(letter, "")
        for letter in letters
    )
    # Names and mathematical symbols do not change a post's language. A substantial
    # unsupported script cannot be classified using our Latin/Cyrillic profiles.
    if unsupported / len(letters) > 1 - MIN_LANGUAGE_CONFIDENCE:
        raise InvalidChannel("unsupported_or_uncertain_language")
    detector = language_factory().create()
    detector.append(text)
    try:
        probabilities = detector.get_probabilities()
    except LangDetectException:
        raise InvalidChannel("language_unknown") from None
    confidence = sum(float(item.prob) for item in probabilities if item.lang in ALLOWED_LANGUAGES)
    if confidence < MIN_LANGUAGE_CONFIDENCE:
        raise InvalidChannel("unsupported_or_uncertain_language")


def check_preview(preview: Preview, checked_at: datetime) -> datetime:
    """Reject missing evidence; exact boundaries are inclusive and evaluated in UTC."""
    if not preview.subscriber_label:
        raise InvalidChannel()
    if preview.subscriber_count is None:
        raise InvalidChannel("subscriber_count_unknown")
    if not 0 <= preview.subscriber_count <= MAX_SUBSCRIBERS:
        raise InvalidChannel("too_many_subscribers")
    checked_at = checked_at.astimezone(UTC)
    if not preview.dates:
        raise InvalidChannel("last_post_unknown")
    latest = datetime.fromtimestamp(max(preview.dates), UTC)
    if latest > checked_at:
        raise InvalidChannel("invalid_post_date")
    if latest < checked_at - timedelta(days=MAX_AGE_DAYS):
        raise InvalidChannel("last_post_too_old")
    # Message IDs order the posts, independently of pinned widgets/HTML order.
    posts = {post.identifier: post.text.strip() for post in preview.posts if post.text.strip()}
    if not posts:
        raise InvalidChannel("post_text_unknown")
    for identifier in sorted(posts, reverse=True)[:TEXT_POSTS]:
        check_text(posts[identifier])
    return latest
