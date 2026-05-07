import csv
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from elliptic import elliptic_value

router = APIRouter(prefix="/elliptic", tags=["elliptic"])

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "elliptic.csv"
HEADER = ["tstamp", "value"]


def _read_recent(n: int = 5) -> list[dict[str, str]]:
    if not DATA_PATH.exists():
        return []
    with DATA_PATH.open(encoding="utf-8") as f:
        rows = [{k.strip(): v for k, v in row.items()} for row in csv.DictReader(f)]
    return list(reversed(rows[-n:]))


def _append(value: str) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_file = not DATA_PATH.exists() or DATA_PATH.stat().st_size == 0
    with DATA_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(HEADER)
        writer.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), value])


@router.get("")
async def hit() -> JSONResponse:
    value = elliptic_value()
    _append(value)
    return JSONResponse(
        content={"value": value, "recent": _read_recent(5)},
        headers={"Cache-Control": "no-store"},
    )
