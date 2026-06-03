#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kinatrip 周排期自动生成脚本
功能：根据模板自动生成一周排期 → 更新 content_calendar.json → git push
用法：python weekly_gen.py <周起始日期> <主题> [--push]
示例：python weekly_gen.py 2026-06-15 "亚洲旅行续篇-台北新加坡" --push
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timedelta

CALENDAR_FILE = "content_calendar.json"
TZ_BJT = "Asia/Shanghai"

# ========== 平台配置模板 ==========
PLATFORM_TEMPLATES = {
    "xiaohongshu": {
        "time_bjt": "20:00",
        "types": ["痛点共鸣", "攻略种草", "功能展示", "种草测评", "使用教程"],
        "title_prefix": "出国旅游不会说？",
        "title_suffix": "拍照翻译秒懂",
        "hashtag_base": "#跨境旅游 #出国旅游翻译 #拍照翻译神器 #旅行必备APP #Kinatrip",
    },
    "facebook": {
        "time_bjt": "21:00",
        "types": ["Story", "Guide", "Tip", "Story", "Guide"],
        "title_prefix": "How I navigated",
        "title_suffix": "with zero local language",
        "hashtag_base": "#Kinatrip #TravelTranslation",
    },
    "x-twitter": {
        "time_bjt": "21:00",
        "types": ["Pain Point", "Quick Tip", "Interactive", "Pain Point", "Quick Tip"],
        "title_prefix": "That moment when",
        "title_suffix": "menu in foreign language",
        "hashtag_base": "#Kinatrip #TravelHack",
    },
    "instagram": {
        "time_bjt": "22:00",
        "types": ["Scene Story", "Feature Spotlight", "Scene Story", "Feature Spotlight", "Scene Story"],
        "title_prefix": "Traveling in",
        "title_suffix": "like a local",
        "hashtag_base": "#Kinatrip #TravelTech",
    },
    "reddit": {
        "time_bjt": "19:00",
        "types": ["Story", "Tool Review", "Story", "Tool Review", "Story"],
        "title_prefix": "How I survived",
        "title_suffix": "with zero local language",
        "hashtag_base": None,
    },
}

# 城市映射（用于自动生成标题）
CITY_MAP = {
    "xiaohongshu": {
        "台北": ("台北夜市点餐", "Taipei_night_market_Taiwan"),
        "新加坡": ("新加坡点餐不踩雷", "Singapore_food_Singapore"),
        "吉隆坡": ("吉隆坡问路不用慌", "Kuala_Lumpur_street_Malaysia"),
        "曼谷": ("曼谷夜市点餐", "Bangkok_night_market_Thailand"),
        "东京": ("东京点菜全靠猜", "Tokyo_restaurant_menu_Japan"),
        "首尔": ("首尔地铁问路", "Seoul_subway_station_Korea"),
        "河内": ("河内咖啡馆点单", "Hanoi_coffee_Vietnam"),
        "上海": ("上海小笼包菜单", "Shanghai_xiaolongbao_China"),
        "京都": ("京都公交问路", "Kyoto_bus_Japan"),
        "普吉岛": ("普吉岛海鲜市场", "Phuket_seafood_Thailand"),
    },
    "facebook": {
        "台北": ("Taipei night market", "Taipei_night_market_Taiwan"),
        "新加坡": ("Singapore food tour", "Singapore_food_Singapore"),
        "吉隆坡": ("Kuala Lumpur streets", "Kuala_Lumpur_street_Malaysia"),
    },
    "x-twitter": {
        "台北": ("Taipei night market signs", "Taipei_night_Taiwan"),
        "新加坡": ("Singapore hawker center", "Singapore_hawker_Singapore"),
        "吉隆坡": ("KL street signs", "KL_street_Malaysia"),
    },
    "instagram": {
        "台北": ("Taipei Night Market Vibes", "taipei-night_1"),
        "新加坡": ("Singapore Hawker Hunt", "singapore-hawker_1"),
        "吉隆坡": ("KL Street Art Walk", "kl-street_1"),
    },
    "reddit": {
        "台北": ("Taipei on $20/day", "Taipei"),
        "新加坡": ("Singapore solo trip", "Singapore"),
        "吉隆坡": ("KL on a budget", "KL"),
    },
}


def parse_args():
    """解析命令行参数"""
    if len(sys.argv) < 3:
        print("用法：python weekly_gen.py <起始日期 YYYY-MM-DD> <主题> [--push]")
        print("示例：python weekly_gen.py 2026-06-15 \"亚洲旅行续篇-台北新加坡\" --push")
        sys.exit(1)

    start_date = sys.argv[1]
    theme = sys.argv[2]
    should_push = "--push" in sys.argv

    # 验证日期格式
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        print(f"❌ 日期格式错误：{start_date}，请用 YYYY-MM-DD 格式")
        sys.exit(1)

    return start_date, theme, should_push


