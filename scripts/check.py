"""Fast, GPU-free regression checks on Windows/Linux. Use --require-artifacts on a provisioned PC.

Run with the project virtualenv's Python. Node dependencies must already be installed.
Temporary runtime state is isolated from both the live server and checked-in databases.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-artifacts', action='store_true', help='Fail instead of skipping missing LFS assets')
    args = parser.parse_args()
    npm = shutil.which('npm')
    if not npm:
        parser.error('npm is missing from PATH; install Node.js 22+ first')
    with tempfile.TemporaryDirectory(prefix='aegis-check-') as temporary:
        state = Path(temporary)
        env = {key: value for key, value in os.environ.items() if not key.startswith('CCTV_')}
        env.update(CCTV_CONFIG_DIR=str(ROOT / 'configs'), CCTV_DATABASE_PATH=str(state / 'check.db'),
                   CCTV_EVIDENCE_ROOT=str(state / 'evidence'), CCTV_CAPABILITY_CACHE=str(state / 'capability.json'),
                   AEGIS_REQUIRE_LOCAL_ARTIFACTS='1' if args.require_artifacts else '0')
        commands = [
            [sys.executable, '-m', 'pytest', '-q', '-ra', 'tests', 'perception/candidates'],
            [npm, '--prefix', 'frontend', 'test'],
            [npm, '--prefix', 'frontend', 'run', 'build'],
        ]
        for command in commands:
            print('> ' + subprocess.list2cmdline(command), flush=True)
            result = subprocess.run(command, cwd=ROOT, env=env)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
