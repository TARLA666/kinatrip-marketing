#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schedule_to_calendar.py  v2
将 Skill 第7步输出的 schedule.md 转换为 content_calendar.json

v2 改动:
  - 统一 schedule.md 为固定9列格式: 日期 | 时间BJT | 平台 | 类型 | 标题 | 配图 | 内容文件 | 标签 | 状态
  - content_file 指向真实文案路径 (drafts/week-YYYY-MM-DD/final/{平台}/{id}.md)
  - 平台名称统一用英文小写 key
  - EST/BJT 换算改用 datetime 自动检测夏令时

用法: python schedule_to_calendar.py drafts/week-YYYY-MM-DD/schedule.md [--push] [--dry-run]
"""

import json
import re
import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

BASE_DIR = Path(__file__).parent
CALENDAR_FILE = BASE_DIR / "content_calendar.json"

PLATFORM_MAP = {
    "Reddit": "reddit", "reddit": "reddit",
    "X": "x-twitter", "X/Twitter": "x-twitter", "x-twitter": "x-twitter",
    "Instagram": "instagram", "instagram": "instagram",
    "Facebook": "facebook", "facebook": "facebook",
    "小红书": "xiaohongshu", "xiaohongshu": "xiaohongshu",
}

WEEKDAY_NAME = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}

PLATFORM_META = {
    "xiaohongshu": {"emoji": "📕", "color": "#FF2442", "label": "小红书"},
    "facebook":    {"emoji": "📘", "color": "#1877F2", "label": "Facebook"},
    "x-twitter":   {"emoji": "🐦", "color": "#1DA1F2", "label": "X/Twitter"},
    "instagram":   {"emoji": "📸", "color": "#E4405F", "label": "Instagram"},
    "reddit":      {"emoji": "🤖", "color": "#FF4500", "label": "Reddit"},
}


def clean_type(type_str: str) -> str:
    return re.sub(r"型$", "", type_str).strip()


def parse_date_from_cell(date_cell: str, year: int) -> str:
    """
    将日期单元格转为 'YYYY-MM-DD'
    支持格式: '周一 6/8', '**周一 6/8**', '2026-06-02', '6/2'
    """
    date_cell = date_cell.replace("**", "")
    # ISO 格式: "2026-06-02"
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_cell)
    if iso_match:
        return iso_match.group(0)
    # 中文格式: "周一 6/8" 或 "6/2"
    m = re.search(r"(\d{1,2})/(\d{1,2})", date_cell)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        return f"{year}-{month:02d}-{day:02d}"
    # 纯数字: "06/02"
    m2 = re.search(r"(\d{1,2})/(\d{1,2})", date_cell.replace("-", "/"))
    if m2:
        month, day = int(m2.group(1)), int(m2.group(2))
        return f"{year}-{month:02d}-{day:02d}"
    raise ValueError(f"无法解析日期: {date_cell}")


def is_dst_naive(date_str: str) -> bool:
    """
    简单判断美国东部是否处于夏令时 (EDT, UTC-4 vs EST, UTC-5)
    美国夏令时: 3月第二个周日 ~ 11月第一个周日
    这里用简化规则: 3月中旬 ~ 11月初 = EDT
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    month = dt.month
    if 3 < month < 11:
        return True
    if month == 3:
        return dt.day >= 8
    if month == 11:
        return dt.day <= 7
    return False


def est_to_bjt(est_time: str, date_str: str = None) -> str:
    """
    将 EST/EDT 时间转为 BJT (GMT+8)
    EDT (夏令时): UTC-4 → +12 = BJT
    EST (冬令时): UTC-5 → +13 = BJT
    """
    try:
        t = datetime.strptime(est_time.strip(), "%H:%M")
        hours_ahead = 12 if (date_str and is_dst_naive(date_str)) else 13
        t_bjt = t + timedelta(hours=hours_ahead)
        return t_bjt.strftime("%H:%M")
    except Exception:
        return est_time


