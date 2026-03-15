#!/usr/bin/env python3
"""RentMate server entry point with CLI argument support."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="RentMate server")
    parser.add_argument("--data-dir", default=None, metavar="PATH",
                        help="Path to data directory (default: ./data)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["RENTMATE_DATA_DIR"] = args.data_dir

    import uvicorn
    uvicorn.run("main:app", host=args.host, port=args.port,
                reload=args.reload, log_level=args.log_level)


if __name__ == "__main__":
    main()
