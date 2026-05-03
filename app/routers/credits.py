import csv
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/credits", tags=["credits"])

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "credits.csv"


def _load() -> list[dict[str, str]]:
    if not DATA_PATH.exists():
        return []
    with DATA_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


@router.get("")
async def list_credits() -> list[dict[str, str]]:
    return _load()
