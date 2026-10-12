"""Source-specific formats yield usernames, never trusted channel identities."""

import csv
import io
import json

from random_telegram_bot.discovery.links import (
    extract_tgbox_channels,
    extract_usernames,
    markdown_section,
    public_username,
)


def extract_source(document: str, format: str, section: str | None = None) -> set[str]:
    if format == "links":
        return extract_usernames(
            markdown_section(document, section) if section is not None else document
        )
    if format == "tgbox-list":
        return extract_tgbox_channels(document)
    if format in {"csv-usernames", "csv-graph-channels"}:
        column = "Name" if format == "csv-graph-channels" else "username"
        reader = csv.DictReader(
            io.StringIO(document.lstrip("\ufeff")),
            delimiter="\t" if format == "csv-graph-channels" else ",",
        )
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise ValueError(f"CSV {column} column missing; inspect source format before retrying")
        return {
            name
            for row in reader
            if isinstance(row.get(column), str)
            and (name := public_username("@" + row[column].strip().removeprefix("@"))) is not None
        }
    data = json.loads(document)
    if not isinstance(data, dict):
        raise ValueError("Source JSON must be an object")
    if format == "tgstat":
        response = data.get("response")
        entries = response.get("items") if isinstance(response, dict) else None
        if data.get("status") != "ok" or not isinstance(entries, list):
            raise ValueError("TGStat items missing; export a successful channels/search response")
        names = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid TGStat entry")
            username = entry.get("username")
            if (
                entry.get("peer_type") == "channel"
                and isinstance(username, str)
                and (name := public_username("@" + username.removeprefix("@"))) is not None
            ):
                names.add(name)
        return names
    if format == "one-s":
        entries = data.get("Channels")
        if not isinstance(entries, list):
            raise ValueError("1C Channels list missing; inspect source format before retrying")
        names = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid 1C entry")
            url = entry.get("Url")
            if (
                entry.get("IsChannel") is True
                and isinstance(url, str)
                and (name := public_username(url)) is not None
            ):
                names.add(name)
        return names
    if format == "wikidata":
        results = data.get("results")
        entries = results.get("bindings") if isinstance(results, dict) else None
        if not isinstance(entries, list):
            raise ValueError("Wikidata bindings missing; inspect source format before retrying")
        names = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("username"), dict):
                raise ValueError("Invalid Wikidata entry")
            value = entry["username"].get("value")
            if isinstance(value, str) and (name := public_username("@" + value)) is not None:
                names.add(name)
        return names
    raise ValueError(f"Unsupported source format: {format}")