def extract_image_ref(image_cell: str) -> str | None:
    """从配图列提取描述: 'Bangkok_street.jpg' 或 '无配图'"""
    cell = image_cell.strip()
    if not cell or cell in ("无配图", "无", "—", "-", "N/A"):
        return None
    return cell


def extract_hashtags(text: str) -> str | None:
    tags = re.findall(r"#[\w\u4e00-\u9fff]+", text)
    seen = set()
    unique_tags = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)
    return " ".join(unique_tags) if unique_tags else None


def get_week_number(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    base = datetime(2026, 6, 1)
    return (dt - base).days // 7 + 1


def generate_id(date_str: str, platform: str, existing_ids: set) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = WEEKDAY_NAME[dt.weekday()]
    week_num = get_week_number(date_str)
    base = f"w{week_num}-{weekday}-{platform}"
    if base not in existing_ids:
        return base
    counter = 2
    while f"{base}-{counter}" in existing_ids:
        counter += 1
    return f"{base}-{counter}"


def detect_header_columns(header_line: str) -> tuple:
    """
    检测表头列数，返回 (col_count, col_map)
    col_map: 列索引 → 语义名 (date/time_bjt/platform/type/title/image/content_file/hashtags/status)
    """
    cells = [c.strip() for c in header_line.split("|") if c.strip()]
    col_map = {}
    for i, cell in enumerate(cells):
        cell_lower = cell.lower()
        if "日期" in cell or "date" in cell_lower:
            col_map["date"] = i
        elif "时间" in cell and ("bj" in cell_lower or "北京" in cell):
            col_map["time_bjt"] = i
        elif "时间" in cell:
            col_map.setdefault("time_est", i)  # EST列，备用
        elif "平台" in cell or "platform" in cell_lower:
            col_map["platform"] = i
        elif "类型" in cell or "type" in cell_lower:
            col_map["type"] = i
        elif "标题" in cell or "title" in cell_lower:
            col_map["title"] = i
        elif "配图" in cell or "image" in cell_lower:
            col_map["image"] = i
        elif "内容文件" in cell:
            col_map["content_file"] = i
        elif "内容编号" in cell:
            col_map["content_id"] = i
        elif "内容标题" in cell:
            col_map["title"] = i  # 旧格式的"内容标题"列
        elif "编号" in cell or "id" in cell_lower:
            col_map["content_id"] = i
        elif "标签" in cell or "hashtag" in cell_lower:
            col_map["hashtags"] = i
        elif "状态" in cell or "status" in cell_lower:
            col_map["status"] = i
    return len(cells), col_map


def detect_week_info(text: str) -> tuple:
    """从 schedule.md 文本中提取年份、起止日期、主题"""
    year, week_start, week_end, theme = None, None, None, ""

    # 中文日期: "2026年06月02日"
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        year = int(m.group(1))
        week_start = f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m2 = re.search(r"～\s*(\d{1,2})月(\d{1,2})日", text)
        if m2:
            week_end = f"{year}-{int(m2.group(1)):02d}-{int(m2.group(2)):02d}"

    # ISO 日期: "2026-06-02"
    if not year:
        iso_dates = re.findall(r"(\d{4})-(\d{2})-(\d{2})", text)
        if iso_dates:
            year = int(iso_dates[0][0])
            week_start = f"{year}-{iso_dates[0][1]}-{iso_dates[0][2]}"
            if len(iso_dates) >= 2:
                week_end = f"{year}-{iso_dates[1][1]}-{iso_dates[1][2]}"
            else:
                m2 = re.search(r"～\s*(\d{1,2})月(\d{1,2})日", text)
                if m2:
                    week_end = f"{year}-{int(m2.group(1)):02d}-{int(m2.group(2)):02d}"

    # 主题
    for kw in ["主题", "叙事主题"]:
        if kw in text:
            m = re.search(rf"{kw}[：:]?\s*\**\s*(.+?)(?:\n|$)", text)
            if m:
                theme = re.sub(r"\*\*", "", m.group(1)).strip()
                break

    return year, week_start, week_end, theme


def parse_schedule_md(schedule_path: Path) -> dict:
    """
    解析 schedule.md，兼容旧格式(7列)和新格式(9列)
    新格式: 日期 | 时间BJT | 平台 | 类型 | 标题 | 配图 | 内容文件 | 标签 | 状态
    旧格式: 时间BJT | 时间EST | 平台 | 内容编号 | 标题 | 配图 | 状态
    """
    text = schedule_path.read_text(encoding="utf-8")

    year, week_start, week_end, theme = detect_week_info(text)

    lines = text.split("\n")
    table_rows = []
    in_table = False
    header_col_map = {}
    header_col_count = 0

    # 预扫描: 提取旧格式二级标题中的日期映射
    heading_dates = {}
    for line in lines:
        stripped = line.strip()
        m = re.search(r"(周[一二三四五六日])\s+(\d{4}-\d{2}-\d{2})", stripped)
        if m:
            heading_dates[m.group(1)] = m.group(2)

    # 预扫描: 提取旧格式二级标题中的日期映射
    heading_dates = {}
    for line in lines:
        stripped = line.strip()
        m = re.search(r"(周[一二三四五六日])\s+(\d{4}-\d{2}-\d{2})", stripped)
        if m:
            heading_dates[m.group(1)] = m.group(2)

    # 旧格式的二级标题日期是按顺序排列的，用列表保存
    heading_date_list = []
    for line in lines:
        stripped = line.strip()
        m = re.search(r"###\s*周[一二三四五六日]\s+(\d{4}-\d{2}-\d{2})", stripped)
        if m:
            heading_date_list.append(m.group(1))

    # 用状态机提取所有表格行，记录每个行属于哪个日期
    current_heading_date = ""
    rows_with_dates = []  # [(date_str, row_text)]
    in_table = False

    for line in lines:
        stripped = line.strip()

        # 检测二级标题（旧格式: "### 周一 2026-06-02"）
        m = re.search(r"###\s*周[一二三四五六日]\s+(\d{4}-\d{2}-\d{2})", stripped)
        if m:
            current_heading_date = m.group(1)
            in_table = False
            continue

        if not stripped.startswith("|"):
            continue

        # 检测表头行
        has_heading = ("时间" in stripped or "日期" in stripped) and ("平台" in stripped) and ("标题" in stripped or "配图" in stripped)
        if has_heading:
            in_table = True
            header_col_count, header_col_map = detect_header_columns(stripped)
            continue

        if re.match(r"^\|[\s\-:｜]+\|$", stripped):
            continue

        if in_table:
            rows_with_dates.append((current_heading_date, stripped))

    if not year or not week_start:
        raise ValueError(f"无法从 schedule.md 提取排期周期。请确保文件包含日期信息。\n已检测到: year={year}, start={week_start}")

    if not week_end:
        dt = datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)
        week_end = dt.strftime("%Y-%m-%d")

    # 格式判断: 有 "content_file" 或 "内容文件" 列 → 新格式；否则 → 旧格式
    # 但需要额外确认: 旧格式表头行会被第一个遇到（因为它们在文件前面）
    # 如果同时有 content_id 但没有 content_file，说明是旧格式
    is_new_format = ("content_file" in header_col_map) and ("content_id" not in header_col_map)
    if not is_new_format and "content_file" in header_col_map and "content_id" in header_col_map:
        is_new_format = False  # 两个都有 → 可能是先后两个表格的混合 → 保守判断为旧格式

    current_heading_date = ""

    def get_heading_date(row_text: str) -> str:
        m = re.search(r"(周[一二三四五六日])", row_text)
        return m.group(1) if m else ""

    posts = []
    for heading_date, row in rows_with_dates:
        cells = [c.strip() for c in row.split("|") if c.strip()]
        if not cells:
            continue

        # 跳过分隔线和汇总行
        first_cell = cells[0]
        if re.match(r"^[\-\s]+$", first_cell) or "**" in first_cell or "合计" in first_cell:
            continue
        # 跳过自动化配置行 (auto-001 等)
        if first_cell.startswith("auto-"):
            continue

        if is_new_format:
            if len(cells) < 6:
                print(f"  [跳过] 列数不足 ({len(cells)}列): {cells[:3]}...", file=sys.stderr)
                continue
            try:
                date_raw      = cells[header_col_map.get("date", 0)]
                time_bjt      = cells[header_col_map.get("time_bjt", 1)]
                platform_raw  = cells[header_col_map.get("platform", 2)]
                type_raw      = cells[header_col_map.get("type", 3)]
                title         = cells[header_col_map.get("title", 4)].strip("\"'")
                image_text    = cells[header_col_map.get("image", 5)] if "image" in header_col_map else ""
                content_file  = cells[header_col_map.get("content_file", 6)] if "content_file" in header_col_map else ""
                hashtags_raw  = cells[header_col_map.get("hashtags", 7)] if "hashtags" in header_col_map else ""
                status_raw    = cells[header_col_map.get("status", 8)] if "status" in header_col_map else "待发布"
            except (IndexError, KeyError) as e:
                print(f"  [跳过] 列解析失败: {e}", file=sys.stderr)
                continue
        else:
            # 旧格式兼容: 时间BJT | 时间EST | 平台 | 内容编号 | 标题 | 配图 | 状态
            if len(cells) < 5:
                print(f"  [跳过] 列数不足 ({len(cells)}列): {cells[:3]}...", file=sys.stderr)
                continue
            try:
                time_bjt      = cells[0]
                platform_raw  = cells[2] if len(cells) > 2 else ""
                content_id    = cells[3] if len(cells) > 3 else ""
                title         = cells[4].strip("\"'") if len(cells) > 4 else ""
                image_text    = cells[5] if len(cells) > 5 else ""
                status_raw    = cells[6] if len(cells) > 6 else "待发布"
                type_raw      = ""
                hashtags_raw  = ""
                content_file  = ""
            except IndexError as e:
                print(f"  [跳过] 列解析失败: {e}", file=sys.stderr)
                continue

            # 旧格式: 直接用二级标题提取的日期
            date_raw = heading_date or week_start

            # 从标题推断类型
            if "Story" in title or "How I" in title:
                type_raw = "Story"
            elif "Pain" in title:
                type_raw = "Pain Point"
            elif "Scene" in title or "Night" in title or "Morning" in title:
                type_raw = "Scene Story"
            elif "Feature" in title:
                type_raw = "Feature Spotlight"
            elif "Guide" in title:
                type_raw = "Guide"
            elif "Tip" in title:
                type_raw = "Tip"
            else:
                type_raw = "通用"

            # 旧格式: 从内容编号生成 content_file
            if content_id and not content_file:
                content_file = f"drafts/week-{week_start}/final/{PLATFORM_MAP.get(platform_raw, platform_raw)}/{content_id}.md"

        platform = PLATFORM_MAP.get(platform_raw, platform_raw.lower().replace(" ", "-"))
        post_type = clean_type(type_raw) if type_raw else "通用"

        try:
            date_str = parse_date_from_cell(date_raw, year)
        except ValueError:
            print(f"  [跳过] 无法解析日期: {date_raw}", file=sys.stderr)
            continue

        image_ref = extract_image_ref(image_text)
        hashtags = hashtags_raw.strip() if hashtags_raw.strip() and hashtags_raw.strip() != "—" else None
        if not hashtags:
            hashtags = None
        status = status_raw.strip() if status_raw.strip() else "待发布"

        posts.append({
            "date": date_str,
            "time_bjt": time_bjt,
            "platform": platform,
            "type": post_type,
            "title": title,
            "content_file": content_file if content_file else None,
            "image_ref": image_ref,
            "hashtags": hashtags,
            "status": status
        })
    return {
        "week_start": week_start,
        "week_end": week_end,
        "theme": theme or "Kinatrip 营销内容",
        "posts": posts
    }


