from fastapi import Request

from ..services.context import AppServices


def services(request: Request) -> AppServices:
    return request.app.state.services
