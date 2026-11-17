#!/usr/bin/env python3
"""Regenerate deploy/setup.sh from monitor.py and deploy/setup_template.sh.

Run this after any change to monitor.py:
    python3 deploy/build_setup.py
"""
import base64
from pathlib import Path

here = Path(__file__).resolve().parent
payload = base64.b64encode((here.parent / "monitor.py").read_bytes()).decode()
lines = "\n".join(payload[i:i + 76] for i in range(0, len(payload), 76))
template = (here / "setup_template.sh").read_text()
(here / "setup.sh").write_text(template.replace("@@PAYLOAD@@", lines))
print("wrote", here / "setup.sh")
