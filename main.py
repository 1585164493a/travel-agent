"""travel-agent 入口：python main.py <命令> [参数]"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trval-agent",
        description="旅游 agent：查真实房价与车票 → 本地过滤 → 按总成本排序",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("hotels", help="查酒店（RollingGo）")
    p.add_argument("--dest", required=True, help="目的地，如 扬州")
    p.add_argument("--check-in", required=True, help="入住日期 yyyy-MM-dd")
    p.add_argument("--nights", type=int, default=2, help="住几晚")
    p.add_argument("--max-price", type=float, default=0, help="每晚预算上限（0=不限）")

    return parser


def cmd_hotels(args: argparse.Namespace) -> int:
    print(f"TODO: 查 {args.dest} {args.check_in} 起 {args.nights} 晚，预算 {args.max_price or '不限'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):          # Windows 控制台默认 GBK，
        try:                                          # 打印 ¥ 之类的字符会直接崩
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    handlers = {"hotels": cmd_hotels}
    handler = handlers.get(args.command)
    if handler is None:
        print(f"还没实现的命令：{args.command}", file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())