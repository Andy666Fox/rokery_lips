import csv

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.config import PROJECT_ROOT

router = APIRouter(prefix="/credits", tags=["credits"])
DATA_PATH = PROJECT_ROOT / "data" / "credits.csv"
EXPECTED_FIELDS = ["title", "artist"]


class Credit(BaseModel):
    title: str
    artist: str


def _load() -> list[Credit]:
    if not DATA_PATH.exists():
        return []

    with DATA_PATH.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != EXPECTED_FIELDS:
            raise RuntimeError(
                f"credits file must contain exactly these columns: {', '.join(EXPECTED_FIELDS)}"
            )

        credits = []
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise RuntimeError(
                    f"credits file has an invalid column count on line {line_number}"
                )
            title = row["title"].strip()
            artist = row["artist"].strip()
            if not title or not artist:
                raise RuntimeError(f"credits file contains an empty value on line {line_number}")
            credits.append(Credit(title=title, artist=artist))
        return credits


@router.get("", response_model=list[Credit])
def list_credits(response: Response) -> list[Credit]:
    response.headers["Cache-Control"] = "public, max-age=300"
    return _load()
