import re
import sys
import time
import subprocess
from pathlib import Path

BASE_DIR = Path("/home/reza/matlab-web-lab")
LOG_FILE = BASE_DIR / "tunnel.log"
URL_FILE = BASE_DIR / "active_tunnel_url.txt"

cmd = [
    "/usr/local/bin/cloudflared",
    "tunnel",
    "--url", "http://127.0.0.1:8084",
    "--no-autoupdate",
    "--logfile", str(LOG_FILE)
]

print("Starting cloudflared tunnel...")
proc = subprocess.Popen(cmd)

found_url = None
for _ in range(30):
    time.sleep(1)
    if LOG_FILE.exists():
        content = LOG_FILE.read_text(errors="ignore")
        match = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", content)
        if match:
            found_url = match.group(0)
            URL_FILE.write_text(found_url)
            print(f"Active Tunnel URL: {found_url}")
            break

if not found_url:
    print("Warning: Could not detect tunnel URL in 30 seconds.")

proc.wait()
