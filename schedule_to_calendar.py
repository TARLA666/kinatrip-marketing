#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_to_calendar.py
将 Skill 第7步输出的 schedule.md 转换为 content_calendar.json 格式并追加
用法：python schedule_to_calendar.py drafts/week-YYYY-MM-DD/schedule.md [--push]
"""

import json
import re
import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

# Windows 终端编码兼容
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    # 用 io 重定向 stdout 确保 utf-8
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

# ---- 路径配置 ----
BASE_DIR = Path(__file__).parent
CALENDAR_FILE = BASE_DIR / "content_calendar.json"

# ---- 平台名称映射（schedule.md → JSON）----
PLATFORM_MAP = {
    "Reddit": "reddit",
    "X": "x-twitter",
    "Instagram": "instagram",
    "Facebook": "facebook",
    "小红书": "xiaohongshu",
}

# ---- 星期映射 ----
WEEKDAY_NAME = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def clean_type(type_str: str) -> str:
    """将 'Story型' → 'Story'，'攻略种草型' → '攻略种草'"""
    return re.sub(r"型$", "", type_str).strip()


def extract_image_ref(image_cell: str) -> str | None:
    """从配图方式列提取描述，如「方式B+（东京街景）」→「东京街景」"""
    # 匹配括号中的描述
    m = re.search(r"（(.+?)）", image_cell)
    if m:
        return m.group(1)
    # 如果没有括号，返回方式本身
    return image_cell.strip() if image_cell.strip() else None


def parse_week_range(text: str):
    """
    从 schedule.md 提取排期周期
    返回: (year, start_month, start_day, end_month, end_day)
    """
    lines = text.split("\n")
    target_line = ""
    for line in lines:
        if "排期周期" in line:
            target_line = line
            break

    if not target_line:
        raise ValueError("无法找到「排期周期」字段")

    # 提取开始日期: 2026年6月8日
    m1 = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", target_line)
    if not m1:
        raise ValueError("无法提取开始日期")

    # 提取结束日期: 6月14日（在 ～ 之后）
    m2 = re.search(r"～\s*(\d{1,2})月(\d{1,2})日", target_line)
    if not m2:
        raise ValueError("无法提取结束日期")

    year = int(m1.group(1))
    start_month = int(m1.group(2))
    start_day = int(m1.group(3))
    end_month = int(m2.group(1))
    end_day = int(m2.group(2))
    return year, start_month, start_day, end_month, end_day


def parse_date_from_cell(date_cell: str, year: int) -> str:
    """
    将 '周一 6/8' 或 '**周一 6/8**' 格式转换为 '2026-06-08'
    """
    date_cell = date_cell.replace("**", "")
    m = re.search(r"(\d{1,2})/(\d{1,2})", date_cell)
    if not m:
        raise ValueError(f"无法解析日期单元格: {date_cell}")
    month, day = int(m.group(1)), int(m.group(2))
    return f"{year}-{month:02d}-{day:02d}"


def est_to_bjt(est_time: str) -> str:
    """将 EST 时间转为 BJT（+12 小时）"""
    try:
        t = datetime.strptime(est_time, "%H:%M")
        t_bjt = t + timedelta(hours=12)
        return t_bjt.strftime("%H:%M")
    except Exception:
        return est_time


def extract_hashtags(text: str) -> str | None:
    """从文本中提取 # 开头的标签"""
    tags = re.findall(r"#[\w\u4e00-\u9fff]+", text)
    seen = set()
    unique_tags = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)
    return " ".join(unique_tags) if unique_tags else None


def get_week_number(date_str: str) -> int:
    """根据日期计算是第几周（从1开始）"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    base = datetime(2026, 6, 1)
    delta = (dt - base).days
    return delta // 7 + 1


def generate_id(date_str: str, platform: str, existing_ids: set) -> str:
    """生成唯一 ID"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = WEEKDAY_NAME[dt.weekday()]
    week_num = get_week_number(date_str)

    if platform == "x-twitter":
        base = f"w{week_num}-{weekday}-x-twitter"
    elif platform == "xiaohongshu":
        base = f"w{week_num}-{weekday}-xiaohongshu"
    else:
        base = f"w{week_num}-{weekday}-{platform}"

    if base not in existing_ids:
        return base

    counter = 2
    while f"{base}-{counter}" in existing_ids:
        counter += 1
    return f"{base}-{counter}"


