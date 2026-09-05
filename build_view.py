#!/usr/bin/env python3
"""Embed the editable office scene into the local companion's page shell."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("fragment", nargs="?", type=Path, default=ROOT / "office.html")
args = parser.parse_args()
fragment = args.fragment.read_text()
if "agentOffice" not in fragment:
    parser.error("Office scene is missing its activity interface")
shell = (ROOT / "page-shell.html").read_text()
assert shell.count("<!--AGENT_OFFICE-->") == 1
(ROOT / "index.html").write_text(shell.replace("<!--AGENT_OFFICE-->", fragment))
print(ROOT / "index.html")
