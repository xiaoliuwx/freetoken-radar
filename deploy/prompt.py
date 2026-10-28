#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeToken Radar · 交互式发布入口

用 Python 的 getpass 读 Token，绕开 PowerShell 5.1 把无 BOM 的 UTF-8 当 GBK 解析的坑。
输入不回显，也不落盘。

用法：双击 deploy.bat，或
    python deploy/prompt.py
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "xiaoliuwx"
REPO = "freetoken-radar"


def main() -> int:
    print()
    print("需要一个 GitHub Token（classic PAT，勾选 repo + workflow）")
    print("创建地址： https://github.com/settings/tokens/new")
    print("  Scopes 勾选：repo、workflow（少 workflow 会被拒）")
    print()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        try:
            if sys.stdin.isatty():
                token = getpass.getpass("粘贴 Token（输入不回显，回车确认）: ").strip()
            else:
                # 非交互环境（管道/IDE 终端）走普通读取，避免 getpass 卡死
                print("粘贴 Token: ", end="", flush=True)
                token = input().strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[x] 已取消")
            return 1

    if not token:
        print("[x] 没有输入 Token")
        return 1

    env = dict(os.environ)
    env["GITHUB_TOKEN"] = token
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [
        sys.executable,
        str(ROOT / "deploy" / "publish_api.py"),
        "--owner", OWNER,
        "--repo", REPO,
    ]
    print()
    return subprocess.call(cmd, env=env, cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())