def parse_schedule_md(schedule_path: Path) -> dict:
    """解析 schedule.md，返回 {week_start, week_end, theme, posts: []}"""
    text = schedule_path.read_text(encoding="utf-8")

    # 1. 提取排期周期
    year, start_month, start_day, end_month, end_day = parse_week_range(text)
    week_start = f"{year}-{start_month:02d}-{start_day:02d}"
    week_end = f"{year}-{end_month:02d}-{end_day:02d}"

    # 2. 提取表格行
    lines = text.split("\n")
    table_rows = []
    in_table = False

    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        # 跳过表头
        if "日期" in line and "平台" in line:
            in_table = True
            continue
        # 跳过分隔线
        if re.match(r"^\|[\s\-|:]+\|$", line):
            continue
        if in_table:
            table_rows.append(line)

    # 3. 解析每一行
    posts = []
    for row in table_rows:
        # 分割单元格，去掉首尾空元素
        cells = [c.strip() for c in row.split("|") if c.strip()]

        if len(cells) < 8:
            continue

        try:
            is_xiaohongshu = (len(cells) == 8)

            if is_xiaohongshu:
                # 8列: 日期 | 时间BJT | 平台 | 类型 | 标题 | 配图 | 内容 | 状态
                date_raw = cells[0]
                time_bjt = cells[1]
                platform_raw = cells[2]
                type_raw = cells[3]
                title = cells[4].strip("\"'")
                image_text = cells[5] if len(cells) > 5 else ""
                content_text = cells[6]
            else:
                # 9列: 日期 | 时间EST | 时间BJT | 平台 | 类型 | 标题 | 配图 | 内容 | 状态
                date_raw = cells[0]
                time_est = cells[1]
                time_bjt_raw = cells[2]
                platform_raw = cells[3]
                type_raw = cells[4]
                title = cells[5].strip("\"'")
                image_text = cells[6] if len(cells) > 6 else ""
                content_text = cells[7]

                if time_bjt_raw == "—":
                    time_bjt = est_to_bjt(time_est)
                else:
                    time_bjt = time_bjt_raw

            platform = PLATFORM_MAP.get(platform_raw, platform_raw.lower())
            post_type = clean_type(type_raw)
            date_str = parse_date_from_cell(date_raw, year)
            hashtags = extract_hashtags(content_text)
            image_ref = extract_image_ref(image_text)

    # 统一 content_file 路径（跨平台兼容）
    # 优先使用相对于 BASE_DIR 的路径；若不在 BASE_DIR 下，则保存为绝对路径
    try:
        content_file = str(schedule_path.relative_to(BASE_DIR)).replace("\\", "/")
    except ValueError:
        # schedule_path 不在 BASE_DIR 下时，保存绝对路径
        content_file = str(schedule_path).replace("\\", "/")
        print(f"  [提示] schedule.md 不在项目目录下，使用绝对路径: {content_file}", file=sys.stderr)
            posts.append({
                "date": date_str,
                "time_bjt": time_bjt,
                "platform": platform,
                "type": post_type,
                "title": title,
                "content_file": content_file,
                "image_ref": image_ref,
                "hashtags": hashtags,
                "status": "待发布"
            })
        except Exception as e:
            print(f"  [跳过] 行解析失败: {cells[:3]}... 错误: {e}", file=sys.stderr)
            continue

    return {
        "week_start": week_start,
        "week_end": week_end,
        "theme": "亚洲旅行续篇 — 多城市深度场景",
        "posts": posts
    }


def update_calendar(schedule_path: Path, dry_run: bool = False):
    """主函数：解析 schedule.md，更新 content_calendar.json"""
    # 以脚本自身目录为锚点解析路径（不依赖 CWD）
    schedule_path = (BASE_DIR / schedule_path).resolve()
    if not schedule_path.exists():
        print(f"[错误] 文件不存在: {schedule_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[读取] 排期表: {schedule_path}")
    week_data = parse_schedule_md(schedule_path)
    print(f"  周期: {week_data['week_start']} ~ {week_data['week_end']}")
    print(f"  主题: {week_data['theme']}")
    print(f"  共 {len(week_data['posts'])} 条内容")

    # 读取现有 calendar
    if CALENDAR_FILE.exists():
        with open(CALENDAR_FILE, "r", encoding="utf-8") as f:
            calendar = json.load(f)
    else:
        calendar = {"meta": {}, "weeks": []}

    # 确保 meta 存在
    if "meta" not in calendar:
        calendar["meta"] = {
            "description": "Kinatrip 多平台内容发布日历 — 用于 GitHub Actions 定时提醒",
            "timezone": "Asia/Shanghai (BJT/GMT+8)",
            "reminder_lead_hours": [24, 1],
            "smtp_subject_prefix": "[Kinatrip 发布提醒]"
        }

    # 收集已有 ID
    existing_ids = set()
    for week in calendar["weeks"]:
        for post in week["posts"]:
            existing_ids.add(post["id"])

    # 为新增 posts 生成 ID
    new_posts = week_data["posts"]
    for post in new_posts:
        post["id"] = generate_id(post["date"], post["platform"], existing_ids)
        existing_ids.add(post["id"])

    # 检查是否已存在相同周期
    existing_weeks = {w["week_start"] for w in calendar["weeks"]}
    if week_data["week_start"] in existing_weeks:
        print(f"  [警告] 周期 {week_data['week_start']} 已存在，将覆盖更新")
        calendar["weeks"] = [w for w in calendar["weeks"] if w["week_start"] != week_data["week_start"]]

    # 追加新周
    calendar["weeks"].append({
        "week_start": week_data["week_start"],
        "week_end": week_data["week_end"],
        "theme": week_data["theme"],
        "posts": new_posts
    })

    # 按 week_start 排序
    calendar["weeks"].sort(key=lambda w: w["week_start"])

    if dry_run:
        print("[DRY-RUN] 仅预览，不写入文件")
        print(json.dumps(calendar, ensure_ascii=False, indent=2))
        return

    # 写回 JSON
    with open(CALENDAR_FILE, "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False, indent=2)
    print(f"[完成] 已更新 {CALENDAR_FILE}")

    # Git 操作
    rel_path = CALENDAR_FILE.resolve().relative_to(BASE_DIR)
    subprocess.run(["git", "add", str(rel_path)], cwd=str(BASE_DIR), check=False)
    subprocess.run(
        ["git", "commit", "-m",
         f"chore: 自动追加排期 {week_data['week_start']} ~ {week_data['week_end']}"],
        cwd=str(BASE_DIR), check=False
    )
    print("[完成] Git commit 完成")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="将 schedule.md 转换为 content_calendar.json")
    parser.add_argument("schedule_md", help="schedule.md 文件路径")
    parser.add_argument("--push", action="store_true", help="自动 git push")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入文件")
    args = parser.parse_args()

    update_calendar(Path(args.schedule_md), dry_run=args.dry_run)

    if args.push and not args.dry_run:
        print("[推送] 执行 git push...")
        result = subprocess.run(
            ["git", "push"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("[完成] Git push 成功")
        else:
            print(f"[错误] Git push 失败:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
