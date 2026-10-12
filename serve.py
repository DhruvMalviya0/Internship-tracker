"""Start the tracker website locally on http://127.0.0.1:8765 (only reachable from this computer).

    python serve.py           # your tracker (no sign-in needed locally)
    python serve.py --open    # also open it in the browser
    python serve.py --demo    # sample data showing every status (separate database)

The deployed, multi-user version runs the same app with gunicorn: see DEPLOY.md.
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser

from bot import config


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the tracker in your browser")
    ap.add_argument("--demo", action="store_true", help="serve sample data from data/demo.db")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    from waitress import create_server

    from web.app import create_app

    target = None
    if args.demo:
        from bot.demo import build_demo
        target = str(config.DEMO_DB_PATH)
        build_demo(config.DEMO_DB_PATH)
    app = create_app(db_target=target, demo=args.demo)
    try:
        server = create_server(app, host="127.0.0.1", port=args.port, threads=8)
    except OSError as exc:
        print(f"Could not start on port {args.port} ({exc}). Is the tracker already running? Try --port 8766.")
        return 1
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Tracker running at {url}{'  (DEMO DATA)' if args.demo else ''}\nPress Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.run()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
