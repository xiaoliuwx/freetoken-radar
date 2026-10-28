#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FreeToken Radar · GitHub Pages 部署器（走 API，不需要 github.com 主站）

为什么不用 git push：
    本机到 github.com:443 不通（DNS 正常但 TCP 超时），
    而 api.github.com 可用。所以用 Git Data API 直接写提交，
    再调 Pages API 开站，全程不碰 github.com。

用法：
    set GITHUB_TOKEN=ghp_xxx
    python deploy/publish_api.py --repo freetoken-radar

    # 或者临时传参（会出现在进程列表，不推荐）
    python deploy/publish_api.py --token ghp_xxx --repo freetoken-radar

需要的 Token 权限（classic PAT）：repo + workflow
    workflow 是必须的，否则推送 .github/workflows/sync.yml 会被拒。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"

SKIP_DIRS = {".git", "_shots", "__pycache__", ".venv", "venv", "node_modules", ".wrangler"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}


def req(method: str, path: str, token: str, payload: dict | None = None, ok=(200, 201)):
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("X-GitHub-Api-Version", "2022-11-28")
    r.add_header("User-Agent", "FreeToken-Radar-Deployer")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"message": body[:300]}
        return e.code, parsed


def collect_files() -> list[dict]:
    """用 git ls-files 拿受版本控制的文件，读工作区内容。"""
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    entries = []
    for rel in out:
        rel = rel.strip()
        if not rel:
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        if p.name in SKIP_FILES:
            continue
        raw = p.read_bytes()
        if len(raw) > 3_000_000:
            print(f"  [skip] {rel} 超过 3MB")
            continue
        try:
            text = raw.decode("utf-8")
            entries.append({"path": rel, "mode": "100644", "type": "blob", "content": text})
        except UnicodeDecodeError:
            entries.append({
                "path": rel, "mode": "100644", "type": "blob",
                "content": base64.b64encode(raw).decode("ascii"), "encoding": "base64",
            })
    return entries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--repo", default="freetoken-radar")
    ap.add_argument("--owner", default="", help="目标账号，默认用 Token 所属账号")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--no-pages", action="store_true", help="只推代码，不开 Pages")
    args = ap.parse_args()

    token = args.token.strip()
    if not token:
        sys.exit("缺少 Token：先 set GITHUB_TOKEN=ghp_xxx 再运行")

    # 1) 身份
    code, me = req("GET", "/user", token)
    if code != 200:
        sys.exit(f"Token 无效（{code}）：{me.get('message')}")
    login = me["login"]
    owner = args.owner.strip() or login
    if args.owner and login.lower() != args.owner.lower():
        sys.exit(
            f"Token 属于 {login}，但目标是 {args.owner}。\n"
            f"  要么换一个 {args.owner} 的 Token，要么去掉 --owner 用当前账号。"
        )
    print(f"[1/6] 已认证：{login} → 目标 {owner}/{args.repo}")

    # 2) 仓库
    code, repo = req("GET", f"/repos/{owner}/{args.repo}", token)
    if code == 404:
        code, repo = req("POST", "/user/repos", token, {
            "name": args.repo,
            "description": "免费 AI Token 额度工作台：实时抓取 + 液态玻璃界面",
            "homepage": f"https://{owner.lower()}.github.io/{args.repo}/",
            "private": bool(args.private),
            "has_issues": False,
            "has_wiki": False,
            "has_projects": False,
        })
        if code not in (200, 201):
            sys.exit(f"建仓失败（{code}）：{repo.get('message')}")
        print(f"[2/6] 已创建仓库 {owner}/{args.repo}")
    else:
        print(f"[2/6] 仓库已存在 {owner}/{args.repo}")

    full = repo["full_name"]

    # 3) 现有分支头（空仓库要先用 Contents API 造一个提交，否则 tree API 报 409）
    parent_sha = None
    code, ref = req("GET", f"/repos/{full}/git/ref/heads/{args.branch}", token)
    if code == 200:
        parent_sha = ref["object"]["sha"]
    else:
        print("[2.5] 空仓库，先创建首个提交…")
        seed = base64.b64encode(b"# FreeToken Radar\n").decode("ascii")
        code, res = req("PUT", f"/repos/{full}/contents/README.md", token, {
            "message": "chore: init repository",
            "content": seed,
            "branch": args.branch,
        })
        if code not in (200, 201):
            sys.exit(f"初始化空仓库失败（{code}）：{res.get('message')}")
        code, ref = req("GET", f"/repos/{full}/git/ref/heads/{args.branch}", token)
        if code != 200:
            sys.exit("初始化后仍取不到分支，请稍后重试")
        parent_sha = ref["object"]["sha"]

    # 4) 一次性写入全部文件
    files = collect_files()
    print(f"[3/6] 准备 {len(files)} 个文件…")
    code, tree = req("POST", f"/repos/{full}/git/trees", token, {"tree": files})
    if code not in (200, 201):
        msg = tree.get("message", "")
        if "workflow" in msg.lower():
            sys.exit("Token 缺少 workflow 权限：请给 PAT 勾上 workflow scope 后重试")
        sys.exit(f"写文件树失败（{code}）：{msg}")

    payload = {
        "message": f"deploy: FreeToken Radar {time.strftime('%Y-%m-%d %H:%M')}",
        "tree": tree["sha"],
    }
    if parent_sha:
        payload["parents"] = [parent_sha]
    code, commit = req("POST", f"/repos/{full}/git/commits", token, payload)
    if code not in (200, 201):
        sys.exit(f"创建提交失败（{code}）：{commit.get('message')}")
    print(f"[4/6] 提交 {commit['sha'][:7]}（{len(files)} 个文件）")

    if parent_sha:
        code, _ = req("PATCH", f"/repos/{full}/git/refs/heads/{args.branch}", token, {
            "sha": commit["sha"], "force": True,
        })
        if code not in (200, 201):
            sys.exit(f"更新分支失败（{code}）")
    else:
        code, _ = req("POST", f"/repos/{full}/git/refs", token, {
            "ref": f"refs/heads/{args.branch}", "sha": commit["sha"],
        })
        if code not in (200, 201):
            sys.exit(f"创建分支失败（{code}）")
    print(f"[5/6] 分支 {args.branch} → {commit['sha'][:7]}")

    # 6) Pages
    url = f"https://{owner.lower()}.github.io/{args.repo}/"
    if args.no_pages:
        print("[6/6] 跳过 Pages")
        return

    code, pages = req("POST", f"/repos/{full}/pages", token, {
        "source": {"branch": args.branch, "path": "/"},
    })
    if code == 409:  # 已存在，更新来源
        code, pages = req("PUT", f"/repos/{full}/pages", token, {
            "source": {"branch": args.branch, "path": "/"},
        })
    if code in (200, 201, 204):
        print("[6/6] Pages 已开启，等待首次构建…")
    else:
        print(f"[6/6] Pages 开启失败（{code}）：{pages.get('message')}")
        print("      可手动到 Settings → Pages 选择 main / (root)")
        return

    for i in range(30):
        time.sleep(6)
        code, info = req("GET", f"/repos/{full}/pages", token)
        status = (info.get("status") or "").lower()
        if status == "built":
            print(f"[done] 已上线：{info.get('html_url') or url}")
            return
        if status == "errored":
            print("[warn] Pages 构建报错，去 Actions 里看 build_and_deployment 日志")
            return
        print(f"       构建中… {status or 'queued'} ({i + 1}/30)")
    print(f"[done] 已提交，构建仍在进行：{url}")


if __name__ == "__main__":
    main()
