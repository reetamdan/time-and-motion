"""Run with: python -m backend"""

from .app.main import app

import uvicorn

services = app.state.services
uvicorn.run(app, host=services.config.runtime.host, port=services.config.runtime.port)
