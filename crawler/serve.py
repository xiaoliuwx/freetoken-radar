#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeToken Radar · 本地实时服务
--------------------------------
一个进程同时做两件事：
  1) 在后台按固定间隔跑抓虫，刷新 data/offers.json
  2) 用 HTTP 服务把站点跑起来（页面通过 fetch 轮询实现「实时更新」）

用法：
    python crawler/serve.py                # http://127.0.0.1:8080
    python crawler/serve.py --port 9000 --interval 300
"""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_offers as crawler  # noqa: E402

ROOT = crawler.ROOT
CST = timezone(timedelta(hours=8))


class Handler(SimpleHTTPRequestHandler):
    """静态文件服务 + 禁用缓存（保证实时数据立即生效）。"""

    def end_headers(self) -> None:
        if self.path.split("?")[0].endswith((".json", ".js")):
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # 精简日志
        if "404" in (fmt % args):
            print(f"[404] {self.path}", flush=True)


def crawl_loop(interval: int, offline: bool) -> None:
    last = None
    while True:
        try:
            data = crawler.build(offline=offline)
            digest = crawler.write_outputs(data)
            stamp = datetime.now(CST).strftime("%H:%M:%S")
            print(
                f"[crawl {stamp}] {data['meta']['stats']['total']} 平台 / "
                f"{len(data['news'])} 资讯 / hash {digest}"
                + ("" if digest != last else " (无变化)"),
                flush=True,
            )
            last = digest
        except Exception as exc:  # noqa: BLE001
            print(f"[crawl error] {exc}", flush=True)
        time.sleep(max(30, interval))


def main() -> None:
    ap = argparse.ArgumentParser(description="FreeToken Radar 本地服务")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--interval", type=int, default=600, help="抓取间隔秒数")
    ap.add_argument("--offline", action="store_true", help="不联网，仅用基线数据")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    threading.Thread(
        target=crawl_loop, args=(args.interval, args.offline), daemon=True
    ).start()

    handler = partial(Handler, directory=str(ROOT))
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/index.html"
    print("=" * 56)
    print("  FreeToken Radar 已启动")
    print(f"  站点地址 : {url}")
    print(f"  抓取间隔 : {args.interval}s（后台自动刷新 data/offers.json）")
    print("  退出     : Ctrl + C")
    print("=" * 56)
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[bye] 已停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
