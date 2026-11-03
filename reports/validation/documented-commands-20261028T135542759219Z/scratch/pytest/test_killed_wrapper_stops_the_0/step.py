
import sys, time
from pathlib import Path
marker = Path(sys.argv[1])
marker.write_text("started")
try:
    while True:
        time.sleep(0.2)
finally:
    marker.write_text("cleaned-up")