def generate_week_schedule(start_date: str, theme: str) -> dict:
    """
    生成一周的排期数据
    根据主题自动提取城市，生成对应内容
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = start + timedelta(days=6)

    # 从主题中提取城市（简单规则：匹配常见城市名）
    cities = []
    city_keywords = ["台北", "新加坡", "吉隆坡", "曼谷", "东京", "首尔", "河内", "上海", "京都", "普吉岛"]
    for city in city_keywords:
        if city in theme:
            cities.append(city)
    if not cities:
        cities = ["目的地"] * 7  # 默认

    # 生成 7 天的 posts
    posts = []
    platforms_order = ["xiaohongshu", "instagram", "facebook", "x-twitter", "reddit"]

    for day_offset in range(7):
        current_date = (start + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        city = cities[day_offset % len(cities)]

        for plat_idx, platform in enumerate(platforms_order):
            template = PLATFORM_TEMPLATES[platform]
            post_type = template["types"][day_offset % len(template["types"])]

            # 从 CITY_MAP 获取城市信息（标题 + 图片引用）
            city_map = CITY_MAP.get(platform, {})
            city_info = city_map.get(city)
            if city_info is None:
                image_ref = None
                # 根据平台生成默认标题
                if platform == "xiaohongshu":
                    title = f"{city}旅行？{template['title_suffix']}"
                    image_ref = f"{city}_1.jpg"
                elif platform in ["facebook", "reddit"]:
                    title = f"{template['title_prefix']} {city}"
                    image_ref = f"{city}_1.jpg"
                elif platform == "x-twitter":
                    title = f"{template['title_prefix']} {city} 😎"
                    image_ref = f"{city}_1.jpg"
                else:  # instagram
                    title = f"{city} Travel ✨"
                    image_ref = f"{city}_1.jpg"
            else:
                title = {
                    "xiaohongshu": f"{city_info[0]}？{template['title_suffix']}",
                    "facebook": f"{template['title_prefix']} {city_info[0]}",
                    "reddit": f"{template['title_prefix']} {city_info[0]}",
                    "x-twitter": f"{template['title_prefix']} {city_info[0]} 😎",
                    "instagram": f"{city_info[0]} ✨",
                }[platform]
                image_ref = f"{city_info[1]}.jpg"

            # 生成 ID
            day_abbr = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][day_offset]
            post_id = f"w{len(cities)}-{day_abbr}-{platform}" if len(cities) > 1 else f"{day_abbr}-{platform}"

            # 生成 hashtags
            hashtags = template["hashtag_base"]
            if hashtags and city != "目的地":
                hashtags += f" #{city}"

            posts.append({
                "id": post_id,
                "date": current_date,
                "time_bjt": template["time_bjt"],
                "platform": platform,
                "type": post_type,
                "title": title,
                "content_file": f"drafts/week-{start_date}/schedule.md",
                "image_ref": image_ref,
                "hashtags": hashtags,
                "status": "待发布"
            })

    return {
        "week_start": start.strftime("%Y-%m-%d"),
        "week_end": end.strftime("%Y-%m-%d"),
        "theme": theme,
        "posts": posts
    }


def update_calendar(calendar: dict, new_week: dict) -> dict:
    """将新的一周追加到日历"""
    if "weeks" not in calendar:
        calendar["weeks"] = []
    calendar["weeks"].append(new_week)
    return calendar


def git_commit_and_push(start_date: str, theme: str):
    """Git commit 并 push"""
    try:
        # git add
        subprocess.run(["git", "add", CALENDAR_FILE], check=True, capture_output=True)
        # git commit
        commit_msg = f"feat: 添加 {start_date} 周排期 — {theme}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True, capture_output=True)
        print(f"✅ Git commit 成功：{commit_msg}")
        # git push
        result = subprocess.run(["git", "push", "origin", "master"], check=True, capture_output=True, text=True)
        print(f"✅ Git push 成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Git 操作失败：{e.stderr.decode() if e.stderr else e}")
        return False


def main():
    start_date, theme, should_push = parse_args()

    print("=" * 50)
    print("  Kinatrip 周排期自动生成")
    print("=" * 50)
    print(f"  起始日期：{start_date}")
    print(f"  主题：{theme}")
    print(f"  自动 Push：{'是' if should_push else '否（加 --push 参数启用）'}")
    print()

    # 1. 读取现有日历
    if not os.path.exists(CALENDAR_FILE):
        print(f"❌ 日历文件不存在：{CALENDAR_FILE}")
        sys.exit(1)

    with open(CALENDAR_FILE, "r", encoding="utf-8") as f:
        calendar = json.load(f)
    print(f"✅ 已读取现有日历（共 {len(calendar.get('weeks', []))} 周）")

    # 2. 检查是否已存在该周
    existing_weeks = [w["week_start"] for w in calendar.get("weeks", [])]
    if start_date in existing_weeks:
        print(f"⚠️  警告：{start_date} 周已存在，将跳过")
        return

    # 3. 生成新周排期
    new_week = generate_week_schedule(start_date, theme)
    print(f"✅ 已生成新周排期：{new_week['week_start']} ~ {new_week['week_end']}")
    print(f"   共 {len(new_week['posts'])} 条内容（5平台 × 7天）")

    # 4. 更新日历
    calendar = update_calendar(calendar, new_week)

    # 5. 写回文件
    with open(CALENDAR_FILE, "w", encoding="utf-8") as f:
        json.dump(calendar, f, ensure_ascii=False, indent=2)
    print(f"✅ 已更新 {CALENDAR_FILE}")

    # 6. Git commit + push（如果需要）
    if should_push:
        print()
        git_commit_and_push(start_date, theme)
    else:
        print()
        print("💡 提示：添加 --push 参数可自动 commit + push")
        print(f"   git add {CALENDAR_FILE}")
        print(f"   git commit -m \"feat: 添加 {start_date} 周排期\"")
        print(f"   git push origin master")

    print()
    print("=" * 50)
    print("  完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