def update_calendar(schedule_path: Path, dry_run: bool = False):
    schedule_path = (BASE_DIR / schedule_path).resolve()
    if not schedule_path.exists():
        print(f"[错误] 文件不存在: {schedule_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[读取] 排期表: {schedule_path}")
    week_data = parse_schedule_md(schedule_path)
    print(f"  周期: {week_data['week_start']} ~ {week_data['week_end']}")
    print(f"  主题: {week_data['theme']}")
    print(f"  共 {len(week_data['posts'])} 条内容")

    if CALENDAR_FILE.exists():
        with open(CALENDAR_FILE, "r", encoding="utf-8") as f:
            calendar = json.load(f)
    else:
        calendar = {"meta": {}, "weeks": []}

    if "meta" not in calendar or not calendar["meta"]:
        calendar["meta"] = {
            "description": "Kinatrip 多平台内容发布日历 — GitHub Actions 精准提醒",
            "timezone": "Asia/Shanghai (BJT/GMT+8)",
            "reminder_minutes": 30,
            "reminder_window_minutes": 5,
            "smtp_subject_prefix": "⏰ [Kinatrip 发布提醒]"
        }

    existing_ids = set()
    for week in calendar.get("weeks", []):
        for post in week.get("posts", []):
            existing_ids.add(post.get("id", ""))

    new_posts = week_data["posts"]
    for post in new_posts:
        post["id"] = generate_id(post["date"], post["platform"], existing_ids)
        existing_ids.add(post["id"])

    existing_weeks = {w["week_start"] for w in calendar.get("weeks", [])}
    if week_data["week_start"] in existing_weeks:
        print(f"  [覆盖] 周期 {week_data['week_start']} 已存在，将更新")
        calendar["weeks"] = [w for w in calendar["weeks"] if w["week_start"] != week_data["week_start"]]

    calendar["weeks"].append({
        "week_start": week_data["week_start"],
        "week_end": week_data["week_end"],
        "theme": week_data["theme"],
        "posts": new_posts
    })
    calendar["weeks"].sort(key=lambda w: w["week_start"])

    if dry_run:
        print("\n[DRY-RUN] 预览 content_calendar.json:\n")
        print(json.dumps(calendar, ensure_ascii=False, indent=2))
        return

    with open(CALENDAR_FILE, "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False, indent=2)
    print(f"[完成] 已更新 {CALENDAR_FILE}")

    subprocess.run(["git", "add", str(CALENDAR_FILE.name)], cwd=str(BASE_DIR), check=False)
    subprocess.run(
        ["git", "commit", "-m",
         f"chore: 更新排期 {week_data['week_start']} ~ {week_data['week_end']}"],
        cwd=str(BASE_DIR), check=False
    )
    print("[完成] Git commit")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="schedule.md → content_calendar.json (v2)")
    parser.add_argument("schedule_md", help="schedule.md 路径")
    parser.add_argument("--push", action="store_true", help="自动 git push")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")
    args = parser.parse_args()

    update_calendar(Path(args.schedule_md), dry_run=args.dry_run)

    if args.push and not args.dry_run:
        print("[推送] git push...")
        result = subprocess.run(["git", "push"], cwd=str(BASE_DIR), capture_output=True, text=True)
        if result.returncode == 0:
            print("[完成] Git push 成功")
        else:
            print(f"[错误] Git push 失败:\n{result.stderr}", file=sys.stderr)


if __name__ == "__main__":
    main()
