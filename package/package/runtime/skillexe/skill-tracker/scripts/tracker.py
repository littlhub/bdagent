#!/usr/bin/env python3
"""skill-tracker —— 记录技能调用历史与统计。

数据落盘在技能自身目录的 data/history.json，随项目走，
不依赖任何手机端路径。可用 DBAGENT_DATA_DIR 覆盖。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DBAGENT_DATA_DIR") or (SKILL_ROOT / "data"))
HISTORY_FILE = DATA_DIR / "history.json"


def _load():
    if not HISTORY_FILE.exists():
        return {"history": []}
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("history"), list):
            data["history"] = []
        return data
    except (OSError, ValueError):
        return {"history": []}


def _save(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cmd_record(args):
    data = _load()
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "skills": [s.strip() for s in args.skill.split(",") if s.strip()],
        "ok": not args.fail,
        "duration": float(args.duration or 0),
        "tokens": int(args.tokens or 0),
    }
    data["history"].append(entry)
    _save(data)
    print(f"已记录: {', '.join(entry['skills'])} "
          f"({'成功' if entry['ok'] else '失败'}, {entry['duration']:.2f}s)")
    return 0


def cmd_show(args):
    data = _load()
    hist = data["history"]
    if not hist:
        print("暂无记录")
        return 0
    limit = max(1, int(args.limit or 20))
    rows = hist[-limit:]
    print(f"最近 {len(rows)} 条技能调用（共 {len(hist)} 条）")
    print(f"{'时间':<20} {'技能':<28} {'结果':<6} {'耗时':>7}")
    print("-" * 66)
    for e in rows:
        skills = ",".join(e.get("skills") or [])
        mark = "✅" if e.get("ok") else "❌"
        print(f"{e.get('time',''):<20} {skills[:28]:<28} {mark:<6} "
              f"{e.get('duration', 0):>6.2f}s")
    return 0


def cmd_stats(args):
    data = _load()
    hist = data["history"]
    if not hist:
        print("暂无记录")
        return 0

    total = len(hist)
    ok = sum(1 for e in hist if e.get("ok"))
    rate = ok / total * 100

    agg: dict[str, dict] = {}
    for e in hist:
        for s in e.get("skills") or []:
            item = agg.setdefault(s, {"count": 0, "ok": 0, "dur": 0.0, "tok": 0})
            item["count"] += 1
            item["dur"] += float(e.get("duration") or 0)
            item["tok"] += int(e.get("tokens") or 0)
            if e.get("ok"):
                item["ok"] += 1

    print("📊 技能统计")
    print(f"总调用 {total} 次，成功 {ok} 次（{rate:.1f}%）")
    for i, (name, v) in enumerate(
            sorted(agg.items(), key=lambda kv: -kv[1]["count"]), 1):
        avg = v["dur"] / v["count"] if v["count"] else 0
        print(f"  {i:>2}. {name:<24} {v['count']:>4} 次  "
              f"平均 {avg:.2f}s  tokens {v['tok']}")
    return 0


def cmd_clear(args):
    if not HISTORY_FILE.exists():
        print("没有记录可清空")
        return 0
    if not args.yes:
        ans = input("确认清空所有历史记录？(y/N) ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消")
            return 0
    HISTORY_FILE.unlink()
    print("已清空历史记录")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="skill-tracker", description="技能调用追踪")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("record", help="记录一次调用")
    p.add_argument("--skill", required=True, help="技能名，多个用逗号分隔")
    p.add_argument("--ok", action="store_true", default=True)
    p.add_argument("--fail", action="store_true", help="标记为失败")
    p.add_argument("--duration", type=float, default=0)
    p.add_argument("--tokens", type=int, default=0)
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("show", help="显示历史")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("stats", help="显示统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("clear", help="清空历史")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_clear)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # 无子命令时默认显示统计
        return cmd_stats(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
