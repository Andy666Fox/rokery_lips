"""Load the editable chat copy once per process; durable replies keep their original text."""

import os
import tomllib
from pathlib import Path
from string import Formatter


def load_messages(path: Path) -> dict[str, str]:
    with path.open("rb") as source:
        data = tomllib.load(source)
    result: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Bot message {key!r} must be a nonempty string")
        for _, field, _, _ in Formatter().parse(value):
            if field is not None and not field.isidentifier():
                raise ValueError(f"Bot message {key!r} contains an unsupported placeholder")
        result[key] = value.strip()
    return result


_templates = load_messages(Path(__file__).with_name("resources") / "messages.toml")
if (override_path := os.environ.get("BOT_MESSAGES_FILE")) is not None:
    overrides = load_messages(Path(override_path))
    for key, value in overrides.items():
        if key not in _templates:
            raise ValueError(f"Unknown bot message {key!r}; check messages.toml")
        allowed = {
            field for _, field, _, _ in Formatter().parse(_templates[key]) if field is not None
        }
        provided = {field for _, field, _, _ in Formatter().parse(value) if field is not None}
        if not provided <= allowed:
            raise ValueError(f"Unknown placeholder in bot message {key!r}; check messages.toml")
    _templates.update(overrides)


def message(key: str, **values: str | int) -> str:
    try:
        return _templates[key].format(**values)
    except (KeyError, ValueError) as error:
        raise ValueError(
            f"Invalid bot message {key!r}; check messages.toml and its placeholders"
        ) from error
