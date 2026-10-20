"""python -m backend.app [--port 8000]  -> open http://127.0.0.1:8000"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m backend.app")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Deadlock coach prototype running at {url}  (Ctrl+C to stop)")
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
