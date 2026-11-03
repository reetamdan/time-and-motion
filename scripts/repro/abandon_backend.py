#!/usr/bin/env python3
"""TEST HELPER: start one paced run with the real supervisor, print its identity, then wait to be killed.

Used by validate_live_workflow.py to simulate a crashed backend faithfully: the validator hard-kills
this whole process tree, so its pipes close exactly as in a real crash (an in-process "abandon" keeps a
live, non-draining stdout reader that a real crash would not have).
"""

import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.main import create_app  # noqa: E402

app = create_app(database_path=sys.argv[1])
services = app.state.services
services.repository.migrate()
services.repository.seed_configuration(services.config)
loop = asyncio.new_event_loop()
loop.run_until_complete(services.supervisor.start("paced"))
state = services.supervisor.states()["cam-01"]
print(json.dumps({"run_id": services.supervisor.current_run()[0], "epoch": state.epoch}), flush=True)
while True:  # keep the loop (and its consumer) alive until killed
    loop.run_until_complete(asyncio.sleep(1))
    time.sleep(0)
