from __future__ import annotations

import argparse

from shengjing_worker_kit import serve

from .backend import AnalysisBackend


def main() -> None:
    parser = argparse.ArgumentParser(prog="shengjing-analysis-worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    serve(AnalysisBackend(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
