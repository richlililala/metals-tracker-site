#!/usr/bin/env python3
"""
贵金属价格追踪器 - 钌（Ruthenium）& 铱（Iridium）

数据源：
  1. Umicore PMM        — 主要来源，官方 CSV 接口，每个交易日更新
  2. Johnson Matthey    — 参考定盘价，matthey.com HTML 抓取
  3. BASF               — BASF 贵金属价格指示，HTML 抓取
  4. 91金属网            — 中国市场钌粉现货报价（CNY/g），hq.91jinshu.com，独立表存储
  5. Metals-API         — 可选备用，需 API Key（免费套餐 100 次/月）

存储：本地 SQLite（metals_prices.db）
  - prices        : 国际定盘价（USD/troy oz）
  - powder_prices : 钌粉现货（CNY/g，来自 91金属网 hq.91jinshu.com）

用法：
  python metals_tracker.py             # 抓取所有来源今日数据
  python metals_tracker.py --history   # 打印历史记录
  python metals_tracker.py --days 60   # 查看最近 60 天历史
  python metals_tracker.py --export    # 导出为 CSV 文件
  python metals_tracker.py --backfill 30  # 从 Umicore 回填最近 30 天

环境变量（可选）：
  METALS_API_KEY   — metals-api.com 的 API Key
  METALS_DB_PATH   — 自定义数据库路径（默认脚本同目录）
"""

import argparse
import csv
import io
import logging
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# ── 配置 ─────────────────────────────────────────────────────────────────────

