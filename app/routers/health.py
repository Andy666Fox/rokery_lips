from fastapi import APIRouter, Response
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    status: str


@router.get("/health", response_model=HealthStatus)
def health(response: Response) -> HealthStatus:
    response.headers["Cache-Control"] = "no-store"
    return HealthStatus(status="ok")
