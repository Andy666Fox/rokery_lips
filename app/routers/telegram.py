from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.services.telegram import TelegramPost, TelegramService, TelegramUnavailableError

router = APIRouter(prefix="/telegram", tags=["telegram"])


def get_telegram_service(request: Request) -> TelegramService:
    return request.app.state.telegram_service


@router.get("/latest", response_model=TelegramPost)
async def latest_post(
    response: Response,
    service: Annotated[TelegramService, Depends(get_telegram_service)],
) -> TelegramPost:
    response.headers["Cache-Control"] = "no-cache"
    try:
        return await service.latest_post()
    except TelegramUnavailableError as error:
        raise HTTPException(
            status_code=503, detail="telegram unavailable", headers={"Cache-Control": "no-store"}
        ) from error