DB_PATH = Path(os.environ.get("METALS_DB_PATH", Path(__file__).parent / "metals_prices.db"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Umicore 金属代码映射
UMICORE_METALS: Dict[str, str] = {
    "Ruthenium": "RU",
    "Iridium":   "IR",
}

# Metals-API 符号映射
METALS_API_SYMBOLS: Dict[str, str] = {
    "RUTH": "Ruthenium",
    "IRD":  "Iridium",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── 数据库 ────────────────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT    NOT NULL,
            metal      TEXT    NOT NULL,
            price_usd  REAL    NOT NULL,
            unit       TEXT    NOT NULL DEFAULT 'troy_oz',
            source     TEXT    NOT NULL,
            fetched_at TEXT    NOT NULL,
            UNIQUE(date, metal, source)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS powder_prices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT    NOT NULL,
            product     TEXT    NOT NULL,
            price_cny   REAL    NOT NULL,
            unit        TEXT    NOT NULL DEFAULT 'g',
            supplier    TEXT,
            source      TEXT    NOT NULL DEFAULT '91jinshu',
            url         TEXT,
            fetched_at  TEXT    NOT NULL,
            UNIQUE(date, product, supplier, source)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            metal        TEXT    NOT NULL,
            alert_type   TEXT    NOT NULL CHECK(alert_type IN ('price_threshold','news_keyword','anomaly')),
            condition    TEXT    NOT NULL,
            triggered_at TEXT    NOT NULL,
            notified     INTEGER NOT NULL DEFAULT 0,
            note         TEXT
        )
    """)
    conn.commit()
    return conn


def save(conn: sqlite3.Connection, records: List[dict]) -> Tuple[int, int]:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    saved = skipped = 0
    for r in records:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO prices (date, metal, price_usd, unit, source, fetched_at)
                VALUES (:date, :metal, :price_usd, :unit, :source, :now)
                """,
                {**r, "unit": "troy_oz", "now": now},
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                saved += 1
            else:
                skipped += 1
        except Exception as exc:
            log.error("DB 写入失败 %s: %s", r, exc)
    conn.commit()
    return saved, skipped


def save_powder(conn: sqlite3.Connection, records: List[dict]) -> Tuple[int, int]:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    saved = skipped = 0
    for r in records:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO powder_prices
                  (date, product, price_cny, unit, supplier, source, url, fetched_at)
                VALUES (:date, :product, :price_cny, :unit, :supplier, :source, :url, :now)
                """,
                {**r, "now": now},
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                saved += 1
            else:
                skipped += 1
        except Exception as exc:
            log.error("powder_prices 写入失败 %s: %s", r, exc)
    conn.commit()
    return saved, skipped


# ── 数据源 1：Umicore PMM CSV API ─────────────────────────────────────────────

def _parse_umicore_csv(text: str, metal: str, source: str = "umicore") -> List[dict]:
    """
    解析 Umicore 导出的 CSV 格式：
      "Date";"$ / toz"
      "2026-05-01";1,700.00
    使用分号分隔，数字含千位逗号。
    """
    records = []
    reader = csv.reader(io.StringIO(text), delimiter=";", quotechar='"')
    for i, row in enumerate(reader):
        if i == 0 or len(row) < 2:
            continue
        try:
            dt = row[0].strip().strip('"')
            price_str = row[1].strip().strip('"').replace(",", "")
            price = float(price_str)
            if price > 0:
                records.append({
                    "date": dt,
                    "metal": metal,
                    "price_usd": price,
                    "source": source,
                })
        except (ValueError, IndexError):
            continue
    return records


def fetch_umicore_range(metal: str, start: date, end: date) -> List[dict]:
    """从 Umicore CSV API 获取指定日期范围的价格。"""
    code = UMICORE_METALS[metal]
    url = (
        f"https://pmm.umicore.com/api/chart/export/csv/"
        f"{code}/{start.isoformat()}/{end.isoformat()}/AMUS/"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        records = _parse_umicore_csv(r.text, metal)
        return records
    except Exception as exc:
        log.warning("Umicore API 失败 (%s, %s~%s): %s", metal, start, end, exc)
        return []


def fetch_umicore_today() -> List[dict]:
    """获取今日及近期价格（取当月数据，取最新一条）。"""
    today = date.today()
    month_start = today.replace(day=1)
    all_records = []

    for metal in UMICORE_METALS:
        log.info("  Umicore → %s ...", metal)
        records = fetch_umicore_range(metal, month_start, today)
        if records:
            latest = records[-1]
            log.info("    ✓ %s: $%s / troy oz (日期: %s)",
                     metal, f"{latest['price_usd']:,.2f}", latest["date"])
            # 只保存今日（或最近一个交易日）的数据
            all_records.append(latest)
        else:
            log.warning("    ✗ 未获取到 %s 数据", metal)

    return all_records


# ── 数据源 2：Johnson Matthey ────────────────────────────────────────────────

# JM 在 2023 年将 PGM 业务拆分为独立公司，价格页面已迁移至 matthey.com
JM_PRICE_URL = "https://matthey.com/products-and-markets/pgms-and-circularity/pgm-management"

# JM 金属名称映射（页面英文名 → 内部 metal 字段）
_JM_METAL_MAP = {
    "ruthenium": "Ruthenium",
    "iridium":   "Iridium",
}


def fetch_johnson_matthey() -> List[dict]:
    """从 Johnson Matthey 网站抓取 PGM 参考定价（USD/troy oz）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("缺少 beautifulsoup4，请运行: pip install beautifulsoup4 lxml")
        return []

    log.info("  Johnson Matthey → %s", JM_PRICE_URL)
    try:
        r = requests.get(JM_PRICE_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "lxml")
        today = date.today().isoformat()
        records = []

        # JM 页面通常将价格放在 <table> 或含 "price" class 的 div 里
        # 策略 A: 找包含金属名 + 数值的表格行
        for row in soup.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            label = cells[0].lower()
            for key, metal in _JM_METAL_MAP.items():
                if key in label:
                    # 找该行中第一个像价格的数字（去逗号/空格）
                    for cell in cells[1:]:
                        clean = cell.replace(",", "").replace(" ", "")
                        m = re.search(r"\d{3,6}(?:\.\d+)?", clean)
                        if m:
                            price = float(m.group())
                            if 100 < price < 200000:
                                records.append({
                                    "date":      today,
                                    "metal":     metal,
                                    "price_usd": price,
                                    "source":    "johnson_matthey",
                                })
                                log.info("    ✓ JM %s: $%,.0f", metal, price)
                                break

        # 策略 B: 查找 JSON-LD 或 data-* 属性中的价格
        if not records:
            text = r.text
            for key, metal in _JM_METAL_MAP.items():
                pattern = rf'"{key}"[^{{}}]*?"price"\s*:\s*"?([\d,\.]+)"?'
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    price = float(m.group(1).replace(",", ""))
                    if 100 < price < 200000:
                        records.append({
                            "date": today, "metal": metal,
                            "price_usd": price, "source": "johnson_matthey",
                        })
                        log.info("    ✓ JM %s (JSON): $%,.0f", metal, price)

        if not records:
            log.warning("    ✗ JM：未能解析价格（页面结构可能已变更，请检查 %s）", JM_PRICE_URL)
        return records

    except requests.RequestException as exc:
        log.warning("    ✗ JM 请求失败: %s", exc)
        return []


# ── 数据源 3：BASF 贵金属价格指示 ────────────────────────────────────────────

# BASF Catalysts 贵金属业务已剥离为 ECMS，价格页面迁至新域名
BASF_PRICE_URL = "https://eibprices.basf-catalystsmetals.com/mp/"
BASF_PRICE_URL_ALT = "https://apps.catalysts.basf.com/apps/eibprices/mp/"

_BASF_METAL_MAP = {
    "ruthenium": "Ruthenium",
    "iridium":   "Iridium",
}


def fetch_basf() -> List[dict]:
    """从 BASF/ECMS Precious Metal Services 页面抓取钌铱参考价（USD/troy oz）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("缺少 beautifulsoup4")
        return []

    for url in (BASF_PRICE_URL, BASF_PRICE_URL_ALT):
        log.info("  BASF → %s", url)
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            break
        except requests.RequestException as exc:
            log.warning("    ✗ BASF 请求失败 (%s): %s", url, exc)
    else:
        return []

    try:
        soup = BeautifulSoup(r.content, "lxml")
        today = date.today().isoformat()
        records = []

        for row in soup.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            label = cells[0].lower()
            for key, metal in _BASF_METAL_MAP.items():
                if key in label:
                    for cell in cells[1:]:
                        clean = cell.replace(",", "").replace(" ", "")
                        m = re.search(r"\d{3,6}(?:\.\d+)?", clean)
                        if m:
                            price = float(m.group())
                            if 100 < price < 200000:
                                records.append({
                                    "date":      today,
                                    "metal":     metal,
                                    "price_usd": price,
                                    "source":    "basf",
                                })
                                log.info("    ✓ BASF %s: $%,.0f", metal, price)
                                break

        if not records:
            # 备选：在页面文本中用正则找 Ruthenium / Iridium 后跟数字
            text = r.text
            for key, metal in _BASF_METAL_MAP.items():
                pattern = rf'{key}\D{{0,40}}?([\d]{{3,6}}(?:[,\.][\d]{{2,3}})?)'
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    price = float(m.group(1).replace(",", ""))
                    if 100 < price < 200000:
                        records.append({
                            "date": today, "metal": metal,
                            "price_usd": price, "source": "basf",
                        })
                        log.info("    ✓ BASF %s (regex): $%,.0f", metal, price)

        if not records:
            log.warning("    ✗ BASF：未能解析价格（请检查 %s 或 %s）",
                        BASF_PRICE_URL, BASF_PRICE_URL_ALT)
        return records

    except Exception as exc:
        log.warning("    ✗ BASF 解析失败: %s", exc)
        return []


# ── 数据源 4：91金属网 钌粉现货 ───────────────────────────────────────────────

# 91金属网：钌粉价格走势页面（itemid=22 对应钌粉）
JINSHU91_URL = "https://hq.91jinshu.com/price-htm-itemid-22.html"
JINSHU91_HEADERS = {
    **HEADERS,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://hq.91jinshu.com/",
}


def fetch_91jinshu_powder() -> List[dict]:
    """
    从 91金属网 抓取钌粉最新现货价（CNY/g）。
    价格内嵌于页面 ECharts JS 数据中：
      xAxis data: ['2026-05-11', ...]
      series data: ['390.00', ...]  ← 单位 元/克
    页面 URL: https://hq.91jinshu.com/price.php?itemid=22
    """
    log.info("  91金属网 → %s", JINSHU91_URL)
    try:
        r = requests.get(
            "https://hq.91jinshu.com/price.php?itemid=22",
            headers=JINSHU91_HEADERS, timeout=20
        )
        r.raise_for_status()
        r.encoding = "utf-8"
        text = r.text

        # 提取 ECharts xAxis dates 数组
        dates_m = re.search(
            r"data:\s*\[('[\d-]+'(?:,'[\d-]+')*)\]",
            text
        )
        # 提取 ECharts series data 数组（价格）
        prices_m = re.search(
            r"series:\s*\[.*?data:\s*\[('[\d.]+'(?:,'[\d.]+')*)\]",
            text, re.DOTALL
        )

        if not dates_m or not prices_m:
            log.warning("    ✗ 91金属网：未找到 ECharts 数据（页面结构可能已变更）")
            return []

        dates  = [d.strip("'") for d in dates_m.group(1).split(",")]
        prices = [p.strip("'") for p in prices_m.group(1).split(",")]

        records = []
        for dt, p in zip(reversed(dates), reversed(prices)):
            try:
                price_g = float(p)
                if price_g <= 0:
                    continue
                records.append({
                    "date":      dt,
                    "product":   "钌粉",
                    "price_cny": price_g,
                    "unit":      "g",
                    "supplier":  None,
                    "source":    "91jinshu",
                    "url":       JINSHU91_URL,
                })
                log.info("    ✓ 钌粉: ¥%.2f/g (日期: %s)", price_g, dt)
                break  # 只取最新一条
            except ValueError:
                continue

        if not records:
            log.warning("    ✗ 91金属网：解析到的价格均无效")
        return records

    except requests.RequestException as exc:
        log.warning("    ✗ 91金属网 请求失败: %s", exc)
        return []


# ── 数据源 5：Metals-API（可选）────────────────────────────────────────────────

def fetch_metals_api(api_key: str) -> List[dict]:
    """
    从 metals-api.com 获取实时价格（需 API Key）。
    免费套餐：100 次/月，每小时更新。
    """
    log.info("  Metals-API → 实时报价 ...")
    today = date.today().isoformat()
    symbols = ",".join(METALS_API_SYMBOLS.keys())
    url = f"https://metals-api.com/api/latest?access_key={api_key}&base=USD&symbols={symbols}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        data = r.json()
        if not data.get("success"):
            err = data.get("error", {})
            log.warning("  Metals-API 错误: %s", err.get("info", str(err)))
            return []

        rates = data.get("rates", {})
        results = []
        for sym, name in METALS_API_SYMBOLS.items():
            if sym not in rates or not rates[sym]:
                continue
            # base=USD 时 rate = troy_oz/USD，反转得 USD/troy_oz
            price = round(1.0 / rates[sym], 2)
            log.info("    ✓ %s: $%s / troy oz", name, f"{price:,.2f}")
            results.append({
                "metal": name,
                "price_usd": price,
                "source": "metals-api",
                "date": today,
            })
        return results
    except Exception as exc:
        log.warning("  Metals-API 失败: %s", exc)
        return []


# ── 回填历史数据 ───────────────────────────────────────────────────────────────

def backfill(conn: sqlite3.Connection, days: int):
    """从 Umicore 回填最近 N 天的历史数据。"""
    today = date.today()
    start = today - timedelta(days=days)
    log.info("回填历史数据：%s → %s", start, today)
    all_records = []

    for metal in UMICORE_METALS:
        log.info("  获取 %s 历史数据 ...", metal)
        records = fetch_umicore_range(metal, start, today)
        log.info("    获取 %d 条", len(records))
        all_records.extend(records)

    saved, skipped = save(conn, all_records)
    log.info("回填完成：新增 %d 条，跳过重复 %d 条", saved, skipped)


# ── 输出 ──────────────────────────────────────────────────────────────────────

def print_latest(conn: sqlite3.Connection):
    rows = conn.execute("""
        SELECT metal, price_usd, source, date
        FROM prices
        WHERE (metal, date) IN (
            SELECT metal, MAX(date) FROM prices GROUP BY metal
        )
        ORDER BY metal
    """).fetchall()

    if not rows:
        print("数据库为空，尚无记录。")
        return

    print()
    print("┌──────────────────────────────────────────────────────────┐")
    print("│            贵金属最新价格（USD / troy oz）               │")
    print("├──────────────┬──────────────┬──────────────┬─────────────┤")
    print("│ 金属         │   价格 (USD) │ 来源         │ 日期        │")
    print("├──────────────┼──────────────┼──────────────┼─────────────┤")
    for metal, price, source, dt in rows:
        print(f"│ {metal:<12} │ ${price:>11,.2f} │ {source:<12} │ {dt} │")
    print("└──────────────┴──────────────┴──────────────┴─────────────┘")
    print()


def print_history(conn: sqlite3.Connection, days: int = 30):
    rows = conn.execute("""
        SELECT date, metal, price_usd, source
        FROM prices
        WHERE date >= date('now', ?)
        ORDER BY date DESC, metal
    """, (f"-{days} days",)).fetchall()

    if not rows:
        print(f"最近 {days} 天无记录（可用 --backfill {days} 回填历史数据）。")
        return

    print(f"\n最近 {days} 天历史数据（共 {len(rows)} 条）：\n")
    prev_date = None
    for dt, metal, price, source in rows:
        if dt != prev_date:
            if prev_date is not None:
                print()
            print(f"  {dt}")
            prev_date = dt
        print(f"    {metal:<12}  ${price:>10,.2f}  [{source}]")
    print()


def export_csv_file(conn: sqlite3.Connection, output_path: str):
    rows = conn.execute(
        "SELECT date, metal, price_usd, unit, source, fetched_at "
        "FROM prices ORDER BY date DESC, metal"
    ).fetchall()
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "metal", "price_usd", "unit", "source", "fetched_at"])
        writer.writerows(rows)
    log.info("已导出 %d 条记录 → %s", len(rows), output_path)


# ── 主程序 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="贵金属价格追踪器（钌 Ruthenium & 铱 Iridium）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--history", action="store_true", help="显示历史记录")
    parser.add_argument("--days", type=int, default=30, help="历史天数（默认 30）")
    parser.add_argument("--export", action="store_true", help="导出全部数据为 CSV")
    parser.add_argument("--out", default="metals_prices_export.csv", help="CSV 输出路径")
    parser.add_argument("--backfill", type=int, metavar="DAYS",
                        help="从 Umicore 回填最近 N 天历史数据")
    args = parser.parse_args()

    conn = init_db()
    log.info("数据库：%s", DB_PATH)

    if args.history:
        print_history(conn, args.days)
        conn.close()
        return

    if args.export:
        export_csv_file(conn, args.out)
        conn.close()
        return

    if args.backfill:
        backfill(conn, args.backfill)
        print_history(conn, min(args.backfill, 30))
        conn.close()
        return

    # ── 日常抓取 ──────────────────────────────────────────────────────────────
    all_records: List[dict] = []

    log.info("【来源 1】Umicore PMM（官方 CSV 接口）")
    all_records.extend(fetch_umicore_today())

    log.info("【来源 2】Johnson Matthey")
    all_records.extend(fetch_johnson_matthey())

    log.info("【来源 3】BASF Precious Metal Services")
    all_records.extend(fetch_basf())

    api_key = os.environ.get("METALS_API_KEY")
    if api_key:
        log.info("【来源 5】Metals-API")
        all_records.extend(fetch_metals_api(api_key))
    else:
        log.info("（提示：设置 METALS_API_KEY 环境变量可启用 Metals-API 备用源）")

    if not all_records:
        log.error("Umicore/JM/BASF 均未获取到数据，请检查网络。")
        sys.exit(1)

    saved, skipped = save(conn, all_records)
    log.info("prices 入库：新增 %d 条，跳过重复 %d 条", saved, skipped)

    log.info("【来源 4】91金属网 钌粉现货")
    powder_records = fetch_91jinshu_powder()
    if powder_records:
        ps, pk = save_powder(conn, powder_records)
        log.info("powder_prices 入库：新增 %d 条，跳过重复 %d 条", ps, pk)

    print_latest(conn)
    conn.close()


if __name__ == "__main__":
    main()
