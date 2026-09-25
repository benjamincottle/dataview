"""Container health check: exit non-zero unless /healthz answers."""

import os
import sys
import urllib.request

url = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/healthz"
try:
    with urllib.request.urlopen(url, timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except OSError:
    sys.exit(1)
