#!/usr/bin/env python3
"""
生成贵金属价格仪表盘 HTML 文件，双击即可在浏览器查看。
用法：python3 generate_dashboard.py
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

DB_PATH    = Path(os.environ.get("METALS_DB_PATH", Path(__file__).parent / "metals_prices.db"))
OUTPUT     = Path(__file__).parent / "dashboard.html"
OUTPUT_OFF = Path(__file__).parent / "dashboard_offline.html"

CHARTJS_FILES = [
    Path(__file__).parent / "chart.umd.min.js",
    Path(__file__).parent / "chartjs-adapter-date-fns.bundle.min.js",
]
CHARTJS_CDNS = [
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
    "https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js",
]

# ── 产业链静态数据（定期人工更新）────────────────────────────────────────────

SUPPLY_CHAIN_DATA = {
    # ── 产地与年产量（公吨，近 10 年估算）────────────────────────────────
    "production": {
        "years":   [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024],
        "regions": {
            "南非 South Africa": [28.5, 29.0, 30.2, 30.5, 29.8, 26.0, 29.5, 31.0, 31.8, 32.0],
            "俄罗斯 Russia":      [1.3,  1.3,  1.3,  1.3,  1.3,  1.2,  1.2,  1.3,  1.3,  1.3],
            "其他 Others":        [1.2,  1.2,  1.5,  1.7,  1.9,  1.8,  2.0,  2.2,  2.4,  2.5],
        },
        "note": "数据来源：SFA(Oxford)、Johnson Matthey PGM Market Report 综合估算。南非 2020 年下降因 COVID-19 停工。",
    },
    # ── 供应链各环节主要企业 ────────────────────────────────────────────
    "chain": [
        {
            "stage": "矿山开采",
            "icon": "⛏",
            "color": "#a78bfa",
            "players": [
                {"name": "Anglo American Platinum（隶属 Anglo American）",
                 "detail": "南非最大 PGM 矿商，Rustenburg/Mogalakwena 矿区，钌年产约 10-12 t"},
                {"name": "Impala Platinum（Implats）",
                 "detail": "南非第二大，Impala Lease Area 矿区，钌年产约 7-9 t"},
                {"name": "Sibanye-Stillwater",
                 "detail": "南非/美国，收购 Lonmin 矿区，钌年产约 5-7 t"},
                {"name": "Norilsk Nickel（俄罗斯）",
                 "detail": "作为镍矿副产品提取，年产约 1-1.5 t，制裁风险影响西方买家"},
                {"name": "金川集团（中国甘肃）",
                 "detail": "中国唯一具规模的铂族金属矿，钌产量极少（< 0.5 t/yr），国内基本靠进口"},
            ],
        },
        {
            "stage": "精炼加工",
            "icon": "🏭",
            "color": "#34d399",
            "players": [
                {"name": "Johnson Matthey（英国）",
                 "detail": "全球最大 PGM 精炼/交易商，设日均 \"Base Price\" 作为全球定价基准"},
                {"name": "Heraeus（德国）",
                 "detail": "钌精炼、交易、工业应用一体化，在亚洲市场积极布局"},
                {"name": "Umicore（比利时）",
                 "detail": "欧洲二次料回收钌能力强；官网每日公布钌铱 10am 定盘价（即本追踪器数据源）"},
                {"name": "田中贵金属（Tanaka，日本）",
                 "detail": "亚太最大 PGM 精炼商，主要服务日本电子行业（Murata 等）"},
                {"name": "东曹（Tosoh，日本）",
                 "detail": "专注 RuO₂ 靶材及浆料，是日本半导体/电阻行业主要供应商"},
            ],
        },
        {
            "stage": "终端采购",
            "icon": "🏢",
            "color": "#fb923c",
            "players": [
                {"name": "村田制作所 Murata（日本）",
                 "detail": "全球最大 MLCC/厚膜电阻制造商，最大单一钌消费者，每年耗钌约 5-8 t"},
                {"name": "国巨 Yageo（台湾）",
                 "detail": "全球第二大电阻制造商，厚膜电阻 RuO₂ 浆料消费，约 2-3 t/yr"},
                {"name": "TDK / 三星电机 SEMCO",
                 "detail": "厚膜被动元件，钌耗量各约 1-2 t/yr"},
                {"name": "Seagate / Western Digital / Toshiba",
                 "detail": "HDD 盘片用钌作磁性层间耦合层（Ru underlayer）；每盘 <0.005g，但出货量 3.5 亿片/yr，合计约 1.5-2 t/yr"},
                {"name": "Intel / TSMC / Samsung（晶圆厂）",
                 "detail": "7nm 以下节点用钌取代铜作互连材料（2023 年起量产），钌需求快速增长，预计 2027 年达 2-3 t/yr"},
                {"name": "De Nora（意大利）/ Jiangyin Miracle（中国江阴）",
                 "detail": "DSA 阳极主要制造商，氯碱行业使用 RuO₂/TiO₂ 涂层钛电极，年耗约 3-4 t"},
                {"name": "中石化 / 上海石化 / 中化工 集团旗下炼化",
                 "detail": "氨合成钌催化剂（比传统铁基催化剂效率高 20%）；中国化工用钌约 2-3 t/yr"},
            ],
        },
    ],
    # ── 需求拆解（2024 全球，公吨）──────────────────────────────────────
    "demand_breakdown": [
        {"label": "厚膜电阻 Chip Resistors",  "value": 12.5, "pct": 36,
         "form": "RuO₂ 浆料",
         "users": "Murata、Yageo、TDK、SEMCO、风华高科",
         "sub": "印制在 Al₂O₃ 陶瓷基板上，用于智能手机/工业电路板精密电阻"},
        {"label": "化工催化 Chemical Catalysis", "value": 7.0, "pct": 21,
         "form": "钌金属/钌盐催化剂",
         "users": "中石化、Topsoe（氨合成）、Lanxess（有机合成）",
         "sub": "Ru 基催化剂可在较低温压下合成氨，节能 20%；亦用于油脂加氢、有机氯化"},
        {"label": "电化学工业 Electrochemical",  "value": 5.5, "pct": 16,
         "form": "RuO₂/TiO₂ 涂层（DSA 电极）",
         "users": "De Nora、Magneto、江阴米可、中国科立特",
         "sub": "氯碱工业（氯气+烧碱）阳极；PEM 绿氢电解槽阳极催化剂（替代铱，成本低 10×）"},
        {"label": "硬盘 HDD",                  "value": 2.0, "pct": 6,
         "form": "钌金属（纯靶材）",
         "users": "Seagate、Western Digital、东芝",
         "sub": "磁性层间耦合层（Ru underlayer）增强磁道密度；HAMR 技术或减少此用量"},
        {"label": "半导体互连 Semiconductor",    "value": 1.5, "pct": 4,
         "form": "钌金属（溅射靶材 / ALD 前驱体）",
         "users": "TSMC、Intel、Samsung Foundry",
         "sub": "7nm 以下节点取代铜互连，接触孔（contact fill）填充材料，2023 年开始规模量产"},
        {"label": "耐磨合金/其他 Others",        "value": 2.5, "pct": 7,
         "form": "钌铂/钌铱合金",
         "users": "航空发动机叶片、高温合金、钢笔笔尖、医疗设备",
         "sub": "耐高温、高硬度、抗腐蚀；GE/Rolls-Royce 航空发动机用钌铂高温合金"},
        {"label": "二次回收 Recycling Supply",   "value": -6.5, "pct": -19,
         "form": "（供给端抵减）",
         "users": "Heraeus、Umicore、Tanaka 负责回收",
         "sub": "电阻/HDD 报废回收，约覆盖 15-20% 的年需求；回收量逐年小幅增长"},
    ],
    # ── 替代材料风险评估 ────────────────────────────────────────────────
    "substitutes": [
        {"app": "厚膜电阻", "sub": "MnO₂ / BaTiO₃ 浆料",
         "risk": "低", "note": "性能差距大（精度/温稳系数），高端电阻无法替代"},
        {"app": "HDD 磁性层", "sub": "新磁性材料（FePt）",
         "risk": "低~中", "note": "HAMR 技术（Seagate）可减少钌用量，但量产转型需 5-8 年"},
        {"app": "半导体互连", "sub": "钴（Co）、铜（Cu）",
         "risk": "低", "note": "钌在 3nm 以下优势更突出，替代方向相反（钌替代铜，非被铜替代）"},
        {"app": "DSA 氯碱电极", "sub": "IrO₂ 基涂层",
         "risk": "低~中", "note": "铱比钌贵 5-6×，一般作为混合使用而非替代"},
        {"app": "PEM 电解槽阳极", "sub": "铱（Ir）催化剂",
         "risk": "中", "note": "铱稳定性更好但更贵；研究中的非贵金属催化剂尚不成熟"},
        {"app": "氨合成催化剂", "sub": "铁基 / 钴基催化剂",
         "risk": "中", "note": "传统铁催化剂成本低，但钌基低温低压优势日益凸显，竞争共存"},
    ],
    # ── 未来五年供需判断 ──────────────────────────────────────────────
    "outlook": {
        "supply_growth": "+3~5%/yr",
        "demand_growth": "+8~12%/yr",
        "current_balance": "赤字 ~6.8 t（2024）",
        "verdict": "tightening",  # tightening / balanced / surplus
        "verdict_cn": "趋紧",
        "phases": [
            {"period": "2026",
             "label": "短暂平衡",
             "color": "#fbbf24",
             "body": "绿氢项目推进慢，铂矿产量温和回升，市场从赤字转向基本平衡。价格承压整合。"},
            {"period": "2027",
             "label": "再度收紧",
             "color": "#fb923c",
             "body": "半导体互连（TSMC/Intel 3nm 量产）+ HDD AI 数据中心补货同步发力，需求端多点开花。"},
            {"period": "2028–2030",
             "label": "结构性短缺",
             "color": "#f87171",
             "body": "绿氢电解槽规模化采购启动，钌供给弹性极低（铂矿副产品无法独立扩产），市场进入系统性短缺。"},
        ],
        "key_risks": [
            "绿氢政策退坡或补贴缩减（最大下行风险）",
            "HAMR 技术普及降低 HDD 钌用量（中等风险）",
            "南非电力/劳工问题导致矿山停产（供给黑天鹅）",
            "RuO₂ 电解槽稳定性技术突破（长期颠覆潜力）",
        ],
    },
}


def load_data():
    if not DB_PATH.exists():
        print("错误：数据库不存在，请先运行 python3 metals_tracker.py")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # 全部历史数据（含多来源）
    rows = conn.execute(
        "SELECT date, metal, price_usd, source FROM prices ORDER BY date"
    ).fetchall()

    # 最新价格（每种金属×每个来源各取最新）
    latest = conn.execute("""
        SELECT metal, price_usd, source, date
        FROM prices p1
        WHERE date = (
            SELECT MAX(date) FROM prices p2
            WHERE p2.metal = p1.metal AND p2.source = p1.source
        )
        ORDER BY metal, source
    """).fetchall()

    # 最近 30 天（Umicore 作为主线，其余来源作对比）
    recent = conn.execute("""
        SELECT date, metal, price_usd, source
        FROM prices
        WHERE date >= date('now', '-30 days')
        ORDER BY date DESC, metal, source
    """).fetchall()

    # 价格变化分析数据（以 Umicore 为基准）
    analysis = {}
    for metal in ("Ruthenium", "Iridium"):
        metal_rows = conn.execute(
            "SELECT date, price_usd FROM prices WHERE metal=? AND source='umicore' ORDER BY date",
            (metal,)
        ).fetchall()
        if not metal_rows:
            # 如果 umicore 无数据，用任何来源的数据
            metal_rows = conn.execute(
                "SELECT date, price_usd FROM prices WHERE metal=? ORDER BY date",
                (metal,)
            ).fetchall()
        if not metal_rows:
            continue
        current_price = metal_rows[-1][1]
        current_date  = metal_rows[-1][0]
        peak = max(metal_rows, key=lambda r: r[1])
        low  = min(metal_rows, key=lambda r: r[1])
        ago30_rows = [r for r in metal_rows if r[0] <= conn.execute(
            "SELECT date('now','-30 days')"
        ).fetchone()[0]]
        ago30 = ago30_rows[-1] if ago30_rows else metal_rows[0]
        chg30 = (current_price - ago30[1]) / ago30[1] * 100
        chg_peak = (current_price - peak[1]) / peak[1] * 100
        chg_low  = (current_price - low[1])  / low[1]  * 100
        analysis[metal] = {
            "current": current_price, "current_date": current_date,
            "ago30_price": ago30[1],  "ago30_date": ago30[0], "chg30": chg30,
            "peak_price": peak[1],    "peak_date": peak[0],   "chg_peak": chg_peak,
            "low_price":  low[1],     "low_date":  low[0],    "chg_low": chg_low,
        }

    # 最新多来源对比（同日期各来源价格）
    source_compare = {}
    for metal, price, source, dt in latest:
        source_compare.setdefault(metal, []).append({
            "source": source, "price": price, "date": dt
        })

    # 钌粉现货（powder_prices 表）
    powder = []
    try:
        powder = conn.execute("""
            SELECT date, product, price_cny, unit, supplier, url
            FROM powder_prices
            WHERE date >= date('now', '-7 days')
            ORDER BY date DESC, price_cny
        """).fetchall()
    except Exception:
        pass

    # 今日预警（price_alerts 表）
    today_alerts = []
    try:
        from alert_engine import get_today_alerts
        today_alerts = get_today_alerts(conn)
    except Exception:
        pass

    conn.close()
    return rows, latest, recent, analysis, source_compare, powder, today_alerts


def build_chart_data(rows):
    """构建图表数据集，按 (metal, source) 分组。"""
    series = {}
    for dt, metal, price, source in rows:
        key = (metal, source)
        series.setdefault(key, {"dates": [], "prices": []})
        series[key]["dates"].append(dt)
        series[key]["prices"].append(price)
    return series


def build_powder_section(powder: list) -> str:
    """构建「钌粉市场」组件 HTML（来自 91金属网）。"""
    if not powder:
        return """
<div class="sc-section" id="powder">
  <div class="section-title">
    <span class="section-icon">🔬</span> 钌粉现货市场（91金属网）
    <span class="section-date">暂无数据 · 运行 python3 metals_tracker.py 抓取</span>
  </div>
  <div class="sc-card" style="color:#64748b;font-size:13px;padding:20px 24px;">
    暂无钌粉报价数据。请先运行 <code>python3 metals_tracker.py</code> 从 91金属网获取。<br>
    也可手动访问
    <a href="https://hq.91jinshu.com/price-htm-itemid-22.html" target="_blank"
       style="color:#e8a838">91金属网 · 钌粉价格走势</a>。
  </div>
</div>"""

    rows_html = ""
    for dt, product, price_cny, unit, supplier, url in powder:
        link_open  = f'<a href="{url}" target="_blank" style="color:#e8a838">' if url else ""
        link_close = "</a>" if url else ""
        rows_html += f"""
        <tr>
          <td class="p-date">{dt}</td>
          <td class="p-name">{link_open}{product}{link_close}</td>
          <td class="p-price">¥{price_cny:,.1f}<span class="p-unit">/{unit}</span></td>
          <td class="p-supplier">{supplier or '—'}</td>
        </tr>"""

    # 换算参考：CNY/g → USD/troy oz（参考汇率 7.2，仅供参考）
    if powder:
        avg_cny = sum(r[2] for r in powder) / len(powder)
        usd_ref = avg_cny * 31.1035 / 7.2
        ref_note = f"均价 ¥{avg_cny:,.1f}/g ≈ ${usd_ref:,.0f}/troy oz（按 USD/CNY=7.2 估算，仅供参考）"
    else:
        ref_note = ""

    return f"""
<div class="sc-section" id="powder">
  <div class="section-title">
    <span class="section-icon">🔬</span> 钌粉现货市场（91金属网）
    <span class="section-date">中国市场报价 · CNY/g</span>
  </div>
  <div class="sc-card">
    <div class="sc-card-title">钌粉近期报价（近 7 天）</div>
    <table class="sub-table powder-table">
      <thead><tr>
        <th>日期</th><th>产品名称</th><th>报价</th><th>供应商</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    <div class="sc-note">⚠ {ref_note}<br>
      注：91金属网为中国有色金属行情平台，钌粉报价为市场参考价。
      国际市场计量单位为 troy oz（金衡盎司），1 troy oz = 31.1035 g。
    </div>
  </div>
</div>"""


def build_supply_chain_html() -> str:
    """构建「产业链地图」组件：产地产量 / 供应链流程 / 需求拆解 / 替代材料 / 五年展望。"""
    d = SUPPLY_CHAIN_DATA

    # ── 1. 产地与产量（近 10 年横向条形图 via HTML bars）─────────────────
    prod = d["production"]
    years = prod["years"]
    regions = prod["regions"]
    palette = {"南非 South Africa": "#a78bfa", "俄罗斯 Russia": "#f87171", "其他 Others": "#64748b"}
    max_total = max(sum(v[i] for v in regions.values()) for i in range(len(years)))

    prod_rows = ""
    for i, yr in enumerate(years):
        totals = {r: v[i] for r, v in regions.items()}
        total = sum(totals.values())
        bar_html = ""
        for region, val in totals.items():
            pct = val / max_total * 100
            bar_html += f'<div class="pbar-seg" style="width:{pct:.1f}%;background:{palette[region]}" title="{region}: {val}t"></div>'
        prod_rows += f"""
        <div class="pbar-row">
          <div class="pbar-yr">{yr}</div>
          <div class="pbar-track">{bar_html}</div>
          <div class="pbar-total">{total:.1f} t</div>
        </div>"""

    legend_html = "".join(
        f'<span class="legend-dot" style="background:{palette[r]}"></span><span class="legend-label">{r}</span>'
        for r in regions
    )

    # ── 2. 供应链流程（矿山→精炼→终端）──────────────────────────────────
    chain_html = ""
    for ci, stage in enumerate(d["chain"]):
        players_html = "".join(
            f'<div class="player"><span class="player-name">{p["name"]}</span>'
            f'<span class="player-detail">{p["detail"]}</span></div>'
            for p in stage["players"]
        )
        arrow = '<div class="chain-arrow">→</div>' if ci < len(d["chain"]) - 1 else ""
        chain_html += f"""
        <div class="chain-block">
          <div class="chain-header" style="color:{stage['color']}">
            <span>{stage['icon']}</span> {stage['stage']}
          </div>
          <div class="chain-players">{players_html}</div>
        </div>{arrow}"""

    # ── 3. 需求拆解（条形 + 详情）────────────────────────────────────────
    demand_rows = ""
    total_gross = sum(x["value"] for x in d["demand_breakdown"] if x["value"] > 0)
    for item in d["demand_breakdown"]:
        if item["value"] < 0:
            continue
        bar_w = item["value"] / total_gross * 100
        badge_color = {"RuO₂ 浆料": "#a78bfa", "钌金属/钌盐催化剂": "#34d399",
                       "RuO₂/TiO₂ 涂层（DSA 电极）": "#fb923c",
                       "钌金属（纯靶材）": "#4f8ef7",
                       "钌金属（溅射靶材 / ALD 前驱体）": "#38bdf8",
                       "钌铂/钌铱合金": "#64748b"}.get(item["form"], "#64748b")
        demand_rows += f"""
        <div class="demand-row">
          <div class="demand-label">{item['label']}</div>
          <div class="demand-bar-wrap">
            <div class="demand-bar" style="width:{bar_w:.0f}%;background:{badge_color}22;border-left:3px solid {badge_color}">
              <span style="color:{badge_color};font-weight:700">{item['value']}t</span>
              <span style="color:#64748b;font-size:11px"> · {item['pct']}%</span>
            </div>
          </div>
          <div class="demand-detail">
            <span class="demand-form" style="background:{badge_color}22;color:{badge_color}">{item['form']}</span>
            <div class="demand-users">采购方：{item['users']}</div>
            <div class="demand-sub">{item['sub']}</div>
          </div>
        </div>"""

    # ── 4. 替代材料风险表 ─────────────────────────────────────────────
    risk_color = {"低": "#34d399", "低~中": "#a3e635", "中": "#fbbf24", "高": "#f87171"}
    sub_rows = "".join(
        f"""<tr>
          <td>{s['app']}</td>
          <td>{s['sub']}</td>
          <td><span class="risk-badge" style="background:{risk_color.get(s['risk'],'#64748b')}22;color:{risk_color.get(s['risk'],'#64748b')}">{s['risk']}</span></td>
          <td style="color:#94a3b8;font-size:12px">{s['note']}</td>
        </tr>"""
        for s in d["substitutes"]
    )

    # ── 5. 五年供需展望 ────────────────────────────────────────────────
    outlook = d["outlook"]
    phases_html = "".join(
        f"""<div class="phase-item" style="border-left:3px solid {p['color']}">
          <div class="phase-period" style="color:{p['color']}">{p['period']}</div>
          <div class="phase-label">{p['label']}</div>
          <div class="phase-body">{p['body']}</div>
        </div>"""
        for p in outlook["phases"]
    )
    risks_html = "".join(f'<li>{r}</li>' for r in outlook["key_risks"])

    verdict_color = {"tightening": "#f87171", "balanced": "#fbbf24", "surplus": "#34d399"}.get(
        outlook["verdict"], "#94a3b8")

    return f"""
<div class="sc-section">
  <div class="section-title">
    <span class="section-icon">🗺</span> 钌产业链深度地图
    <span class="section-date">数据综合自 SFA(Oxford) · JM PGM Report · Heraeus · 智研咨询</span>
  </div>

  <!-- 1. 产地产量 -->
  <div class="sc-card">
    <div class="sc-card-title">① 产地与产量（全球，公吨，2015–2024）</div>
    <div class="pbar-legend">{legend_html}</div>
    <div class="pbar-container">{prod_rows}</div>
    <div class="sc-note">⚠ {prod['note']}</div>
  </div>

  <!-- 2. 供应链流程 -->
  <div class="sc-card">
    <div class="sc-card-title">② 销售渠道与供应链结构</div>
    <div class="chain-flow">{chain_html}</div>
    <div class="sc-note">⚠ 钌无公开期货市场，定价完全依赖 OTC（场外）网络；JM/Heraeus/Umicore 发布的日度定盘价是市场基准。</div>
  </div>

  <!-- 3. 需求拆解 -->
  <div class="sc-card">
    <div class="sc-card-title">③ 采购厂家 & 需求拆解（2024 全球，总量 ~34.5 t）</div>
    <div class="demand-list">{demand_rows}</div>
    <div class="sc-note">⚠ 中国需求约占全球 30–35%（~10–12 t），以厚膜电阻（村田代工厂、风华高科）和化工催化为主，进口依赖度 >95%，金川集团钌产量极微。</div>
  </div>

  <!-- 4. 替代材料 -->
  <div class="sc-card">
    <div class="sc-card-title">④ 替代材料风险评估</div>
    <table class="sub-table">
      <thead><tr><th>应用场景</th><th>潜在替代物</th><th>替代风险</th><th>说明</th></tr></thead>
      <tbody>{sub_rows}</tbody>
    </table>
  </div>

  <!-- 5. 五年展望 -->
  <div class="sc-card">
    <div class="sc-card-title">⑤ 未来五年供需判断（2026–2030）</div>
    <div class="outlook-meta">
      <div class="outlook-meta-item">
        <span class="oml">年供给增速</span><span class="omv">{outlook['supply_growth']}</span>
      </div>
      <div class="outlook-meta-item">
        <span class="oml">年需求增速</span><span class="omv">{outlook['demand_growth']}</span>
      </div>
      <div class="outlook-meta-item">
        <span class="oml">当前市场</span><span class="omv">{outlook['current_balance']}</span>
      </div>
      <div class="outlook-meta-item verdict">
        <span class="oml">五年总判断</span>
        <span class="omv" style="color:{verdict_color};font-size:20px;font-weight:800">
          供需 {outlook['verdict_cn']} ↑
        </span>
      </div>
    </div>
    <div class="phases">{phases_html}</div>
    <div class="risks-block">
      <div class="risks-title">主要风险点</div>
      <ul class="risks-list">{risks_html}</ul>
    </div>
  </div>
</div>"""


def build_analysis_html(analysis: dict) -> str:
    """构建「价格变化分析」组件的 HTML。"""

    def fmt_chg(v):
        arrow = "▲" if v > 0 else "▼"
        color = "#22c55e" if v > 0 else "#f87171"
        return f'<span style="color:{color}">{arrow} {abs(v):.1f}%</span>'

    ru = analysis.get("Ruthenium", {})
    ir = analysis.get("Iridium", {})

    # ── 统计摘要行 ────────────────────────────────────────────────────────────
    def stat_row(metal, a, color):
        cn = "钌" if metal == "Ruthenium" else "铱"
        return f"""
        <div class="stat-row" style="border-left:3px solid {color}">
          <div class="stat-name" style="color:{color}">{cn} {metal}</div>
          <div class="stat-item">
            <span class="stat-label">当前</span>
            <span class="stat-val">${a['current']:,.0f}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">30 天前</span>
            <span class="stat-val">${a['ago30_price']:,.0f}
              &nbsp;{fmt_chg(a['chg30'])}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">年内高点</span>
            <span class="stat-val">${a['peak_price']:,.0f}
              &nbsp;{fmt_chg(a['chg_peak'])}<small style="color:#64748b"> ({a['peak_date']})</small></span>
          </div>
          <div class="stat-item">
            <span class="stat-label">年内低点</span>
            <span class="stat-val">${a['low_price']:,.0f}
              &nbsp;{fmt_chg(a['chg_low'])}<small style="color:#64748b"> ({a['low_date']})</small></span>
          </div>
        </div>"""

    stats_html = stat_row("Ruthenium", ru, "#e8a838") + stat_row("Iridium", ir, "#4f8ef7")

    # ── 四维分析卡片 ──────────────────────────────────────────────────────────
    dims = [
        {
            "icon": "📉",
            "title": "需求端 · 钌铱分别归因（核心）",
            "accent": "#34d399",
            "points": [
                ("钌 ← 化工催化需求下滑（主拖累）",
                 "Johnson Matthey 2026 PGM 报告预计<strong>钌全年需求 −6%</strong>，"
                 "主因<strong>化工板块</strong>（硝酸、氯碱催化等）用量收缩，是钌价回落的直接推手。"),
                ("钌 ← AI 硬盘需求其实是利好",
                 "数据中心扩张带动硬盘（HDD 磁存储钌薄膜层）需求升至<strong>五年新高</strong>。"
                 "⚠ 这是<strong>支撑项而非利空</strong>——钌的回调来自化工，不是 AI/存储端。"),
                ("铱 ← 绿氢电解槽需求降温（主因）",
                 "「工业需求走弱 + PEM 电解槽消耗低于预期」是铱下跌的直接原因。"
                 "绿氢项目兑现速度明显慢于此前炒作时所定价的预期。"),
                ("库存去化周期",
                 "部分中间商在 2025 年底高点抢购备货，目前仍处去库阶段，"
                 "短期压制采购意愿，买方议价力增强。"),
            ],
        },
        {
            "icon": "🔄",
            "title": "板块重置 · 同步回调",
            "accent": "#fb923c",
            "points": [
                ("整个 PGM 板块「价格重置」",
                 "Heraeus 2026 年度预测明确指出：<strong>2026 上半年 PGM 价格将整体下行重置</strong>。"
                 "钌、铱 4 月起<strong>同步见顶、同步阴跌</strong>，正是板块级回调、而非单一金属供应事故。"),
                ("高位有大涨在先",
                 "铂金 2026 年 1 月刚创历史新高，整个铂族金属在过去一年涨幅惊人；"
                 "钌从 2025/5 低点 $615 → 2026/3 高点 $1,750（<strong>+184%</strong>），"
                 "铱同期 +95%。涨多必有回，技术性挤泡沫。"),
                ("宏观面偏空",
                 "2026 年初美元偏强、美欧制造业 PMI 承压，以美元计价的工业贵金属"
                 "边际买盘减少，投机多头顺势减仓。"),
            ],
        },
        {
            "icon": "⚗",
            "title": "替代技术 · 压住高点",
            "accent": "#38bdf8",
            "points": [
                ("铱：减量化突破封顶价格",
                 "牛津大学将 PEM 电解槽<strong>铱用量削减 70%</strong>，西北大学研发出可媲美铱的"
                 "替代催化剂。金属越贵、买家越倾向减量/替代，直接给铱的投机高点封顶并加速回调。"),
                ("投机资金获利了结",
                 "PGM ETF 与大宗商品基金在高点止盈，现货买气转谨慎，"
                 "交易商倾向观望、待方向明朗再建仓。"),
            ],
        },
        {
            "icon": "⛏",
            "title": "供给端 · 平稳（非诱因）",
            "accent": "#a78bfa",
            "points": [
                ("供给稳定，这轮跌的是需求/情绪",
                 "钌铱均为 PGM 副产品，南非贡献全球逾 80%，产量无法独立调节。"
                 "本轮供给<strong>没有明显放量</strong>，因此价格走弱由需求与情绪驱动，而非供应过剩。"),
                ("基本面仍是短缺（回调≠反转）",
                 "Metals Focus 估 2026 年<strong>钌仍缺口约 20.3 万盎司</strong>，铱亦维持小幅短缺；"
                 "供给弹性极低。SFA(Oxford)、JM 维持长期看多——当前是高位修正，不是趋势逆转。"),
            ],
        },
    ]

    cards_html = ""
    for d in dims:
        points_html = ""
        for title, body in d["points"]:
            points_html += f"""
            <div class="point">
              <div class="point-title" style="color:{d['accent']}">{title}</div>
              <div class="point-body">{body}</div>
            </div>"""
        cards_html += f"""
        <div class="dim-card">
          <div class="dim-header">
            <span class="dim-icon">{d['icon']}</span>
            <span class="dim-title" style="color:{d['accent']}">{d['title']}</span>
          </div>
          {points_html}
        </div>"""

    # ── 展望 ──────────────────────────────────────────────────────────────────
    outlook_html = f"""
    <div class="outlook">
      <div class="outlook-title">综合展望</div>
      <div class="outlook-grid">
        <div class="outlook-item bearish">
          <div class="outlook-tag">短期 · 承压</div>
          <div>板块「价格重置」未结束（Heraeus：2026 上半年 PGM 整体下行）、
          铱绿氢兑现慢 + 替代技术压顶、钌化工需求疲软，预计 Q2–Q3 仍有下行压力；<br>
          钌支撑位约 <strong>$1,400</strong>，铱关注 <strong>$6,800</strong>。</div>
        </div>
        <div class="outlook-item bullish">
          <div class="outlook-tag">长期 · 看多</div>
          <div>基本面仍是<strong>短缺</strong>（钌 2026 缺口 ~20.3 万 oz、铱小幅短缺），供给弹性极低；<br>
          钌受 <strong>AI 数据中心硬盘存储</strong>需求拉动（已创五年新高），
          铱则看 <strong>绿氢 PEM 电解槽</strong>规模化商用——当前回调被机构视为买点而非反转。</div>
        </div>
      </div>
    </div>
    <div class="analysis-src">
      数据来源：Johnson Matthey 2026 PGM 市场报告 · Heraeus 2026 贵金属预测 ·
      Metals Focus · SFA (Oxford) · iMARC（铱价 2026）。价格走势基于 Umicore 官方数据。
    </div>"""

    return f"""
<div class="analysis-section">
  <div class="section-title">
    <span class="section-icon">🔍</span> 价格变化分析
    <span class="section-date">基于 Umicore 官方数据 · 截至 {ru.get('current_date','今日')}</span>
  </div>

  <!-- 统计摘要 -->
  <div class="stats-block">{stats_html}</div>

  <!-- 四维分析 -->
  <div class="dims-grid">{cards_html}</div>

  <!-- 展望 -->
  {outlook_html}
</div>"""


METAL_CN_DASH = {"Ruthenium": "钌", "Iridium": "铱"}
ALERT_TYPE_CN_DASH = {"price_threshold": "价格阈值", "anomaly": "价格异常"}


def build_alert_section(alerts: list) -> str:
    """构建「今日预警」顶部区块：有预警 → 红色卡片；无预警 → 灰色提示。"""
    if not alerts:
        return """
<section id="alerts">
  <div class="alert-bar alert-bar--clear">
    <span class="alert-bar-icon">✅</span>
    <span class="alert-bar-text">今日无重大价格变化</span>
  </div>
</section>"""

    items_html = ""
    for a in alerts:
        cn      = METAL_CN_DASH.get(a["metal"], a["metal"])
        type_cn = ALERT_TYPE_CN_DASH.get(a["alert_type"], a["alert_type"])
        items_html += f"""
    <div class="alert-item">
      <div class="alert-item-header">
        <span class="alert-badge">{cn} · {type_cn}</span>
        <span class="alert-cond">{a['condition']}</span>
      </div>
      <div class="alert-note">{a['note']}</div>
    </div>"""

    return f"""
<section id="alerts">
  <div class="alert-bar alert-bar--warn">
    <div class="alert-bar-top">
      <span class="alert-bar-icon">⚠️</span>
      <span class="alert-bar-text">今日预警（{len(alerts)} 条）</span>
    </div>
    <div class="alert-items">{items_html}
    </div>
  </div>
</section>"""


SOURCE_LABELS = {
    "umicore":          "Umicore",
    "johnson_matthey":  "Johnson Matthey",
    "basf":             "BASF",
    "metals-api":       "Metals-API",
}

SOURCE_STYLES = {
    # (borderColor, dashPattern)
    "umicore":         ("#e8a838", []),
    "johnson_matthey": ("#f97316", [6, 3]),
    "basf":            ("#a78bfa", [4, 2]),
    "metals-api":      ("#64748b", [2, 4]),
}

SOURCE_IR_STYLES = {
    "umicore":         ("#4f8ef7", []),
    "johnson_matthey": ("#38bdf8", [6, 3]),
    "basf":            ("#818cf8", [4, 2]),
    "metals-api":      ("#94a3b8", [2, 4]),
}


# ── 产品化配置（可直接改这里换品牌/联系方式）─────────────────────────────────
BRAND = {
    "name":    "PGM Insight",
    "name_cn": "铂族金属价格情报",
    "tagline": "钌 · 铱 · 钌粉 — 全链路价格追踪、归因分析与市场情报",
    "email":   "richlililala@gmail.com",
}

# ── 价格变动归因（编辑性内容，基于行业报告分析；最新在前）──────────────────────
PRICE_EVENTS = [
    {"date": "2026-05-29", "metal": "钌", "frm": 1550, "to": 1500,
     "dim": "需求 · 板块", "tag": "板块重置延续 · 化工需求疲软",
     "detail": "PGM 板块「价格重置」延续（Heraeus：2026 上半年整体下行）。钌端化工催化采购走弱"
               "（Johnson Matthey：2026 钌需求 −6%），盖过了 AI 数据中心硬盘需求创五年新高的利好"
               "——所以这一跌的主因是<b>化工</b>，而非 AI/存储端。"},
    {"date": "2026-05-27", "metal": "铱", "frm": 7250, "to": 7100,
     "dim": "需求 · 替代", "tag": "工业需求走弱 · 替代技术压顶",
     "detail": "绿氢 PEM 电解槽采购低于预期、工业需求转弱；牛津大学将<b>铱用量削减 70%</b> 的"
               "减量化突破给高价封顶，加速回调。"},
    {"date": "2026-05-12", "metal": "钌", "frm": 1650, "to": 1550,
     "dim": "市场情绪", "tag": "投机多头获利了结",
     "detail": "年初 +184% 暴涨后，PGM ETF 与商品基金在高位止盈，现货买气转谨慎，做空力量阶段性主导。"},
    {"date": "2026-04-28", "metal": "铱", "frm": 7800, "to": 7500,
     "dim": "需求 · 绿氢", "tag": "绿氢兑现慢于预期",
     "detail": "市场对绿氢电解槽 2026 大规模放量的预期降温，提前对铱最大的新兴需求重新定价。"},
    {"date": "2026-04-21", "metal": "钌", "frm": 1750, "to": 1700,
     "dim": "板块 · 宏观", "tag": "板块价格重置启动",
     "detail": "铂金 1 月创历史新高后，整个铂族金属同步见顶回落；美元偏强、制造业 PMI 承压共同施压。"},
    {"date": "2026-03-12", "metal": "钌", "frm": 1400, "to": 1750, "peak": True,
     "dim": "投机买盘", "tag": "年内见顶（月内 +25%）",
     "detail": "供给刚性（副产品）叠加 AI / 数据中心叙事，投机性买盘把钌推上年内高点 $1,750，"
               "铱同步见顶 $8,000。此后转入回调。"},
]


def _mailto(subject: str, body: str = "") -> str:
    from urllib.parse import quote
    url = f"mailto:{BRAND['email']}?subject={quote(subject)}"
    if body:
        url += f"&body={quote(body)}"
    return url


def build_hero_html(analysis, powder) -> str:
    """产品首屏：价值主张 + 关键数据 + 转化按钮。"""
    ru = analysis.get("Ruthenium", {})
    ir = analysis.get("Iridium", {})
    ru_price = f"${ru.get('current', 0):,.0f}" if ru else "—"
    ir_price = f"${ir.get('current', 0):,.0f}" if ir else "—"
    powder_price = f"¥{powder[0][2]:,.0f}/g" if powder else "—"

    def chg(v):
        cls = "up" if v > 0 else "down"
        arr = "▲" if v > 0 else "▼"
        return f'<span class="hero-chg {cls}">{arr} {abs(v):.1f}%</span>'

    return f"""
<section id="hero" class="hero">
  <div class="hero-eyebrow">PGM PRICE INTELLIGENCE · 铂族金属价格情报</div>
  <h1 class="hero-title">{BRAND['name_cn']}</h1>
  <p class="hero-tagline">{BRAND['tagline']}</p>
  <p class="hero-sub">每个交易日自动追踪钌・铱国际定盘价与中国钌粉现货，配套<strong>归因分析</strong>与
  <strong>市场情报</strong>——把贵金属行情，变成你能直接拿去和客户对话的标准化工具。</p>
  <div class="hero-stats">
    <div class="hero-stat"><span class="hs-label">钌 Ru</span><span class="hs-val">{ru_price}</span>{chg(ru.get('chg30', 0))}<span class="hs-unit">/oz · 30天</span></div>
    <div class="hero-stat"><span class="hs-label">铱 Ir</span><span class="hs-val">{ir_price}</span>{chg(ir.get('chg30', 0))}<span class="hs-unit">/oz · 30天</span></div>
    <div class="hero-stat"><span class="hs-label">钌粉</span><span class="hs-val">{powder_price}</span><span class="hs-unit">中国现货</span></div>
  </div>
  <div class="hero-cta">
    <a class="btn btn-primary" href="#subscribe">订阅每日行情 →</a>
    <a class="btn btn-ghost" href="#subscribe">预约演示 / 咨询</a>
    <button class="btn btn-chat" onclick="openQA()">💬 问问行情助手</button>
  </div>
  <div class="hero-trust">数据来源：Umicore · Johnson Matthey · BASF · 91金属网　|　每个纽约交易日 10:00 自动更新</div>
</section>"""


def build_attribution_html() -> str:
    """价格变动归因：每一笔涨跌对应原因（回答「为什么跌了」）。"""
    color = {"钌": "#e8a838", "铱": "#4f8ef7"}
    items = ""
    for e in PRICE_EVENTS:
        c = color.get(e["metal"], "#94a3b8")
        delta = e["to"] - e["frm"]
        pct = delta / e["frm"] * 100
        up = delta > 0
        arr = "▲" if up else "▼"
        cls = "up" if up else "down"
        peak = '<span class="evt-peak">高点</span>' if e.get("peak") else ""
        items += f"""
        <div class="evt" style="border-left-color:{c}">
          <div class="evt-top">
            <span class="evt-date">{e['date']}</span>
            <span class="evt-metal" style="color:{c}">{e['metal']}</span>
            <span class="evt-move">${e['frm']:,} → ${e['to']:,}</span>
            <span class="evt-chg {cls}">{arr} {abs(pct):.1f}%</span>
            <span class="evt-dim">{e['dim']}</span>{peak}
          </div>
          <div class="evt-tag">{e['tag']}</div>
          <div class="evt-detail">{e['detail']}</div>
        </div>"""
    return f"""
<div class="attr-wrap">
  <div class="section-title">
    <span class="section-icon">🧭</span> 价格变动归因
    <span class="section-date">每一笔涨跌 · 对应原因</span>
  </div>
  <div class="attr-intro">看到价格变化，想知道「为什么」？这里按时间列出每一笔显著变动，并标注最可能的驱动因素
  （基于 Johnson Matthey / Heraeus / SFA 等行业报告分析，而非逐笔新闻）。</div>
  <div class="evt-list">{items}</div>
</div>"""


def build_cta_html() -> str:
    """订阅 + 预约 + 套餐（核心转化）。"""
    demo = _mailto("预约演示 / 咨询 — PGM Insight",
                   "我想预约一次 15 分钟的演示/咨询。\n公司：\n关注品种：\n联系方式：")
    pro = _mailto("专业版询价 — PGM Insight", "我想了解专业版（每日推送 + 预警 + 报告）的报价。")
    ent = _mailto("企业版咨询 — PGM Insight", "我想了解企业版（定制看板 / API / 咨询）。")
    return f"""
<div class="cta-wrap">
  <div class="section-title"><span class="section-icon">📬</span> 订阅 · 预约
    <span class="section-date">把行情变成你的销售工具</span>
  </div>
  <div class="cta-grid">
    <div class="cta-card">
      <div class="cta-card-title">📈 每日行情订阅</div>
      <p class="cta-card-sub">每个交易日把钌・铱・钌粉最新价、涨跌与归因摘要发到你邮箱。</p>
      <ul class="cta-list">
        <li>钌 / 铱国际定盘价 + 中国钌粉现货</li>
        <li>当日涨跌归因 + 关键事件</li>
        <li>异动预警（突破阈值即时提醒）</li>
      </ul>
      <div class="sub-form">
        <input id="subEmail" type="email" placeholder="you@company.com" class="sub-input">
        <button class="btn btn-primary" onclick="submitSubscribe()">订阅</button>
      </div>
    </div>
    <div class="cta-card">
      <div class="cta-card-title">🤝 预约演示 / 咨询</div>
      <p class="cta-card-sub">想定制品种、定制报告，或把它接入你的销售流程？聊 15 分钟。</p>
      <ul class="cta-list">
        <li>按你的品种 / 客户定制看板</li>
        <li>深度研究与一对一市场咨询</li>
        <li>API / 私有部署 / 白标</li>
      </ul>
      <a class="btn btn-primary" href="{demo}">预约演示 / 咨询 →</a>
    </div>
  </div>
  <div class="plans">
    <div class="plan">
      <div class="plan-name">免费版</div>
      <div class="plan-price">¥0</div>
      <ul class="plan-feat"><li>在线看板浏览</li><li>每日价格 + 走势图</li><li>基础归因分析</li></ul>
      <a class="btn btn-ghost" href="#hero">立即使用</a>
    </div>
    <div class="plan plan--featured">
      <div class="plan-badge">最受欢迎</div>
      <div class="plan-name">专业版</div>
      <div class="plan-price">联系询价</div>
      <ul class="plan-feat"><li>邮件 / 微信每日推送</li><li>异动预警</li><li>钌粉现货 + 多源对比</li><li>季度深度报告</li></ul>
      <a class="btn btn-primary" href="{pro}">询价</a>
    </div>
    <div class="plan">
      <div class="plan-name">企业版</div>
      <div class="plan-price">定制</div>
      <ul class="plan-feat"><li>定制品种 / 客户看板</li><li>API + 私有部署</li><li>一对一市场咨询</li></ul>
      <a class="btn btn-ghost" href="{ent}">联系我们</a>
    </div>
  </div>
</div>"""


def build_qa_assistant_html(analysis, powder) -> str:
    """对话式行情助手（内置问答，模拟销售对话；纯前端可离线）。"""
    import json as _json
    ru = analysis.get("Ruthenium", {})
    ir = analysis.get("Iridium", {})
    ru_cur, ru_peak, ru_cp = ru.get('current', 0), ru.get('peak_price', 0), ru.get('chg_peak', 0)
    ir_cur, ir_peak, ir_cp = ir.get('current', 0), ir.get('peak_price', 0), ir.get('chg_peak', 0)
    powder_p = powder[0][2] if powder else 0
    qa = [
        {"q": "为什么钌最近跌了？",
         "a": f"钌现报 <b>${ru_cur:,.0f}</b>，较年内高点 ${ru_peak:,.0f} 回落 {abs(ru_cp):.1f}%。<br><br>"
              "两个主因：<br>① <b>板块重置</b>——铂金 1 月创新高后整个 PGM 同步回调（Heraeus 预警 2026 上半年下行）；<br>"
              "② <b>化工需求走弱</b>——Johnson Matthey 预计 2026 钌需求 −6%，化工催化用量下降。<br><br>"
              "⚠️ 钌在 AI 数据中心硬盘的需求其实创<b>五年新高</b>（利好），所以这是化工拖累、不是 AI 端的问题。"},
        {"q": "为什么铱最近跌了？",
         "a": f"铱现报 <b>${ir_cur:,.0f}</b>，较高点 ${ir_peak:,.0f} 回落 {abs(ir_cp):.1f}%。"
              "主因是<b>绿氢电解槽需求降温</b>（PEM 采购低于预期）+ <b>替代技术压顶</b>"
              "（牛津大学把铱用量削减 70%）。"},
        {"q": "钌粉和国际钌价什么关系？",
         "a": f"国内钌粉现货约 <b>¥{powder_p:,.0f}/克</b>（91金属网），国际钌定盘价 ${ru_cur:,.0f}/oz"
              "（1 oz = 31.1 g）。钌粉≈国际价换算成人民币/克后叠加加工与流通溢价；两者走势同步，"
              "但钌粉对国内供需与汇率更敏感。"},
        {"q": "后市怎么看？",
         "a": "<b>短期承压</b>：板块重置未结束、绿氢兑现慢、化工偏弱——钌看 $1,400 支撑、铱看 $6,800。<br>"
              "<b>长期看多</b>：基本面仍是短缺（钌 2026 缺口约 20.3 万 oz），钌受 AI 硬盘拉动、"
              "铱看绿氢规模化，机构视当前为买点而非反转。"},
        {"q": "数据多久更新？可靠吗？",
         "a": "主来源 <b>Umicore 官方定盘价</b>，每个交易日更新；另有 Johnson Matthey、BASF、"
              "91金属网（钌粉）多源对比。系统每个<b>纽约工作日 10:00 自动抓取</b>并重算归因。"},
        {"q": "怎么订阅？有什么套餐？",
         "a": "免费版可在线浏览；<b>专业版</b>含邮件/微信每日推送、异动预警、季度报告；"
              "<b>企业版</b>支持定制看板与 API。下方「订阅 · 预约」填邮箱即可，或预约 15 分钟演示 👇"},
    ]
    chips = "".join(f'<button class="qa-chip" data-qi="{i}">{x["q"]}</button>'
                    for i, x in enumerate(qa))
    data_json = _json.dumps(qa, ensure_ascii=False)
    return f"""
<button id="qaLauncher" class="qa-launcher" onclick="toggleQA()">💬 行情助手</button>
<div id="qaPanel" class="qa-panel" aria-hidden="true">
  <div class="qa-head">
    <span class="qa-title">💬 PGM 行情助手</span>
    <button class="qa-close" onclick="toggleQA()" aria-label="关闭">✕</button>
  </div>
  <div id="qaBody" class="qa-body">
    <div class="qa-msg bot">你好 👋 我是 PGM 行情助手。想了解什么？点下面的问题——</div>
  </div>
  <div class="qa-chips">{chips}</div>
</div>
<script type="application/json" id="qaData">{data_json}</script>"""


def build_html(rows, latest, recent, analysis, source_compare, powder, today_alerts=None):
    chart_series = build_chart_data(rows)
    today = date.today().strftime("%Y-%m-%d")

    # ── 最新价格卡片（主卡 + 来源对比徽章）────────────────────────────────────
    # 先按 metal 聚合各来源最新价
    metal_latest: dict = {}
    for metal, price, source, dt in latest:
        metal_latest.setdefault(metal, []).append((source, price, dt))

    cards_html = ""
    for metal in ("Ruthenium", "Iridium"):
        srcs = metal_latest.get(metal, [])
        if not srcs:
            continue
        # 主价格：优先 umicore
        main = next((s for s in srcs if s[0] == "umicore"), srcs[0])
        main_source, main_price, main_dt = main
        icon  = "◆" if metal == "Iridium" else "◇"
        color = "#4f8ef7" if metal == "Iridium" else "#e8a838"
        chinese = "铱 Iridium" if metal == "Iridium" else "钌 Ruthenium"

        badges = ""
        for src, price, dt in srcs:
            if src == main_source:
                continue
            lbl = SOURCE_LABELS.get(src, src)
            badges += (
                f'<div class="src-badge">'
                f'<span class="src-lbl">{lbl}</span>'
                f'<span class="src-price">${price:,.0f}</span>'
                f'</div>'
            )

        cards_html += f"""
        <div class="card" style="border-top: 4px solid {color}" id="prices">
            <div class="card-icon" style="color:{color}">{icon}</div>
            <div class="card-metal">{chinese}</div>
            <div class="card-price">${main_price:,.2f}</div>
            <div class="card-unit">USD / troy oz</div>
            <div class="card-date">截至 {main_dt} · {SOURCE_LABELS.get(main_source, main_source)}</div>
            {f'<div class="src-compare">{badges}</div>' if badges else ''}
        </div>"""

    # ── 近 30 天表格 ─────────────────────────────────────────────────────────
    table_rows = ""
    prev_date = None
    for dt, metal, price, source in recent:
        if source != "umicore":
            continue
        if dt != prev_date:
            table_rows += f'<tr class="date-row"><td colspan="4">{dt}</td></tr>'
            prev_date = dt
        cn = "铱" if metal == "Iridium" else "钌"
        color = "#4f8ef7" if metal == "Iridium" else "#e8a838"
        table_rows += f"""
        <tr>
            <td style="padding-left:24px;color:{color}">{cn} {metal}</td>
            <td style="text-align:right;font-weight:600">${price:,.2f}</td>
            <td style="text-align:right;color:#888">USD/troy oz</td>
            <td style="text-align:right;color:#475569;font-size:11px">Umicore</td>
        </tr>"""

    # ── Chart.js 数据集（多来源对比线）──────────────────────────────────────
    datasets = []
    for (metal, source), series in sorted(chart_series.items()):
        is_ir = (metal == "Iridium")
        styles = SOURCE_IR_STYLES if is_ir else SOURCE_STYLES
        color, dash = styles.get(source, ("#aaa", []))
        y_id = "yIr" if is_ir else "yRu"
        cn = "铱" if is_ir else "钌"
        lbl = f"{cn} · {SOURCE_LABELS.get(source, source)}"
        ds = {
            "label": lbl,
            "data": [{"x": d, "y": p} for d, p in zip(series["dates"], series["prices"])],
            "borderColor": color,
            "backgroundColor": color + "22",
            "borderWidth": 2 if not dash else 1.5,
            "pointRadius": 0,
            "tension": 0.3,
            "fill": False,
            "yAxisID": y_id,
        }
        if dash:
            ds["borderDash"] = dash
        datasets.append(ds)

    datasets_json = json.dumps(datasets, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>贵金属价格追踪</title>
{{chartjs_scripts}}
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0f1117;
    color: #e2e8f0;
    min-height: 100vh;
  }}

  /* ── 顶部标题栏 ────────────────────────────────────────────────── */
  header {{
    padding: 24px 24px 18px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px 16px;
    border-bottom: 1px solid #1e2535;
  }}
  header h1 {{ font-size: 20px; font-weight: 700; letter-spacing: 0.02em; white-space: nowrap; flex-shrink: 0; }}
  header .subtitle {{ color: #64748b; font-size: 12px; }}
  .src-tags {{ margin-left: auto; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }}
  .src-tag {{
    font-size: 10px; padding: 3px 8px; border-radius: 999px;
    border: 1px solid #2d3748; color: #64748b;
  }}
  .src-tag.active {{ border-color: #e8a83866; color: #e8a838; }}

  /* ── 主布局：侧边栏 + 内容区 ────────────────────────────────────── */
  .page-layout {{
    display: grid;
    grid-template-columns: 210px minmax(0, 1fr);
    gap: 0;
    align-items: start;
    max-width: 1600px;
    margin: 0 auto;
  }}

  /* ── 侧边栏 TOC ─────────────────────────────────────────────────── */
  .sidebar {{
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
    padding: 20px 14px 20px 16px;
    border-right: 1px solid #1e2535;
    background: #0c1015;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .sidebar::-webkit-scrollbar {{ width: 4px; }}
  .sidebar::-webkit-scrollbar-track {{ background: transparent; }}
  .sidebar::-webkit-scrollbar-thumb {{ background: #2d3748; border-radius: 2px; }}
  .toc-header {{
    font-size: 10px;
    font-weight: 700;
    color: #334155;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    padding: 0 8px 10px;
    border-bottom: 1px solid #1e2535;
    margin-bottom: 6px;
  }}
  .toc-link {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 8px;
    border-radius: 7px;
    color: #64748b;
    font-size: 12px;
    text-decoration: none;
    transition: background 0.15s, color 0.15s;
    line-height: 1.35;
  }}
  .toc-link:hover {{ background: #1a1f2e; color: #e2e8f0; text-decoration: none; }}
  .toc-link.active {{ background: #1a1f2e; color: #e8a838; }}
  .toc-icon {{ font-size: 13px; flex-shrink: 0; opacity: 0.7; }}
  .toc-divider {{
    height: 1px; background: #1e2535;
    margin: 8px 0;
  }}
  .toc-section-label {{
    font-size: 10px; color: #334155; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.1em;
    padding: 6px 8px 2px;
  }}
  .src-legend {{
    margin-top: auto;
    padding-top: 16px;
    border-top: 1px solid #1e2535;
  }}
  .src-legend-title {{
    font-size: 10px; color: #334155; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.1em;
    padding: 0 8px 8px;
  }}
  .src-item {{
    display: flex; align-items: center; gap: 7px;
    padding: 4px 8px; font-size: 11px; color: #64748b;
  }}
  .src-dot {{ width: 20px; height: 2px; border-radius: 1px; flex-shrink: 0; }}
  .src-dot.dashed {{ background: repeating-linear-gradient(
    90deg, currentColor 0, currentColor 4px, transparent 4px, transparent 7px
  ); }}

  /* ── 内容主区 ───────────────────────────────────────────────────── */
  .main-content {{
    padding: 20px 24px 40px;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }}

  /* ── 价格卡片 ───────────────────────────────────────────────────── */
  .cards {{
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }}
  .card {{
    background: #1a1f2e;
    border-radius: 12px;
    padding: 20px 22px;
    flex: 1;
    min-width: 200px;
  }}
  .card-icon {{ font-size: 20px; margin-bottom: 6px; }}
  .card-metal {{ font-size: 12px; color: #94a3b8; margin-bottom: 4px; }}
  .card-price {{ font-size: 32px; font-weight: 700; letter-spacing: -0.02em; }}
  .card-unit {{ font-size: 11px; color: #64748b; margin-top: 2px; }}
  .card-date {{ font-size: 10px; color: #475569; margin-top: 8px; }}
  .src-compare {{
    display: flex; flex-direction: column; gap: 4px;
    margin-top: 10px; padding-top: 10px;
    border-top: 1px solid #2d3748;
  }}
  .src-badge {{
    display: flex; justify-content: space-between; align-items: center;
    font-size: 11px;
  }}
  .src-lbl {{ color: #64748b; }}
  .src-price {{ color: #94a3b8; font-weight: 600; }}

  /* ── 图表区 ─────────────────────────────────────────────────────── */
  .chart-section {{
    background: #1a1f2e;
    border-radius: 12px;
    padding: 20px;
  }}
  .chart-title {{ font-size: 13px; color: #94a3b8; margin-bottom: 14px; }}
  canvas {{ width: 100% !important; }}

  /* 时间维度切换 */
  .chart-head {{
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 10px; margin-bottom: 14px;
  }}
  .chart-head .chart-title {{ margin-bottom: 0; }}
  .range-tabs {{
    display: inline-flex; gap: 2px; padding: 3px;
    background: #0f1117; border: 1px solid #232a3a; border-radius: 9px;
  }}
  .range-btn {{
    font: inherit; font-size: 12px; font-weight: 600;
    color: #94a3b8; background: transparent;
    border: none; border-radius: 6px; padding: 5px 14px;
    cursor: pointer; transition: all .15s;
  }}
  .range-btn:hover {{ color: #e2e8f0; background: #1a2030; }}
  .range-btn.active {{ color: #0f1117; background: #e8a838; }}
  .range-hint {{ font-size: 11px; color: #475569; margin-top: 8px; text-align: right; }}

  /* ── 分析 / 产业链区（公共）───────────────────────────────────── */
  .analysis-section,
  .sc-section {{
    display: flex; flex-direction: column; gap: 16px;
  }}
  .section-title {{
    font-size: 15px;
    font-weight: 700;
    color: #e2e8f0;
    margin-bottom: 4px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .section-icon {{ font-size: 17px; }}
  .section-date {{ font-size: 10px; color: #475569; font-weight: 400; margin-left: auto; }}

  /* 统计摘要 */
  .stats-block {{ display: flex; flex-direction: column; gap: 8px; }}
  .stat-row {{
    background: #1a1f2e;
    border-radius: 10px;
    padding: 12px 18px;
    display: flex;
    align-items: center;
    gap: 24px;
    flex-wrap: wrap;
  }}
  .stat-name {{ font-size: 12px; font-weight: 700; min-width: 120px; }}
  .stat-item {{ display: flex; flex-direction: column; gap: 2px; }}
  .stat-label {{ font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; }}
  .stat-val {{ font-size: 13px; font-weight: 600; }}

  /* 四维分析卡片 */
  .dims-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
  }}
  @media (max-width: 900px) {{ .dims-grid {{ grid-template-columns: 1fr; }} }}
  .dim-card {{ background: #1a1f2e; border-radius: 12px; padding: 18px 20px; }}
  .dim-header {{
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 14px; padding-bottom: 10px;
    border-bottom: 1px solid #2d3748;
  }}
  .dim-icon {{ font-size: 17px; }}
  .dim-title {{ font-size: 13px; font-weight: 700; }}
  .point {{ margin-bottom: 12px; }}
  .point:last-child {{ margin-bottom: 0; }}
  .point-title {{ font-size: 12px; font-weight: 700; margin-bottom: 4px; }}
  .point-body {{ font-size: 11px; color: #94a3b8; line-height: 1.65; }}

  /* 展望 */
  .outlook {{ border-radius: 12px; overflow: hidden; }}
  .outlook-title {{
    font-size: 12px; font-weight: 700; color: #94a3b8;
    margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.08em;
  }}
  .outlook-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  @media (max-width: 700px) {{ .outlook-grid {{ grid-template-columns: 1fr; }} }}
  .outlook-item {{
    border-radius: 10px; padding: 14px 16px;
    font-size: 11px; line-height: 1.7; color: #94a3b8;
  }}
  .outlook-item.bearish {{ background: #1e1a1a; border-left: 3px solid #f87171; }}
  .outlook-item.bullish {{ background: #131e1a; border-left: 3px solid #34d399; }}
  .outlook-tag {{
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; margin-bottom: 6px;
  }}
  .bearish .outlook-tag {{ color: #f87171; }}
  .bullish .outlook-tag {{ color: #34d399; }}
  .outlook-item strong {{ color: #e2e8f0; }}
  .analysis-src {{
    margin-top: 12px; font-size: 11px; color: #475569; line-height: 1.7;
  }}

  /* ── 产业链地图 ─────────────────────────────────────────────────── */
  .sc-card {{ background: #1a1f2e; border-radius: 12px; padding: 20px 22px; }}
  .sc-card-title {{
    font-size: 13px; font-weight: 700; color: #e2e8f0;
    margin-bottom: 14px; padding-bottom: 8px;
    border-bottom: 1px solid #2d3748;
  }}
  .sc-note {{ font-size: 11px; color: #475569; margin-top: 10px; line-height: 1.6; }}

  /* 产量条形图 */
  .pbar-legend {{ display: flex; gap: 14px; margin-bottom: 10px; flex-wrap: wrap; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; margin-right: 4px; vertical-align: middle; }}
  .legend-label {{ font-size: 11px; color: #94a3b8; }}
  .pbar-container {{ display: flex; flex-direction: column; gap: 5px; }}
  .pbar-row {{ display: flex; align-items: center; gap: 8px; }}
  .pbar-yr {{ font-size: 11px; color: #64748b; width: 34px; flex-shrink: 0; }}
  .pbar-track {{ flex: 1; height: 16px; display: flex; border-radius: 4px; overflow: hidden; background: #0f1117; }}
  .pbar-seg {{ height: 100%; opacity: 0.85; transition: opacity 0.2s; }}
  .pbar-seg:hover {{ opacity: 1; }}
  .pbar-total {{ font-size: 11px; color: #94a3b8; width: 46px; text-align: right; }}

  /* 供应链流程 */
  .chain-flow {{ display: flex; align-items: flex-start; gap: 0; overflow-x: auto; padding-bottom: 8px; }}
  .chain-block {{ background: #0f1117; border-radius: 10px; padding: 12px 14px; min-width: 200px; flex: 1; }}
  .chain-header {{ font-size: 12px; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }}
  .chain-arrow {{ font-size: 22px; color: #334155; padding: 0 6px; align-self: center; flex-shrink: 0; }}
  .chain-players {{ display: flex; flex-direction: column; gap: 6px; }}
  .player {{ display: flex; flex-direction: column; gap: 2px; padding: 7px 9px; background: #1a1f2e; border-radius: 6px; }}
  .player-name {{ font-size: 11px; font-weight: 600; color: #e2e8f0; }}
  .player-detail {{ font-size: 10px; color: #64748b; line-height: 1.5; }}

  /* 需求拆解 */
  .demand-list {{ display: flex; flex-direction: column; gap: 12px; }}
  .demand-row {{ display: flex; flex-direction: column; gap: 5px; }}
  .demand-label {{ font-size: 12px; font-weight: 700; color: #e2e8f0; }}
  .demand-bar {{
    padding: 5px 10px; border-radius: 6px; display: inline-flex; align-items: center;
    gap: 6px; min-width: 100px; font-size: 13px;
  }}
  .demand-detail {{ padding: 7px 10px; background: #0f1117; border-radius: 6px; }}
  .demand-form {{
    display: inline-block; padding: 2px 7px; border-radius: 4px;
    font-size: 10px; font-weight: 600; margin-bottom: 5px;
  }}
  .demand-users {{ font-size: 11px; color: #94a3b8; margin-bottom: 3px; }}
  .demand-sub {{ font-size: 11px; color: #64748b; line-height: 1.6; }}

  /* 替代材料 & 钌粉表 */
  .sub-table, .powder-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .sub-table th, .powder-table th {{
    text-align: left; padding: 7px 10px; color: #64748b;
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    border-bottom: 1px solid #2d3748;
  }}
  .sub-table td, .powder-table td {{ padding: 9px 10px; border-bottom: 1px solid #1e2535; vertical-align: top; }}
  .sub-table tr:last-child td, .powder-table tr:last-child td {{ border-bottom: none; }}
  .risk-badge {{ padding: 2px 7px; border-radius: 4px; font-size: 10px; font-weight: 700; }}

  /* 钌粉表专属 */
  .p-date {{ color: #475569; font-size: 11px; white-space: nowrap; }}
  .p-name {{ color: #e2e8f0; }}
  .p-price {{ font-weight: 700; color: #e8a838; white-space: nowrap; }}
  .p-unit {{ font-size: 10px; color: #64748b; font-weight: 400; }}
  .p-supplier {{ color: #64748b; font-size: 11px; }}

  /* 五年展望 */
  .outlook-meta {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }}
  .outlook-meta-item {{ background: #0f1117; border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 130px; }}
  .outlook-meta-item.verdict {{ border: 1px solid #f8717122; }}
  .oml {{ font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; }}
  .omv {{ font-size: 15px; font-weight: 700; color: #e2e8f0; }}
  .phases {{ display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }}
  .phase-item {{ padding: 10px 14px; background: #0f1117; border-radius: 8px; }}
  .phase-period {{ font-size: 11px; font-weight: 800; margin-bottom: 2px; }}
  .phase-label {{ font-size: 12px; font-weight: 700; color: #e2e8f0; margin-bottom: 4px; }}
  .phase-body {{ font-size: 11px; color: #94a3b8; line-height: 1.65; }}
  .risks-block {{ background: #0f1117; border-radius: 8px; padding: 12px 14px; }}
  .risks-title {{ font-size: 10px; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }}
  .risks-list {{ list-style: none; display: flex; flex-direction: column; gap: 5px; }}
  .risks-list li {{ font-size: 11px; color: #94a3b8; padding-left: 14px; position: relative; line-height: 1.5; }}
  .risks-list li::before {{ content: "⚡"; position: absolute; left: 0; }}

  /* ── 近 30 天表格 ──────────────────────────────────────────────── */
  .table-section {{
    background: #1a1f2e;
    border-radius: 12px;
    overflow: hidden;
  }}
  .table-header {{
    padding: 14px 18px;
    font-size: 13px;
    color: #94a3b8;
    border-bottom: 1px solid #2d3748;
  }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 7px 18px; font-size: 12px; }}
  tr:hover td {{ background: #1e2535; }}
  .date-row td {{
    padding: 10px 18px 4px;
    font-size: 10px;
    color: #475569;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    background: transparent !important;
  }}

  .generated {{ text-align: center; color: #334155; font-size: 10px; padding: 16px 0 24px; }}

  /* ── 预警区块 ──────────────────────────────────────────────────── */
  .alert-bar {{
    border-radius: 10px;
    padding: 12px 18px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .alert-bar--clear {{
    background: #1a1f2e;
    border-left: 4px solid #334155;
    flex-direction: row;
    align-items: center;
    gap: 10px;
  }}
  .alert-bar--warn {{
    background: #1e1515;
    border-left: 4px solid #f87171;
  }}
  .alert-bar-top {{ display: flex; align-items: center; gap: 10px; }}
  .alert-bar-icon {{ font-size: 16px; flex-shrink: 0; }}
  .alert-bar-text {{ font-size: 13px; font-weight: 700; color: #e2e8f0; }}
  .alert-bar--clear .alert-bar-text {{ color: #64748b; }}
  .alert-items {{ display: flex; flex-direction: column; gap: 8px; padding-top: 4px; }}
  .alert-item {{
    background: #261a1a;
    border-radius: 8px;
    padding: 10px 14px;
  }}
  .alert-item-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 5px;
  }}
  .alert-badge {{
    background: #f8717122;
    color: #f87171;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 4px;
    white-space: nowrap;
  }}
  .alert-cond {{ font-size: 12px; font-weight: 600; color: #fca5a5; }}
  .alert-note {{ font-size: 11px; color: #94a3b8; line-height: 1.6; }}

  /* ── 品牌 / 按钮 ──────────────────────────────────────────────── */
  .brand-en {{ font-size: 13px; font-weight: 600; color: #64748b; letter-spacing: 0.04em; }}
  .header-cta {{
    margin-left: 8px; align-self: center; background: #e8a838; color: #0f1117;
    font-size: 12px; font-weight: 700; padding: 8px 16px; border-radius: 8px;
    text-decoration: none; white-space: nowrap;
  }}
  .header-cta:hover {{ background: #f0b94e; }}
  .toc-link--cta {{ color: #e8a838; font-weight: 700; }}
  .btn {{
    display: inline-flex; align-items: center; gap: 6px; font: inherit; font-size: 13px;
    font-weight: 600; border: none; border-radius: 9px; padding: 11px 20px; cursor: pointer;
    text-decoration: none; transition: all .15s;
  }}
  .btn-primary {{ background: #e8a838; color: #0f1117; }}
  .btn-primary:hover {{ background: #f0b94e; transform: translateY(-1px); }}
  .btn-ghost {{ background: transparent; color: #e2e8f0; border: 1px solid #2d3748; }}
  .btn-ghost:hover {{ border-color: #e8a838; color: #e8a838; }}
  .btn-chat {{ background: #16324a; color: #7dd3fc; border: 1px solid #1e4a6b; }}
  .btn-chat:hover {{ background: #1b3d59; }}

  /* ── 产品首屏 Hero ────────────────────────────────────────────── */
  .hero {{
    background: linear-gradient(135deg, #171c2b 0%, #1a1f2e 55%, #1c2438 100%);
    border: 1px solid #232a3a; border-radius: 16px; padding: 34px 32px; margin-bottom: 18px;
  }}
  .hero-eyebrow {{ font-size: 11px; letter-spacing: .14em; color: #e8a838; font-weight: 700; text-transform: uppercase; }}
  .hero-title {{ font-size: 30px; font-weight: 800; color: #f1f5f9; margin: 10px 0 6px; letter-spacing: .02em; }}
  .hero-tagline {{ font-size: 15px; color: #cbd5e1; font-weight: 600; }}
  .hero-sub {{ font-size: 13px; color: #94a3b8; line-height: 1.8; margin-top: 10px; max-width: 780px; }}
  .hero-sub strong {{ color: #e8a838; }}
  .hero-stats {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 20px 0; }}
  .hero-stat {{ display: flex; align-items: baseline; gap: 7px; background: #0f1117; border: 1px solid #232a3a; border-radius: 10px; padding: 11px 16px; }}
  .hs-label {{ font-size: 11px; color: #94a3b8; font-weight: 700; }}
  .hs-val {{ font-size: 20px; font-weight: 800; color: #f1f5f9; }}
  .hs-unit {{ font-size: 10px; color: #475569; }}
  .hero-chg {{ font-size: 11px; font-weight: 700; }}
  .hero-chg.up {{ color: #22c55e; }}
  .hero-chg.down {{ color: #f87171; }}
  .hero-cta {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .hero-trust {{ font-size: 11px; color: #475569; margin-top: 16px; }}

  /* ── 价格变动归因 ─────────────────────────────────────────────── */
  .attr-wrap {{ background: #1a1f2e; border-radius: 12px; padding: 20px 22px; }}
  .attr-intro {{ font-size: 12px; color: #94a3b8; line-height: 1.7; margin: 6px 0 16px; }}
  .evt-list {{ display: flex; flex-direction: column; gap: 10px; }}
  .evt {{ background: #0f1117; border-left: 3px solid #888; border-radius: 0 10px 10px 0; padding: 12px 16px; }}
  .evt-top {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 5px; }}
  .evt-date {{ font-size: 12px; color: #64748b; font-variant-numeric: tabular-nums; }}
  .evt-metal {{ font-size: 13px; font-weight: 800; }}
  .evt-move {{ font-size: 12px; color: #cbd5e1; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .evt-chg {{ font-size: 12px; font-weight: 700; }}
  .evt-chg.up {{ color: #22c55e; }}
  .evt-chg.down {{ color: #f87171; }}
  .evt-dim {{ font-size: 10px; color: #94a3b8; background: #1e2535; border-radius: 5px; padding: 2px 8px; margin-left: auto; }}
  .evt-peak {{ font-size: 10px; color: #0f1117; background: #e8a838; border-radius: 5px; padding: 2px 8px; font-weight: 700; }}
  .evt-tag {{ font-size: 13px; font-weight: 700; color: #e2e8f0; margin-bottom: 4px; }}
  .evt-detail {{ font-size: 12px; color: #94a3b8; line-height: 1.7; }}
  .evt-detail b {{ color: #cbd5e1; }}

  /* ── 订阅 · 预约 · 套餐 ───────────────────────────────────────── */
  .cta-wrap {{ background: #1a1f2e; border-radius: 12px; padding: 20px 22px; }}
  .cta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin: 8px 0 18px; }}
  .cta-card {{ background: #0f1117; border: 1px solid #232a3a; border-radius: 12px; padding: 18px 20px; }}
  .cta-card-title {{ font-size: 15px; font-weight: 700; color: #f1f5f9; margin-bottom: 6px; }}
  .cta-card-sub {{ font-size: 12px; color: #94a3b8; line-height: 1.7; margin-bottom: 12px; }}
  .cta-list {{ list-style: none; margin: 0 0 14px; padding: 0; }}
  .cta-list li {{ font-size: 12px; color: #cbd5e1; padding: 4px 0 4px 20px; position: relative; }}
  .cta-list li::before {{ content: "✓"; color: #22c55e; position: absolute; left: 0; font-weight: 700; }}
  .sub-form {{ display: flex; gap: 8px; }}
  .sub-input {{ flex: 1; background: #1a1f2e; border: 1px solid #2d3748; border-radius: 9px; padding: 10px 12px; color: #e2e8f0; font: inherit; font-size: 13px; }}
  .sub-input:focus {{ outline: none; border-color: #e8a838; }}
  .plans {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
  .plan {{ background: #0f1117; border: 1px solid #232a3a; border-radius: 12px; padding: 18px; text-align: center; position: relative; }}
  .plan--featured {{ border-color: #e8a838; }}
  .plan-badge {{ position: absolute; top: -9px; left: 50%; transform: translateX(-50%); background: #e8a838; color: #0f1117; font-size: 10px; font-weight: 700; padding: 3px 10px; border-radius: 6px; white-space: nowrap; }}
  .plan-name {{ font-size: 14px; font-weight: 700; color: #f1f5f9; }}
  .plan-price {{ font-size: 20px; font-weight: 800; color: #e8a838; margin: 6px 0 12px; }}
  .plan-feat {{ list-style: none; margin: 0 0 14px; padding: 0; }}
  .plan-feat li {{ font-size: 11px; color: #94a3b8; padding: 4px 0; border-bottom: 1px solid #161b27; }}

  /* ── 对话式行情助手 ──────────────────────────────────────────── */
  .qa-launcher {{
    position: fixed; right: 22px; bottom: 22px; z-index: 60; background: #e8a838; color: #0f1117;
    border: none; border-radius: 30px; padding: 13px 20px; font: inherit; font-size: 14px;
    font-weight: 700; cursor: pointer; box-shadow: 0 8px 24px rgba(0,0,0,.4); transition: all .15s;
  }}
  .qa-launcher:hover {{ transform: translateY(-2px); box-shadow: 0 10px 28px rgba(232,168,56,.4); }}
  .qa-launcher.hidden {{ display: none; }}
  .qa-panel {{
    position: fixed; right: 22px; bottom: 22px; z-index: 61; width: 360px;
    max-width: calc(100vw - 32px); height: 520px; max-height: calc(100vh - 44px);
    background: #161b27; border: 1px solid #2d3748; border-radius: 16px;
    box-shadow: 0 16px 48px rgba(0,0,0,.5); display: none; flex-direction: column; overflow: hidden;
  }}
  .qa-panel.open {{ display: flex; }}
  .qa-head {{ display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; background: #1a2030; border-bottom: 1px solid #232a3a; }}
  .qa-title {{ font-size: 14px; font-weight: 700; color: #f1f5f9; }}
  .qa-close {{ background: none; border: none; color: #64748b; font-size: 16px; cursor: pointer; }}
  .qa-close:hover {{ color: #e2e8f0; }}
  .qa-body {{ flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }}
  .qa-msg {{ font-size: 13px; line-height: 1.7; padding: 10px 13px; border-radius: 12px; max-width: 88%; }}
  .qa-msg.bot {{ background: #1e2535; color: #e2e8f0; align-self: flex-start; border-bottom-left-radius: 4px; }}
  .qa-msg.user {{ background: #e8a838; color: #0f1117; align-self: flex-end; font-weight: 600; border-bottom-right-radius: 4px; }}
  .qa-msg.typing {{ color: #64748b; font-style: italic; }}
  .qa-msg b {{ color: #e8a838; }}
  .qa-msg.user b {{ color: #0f1117; }}
  .qa-chips {{ display: flex; flex-wrap: wrap; gap: 7px; padding: 12px; border-top: 1px solid #232a3a; background: #12151f; max-height: 150px; overflow-y: auto; }}
  .qa-chip {{ font: inherit; font-size: 12px; color: #cbd5e1; background: #1e2535; border: 1px solid #2d3748; border-radius: 16px; padding: 6px 12px; cursor: pointer; transition: all .15s; }}
  .qa-chip:hover {{ border-color: #e8a838; color: #e8a838; }}

  /* ── 响应式 ─────────────────────────────────────────────────────── */
  @media (max-width: 900px) {{
    .page-layout {{ grid-template-columns: 1fr; }}
    .sidebar {{ position: static; height: auto; border-right: none; border-bottom: 1px solid #1e2535; flex-direction: row; flex-wrap: wrap; padding: 12px 16px; }}
    .toc-header, .toc-section-label, .toc-divider {{ display: none; }}
    .src-legend {{ margin-top: 0; padding-top: 0; border-top: none; }}
    .toc-link {{ padding: 4px 8px; font-size: 11px; }}
  }}
  @media (max-width: 760px) {{
    .cta-grid {{ grid-template-columns: 1fr; }}
    .plans {{ grid-template-columns: 1fr; }}
    .hero-title {{ font-size: 24px; }}
    .hero {{ padding: 24px 20px; }}
    .qa-panel {{ width: calc(100vw - 24px); right: 12px; bottom: 12px; }}
    .qa-launcher {{ right: 12px; bottom: 12px; }}
  }}
</style>
</head>
<body>

<header>
  <h1>{BRAND['name_cn']} <span class="brand-en">{BRAND['name']}</span></h1>
  <span class="subtitle">钌 Ru · 铱 Ir · 钌粉 · USD/troy oz · CNY/g</span>
  <div class="src-tags">
    <span class="src-tag active">Umicore</span>
    <span class="src-tag">Johnson Matthey</span>
    <span class="src-tag">BASF</span>
    <span class="src-tag">91金属网</span>
  </div>
  <a class="header-cta" href="#subscribe">订阅 / 预约</a>
</header>

<div class="page-layout">

  <!-- ── 侧边栏 ── -->
  <aside class="sidebar" aria-label="目录导航">
    <div class="toc-header">Contents</div>

    <a class="toc-link active" href="#hero">
      <span class="toc-icon">🏠</span>概览
    </a>
    <a class="toc-link" href="#alerts">
      <span class="toc-icon">⚠️</span>今日预警
    </a>
    <a class="toc-link" href="#prices">
      <span class="toc-icon">💰</span>实时价格
    </a>
    <a class="toc-link" href="#chart">
      <span class="toc-icon">📈</span>历史走势图
    </a>

    <div class="toc-divider"></div>
    <div class="toc-section-label">分析</div>

    <a class="toc-link" href="#attribution">
      <span class="toc-icon">🧭</span>价格变动归因
    </a>
    <a class="toc-link" href="#analysis">
      <span class="toc-icon">🔍</span>价格变化分析
    </a>
    <a class="toc-link" href="#supply-chain">
      <span class="toc-icon">🗺</span>产业链地图
    </a>

    <div class="toc-divider"></div>
    <div class="toc-section-label">数据</div>

    <a class="toc-link" href="#powder">
      <span class="toc-icon">🔬</span>钌粉市场
    </a>
    <a class="toc-link" href="#history">
      <span class="toc-icon">🗓</span>近 30 天记录
    </a>

    <div class="toc-divider"></div>
    <a class="toc-link toc-link--cta" href="#subscribe">
      <span class="toc-icon">📬</span>订阅 · 预约
    </a>

    <!-- 数据源图例 -->
    <div class="src-legend">
      <div class="src-legend-title">数据来源</div>
      <div class="src-item">
        <div class="src-dot" style="background:#e8a838;height:2px;width:20px;"></div>
        <span>Umicore（钌）</span>
      </div>
      <div class="src-item">
        <div class="src-dot" style="background:#f97316;height:2px;width:20px;border-top:2px dashed #f97316;background:none;"></div>
        <span>Johnson Matthey</span>
      </div>
      <div class="src-item">
        <div class="src-dot" style="background:#a78bfa;height:2px;width:20px;border-top:2px dashed #a78bfa;background:none;"></div>
        <span>BASF</span>
      </div>
      <div class="src-item">
        <div class="src-dot" style="background:#4f8ef7;height:2px;width:20px;"></div>
        <span>Umicore（铱）</span>
      </div>
    </div>
  </aside>

  <!-- ── 主内容区 ── -->
  <main class="main-content">

    <!-- 产品首屏 -->
    {build_hero_html(analysis, powder)}

    <!-- 今日预警 -->
    {build_alert_section(today_alerts or [])}

    <!-- 价格卡片 -->
    <section id="prices">
      <div class="cards">{cards_html}</div>
    </section>

    <!-- 历史图表 -->
    <section id="chart" class="chart-section">
      <div class="chart-head">
        <div class="chart-title">历史价格走势 · 多数据源对比（实线=Umicore，虚线=JM/BASF）</div>
        <div class="range-tabs" id="rangeTabs">
          <button class="range-btn active" data-range="天">天</button>
          <button class="range-btn" data-range="周">周</button>
          <button class="range-btn" data-range="月">月</button>
          <button class="range-btn" data-range="季度">季度</button>
        </div>
      </div>
      <canvas id="priceChart" height="80"></canvas>
      <div class="range-hint" id="rangeHint"></div>
    </section>

    <!-- 价格变动归因 -->
    <section id="attribution">
      {build_attribution_html()}
    </section>

    <!-- 价格分析 -->
    <section id="analysis">
      {build_analysis_html(analysis)}
    </section>

    <!-- 产业链地图 -->
    <section id="supply-chain">
      {build_supply_chain_html()}
    </section>

    <!-- 钌粉市场 -->
    {build_powder_section(powder)}

    <!-- 近 30 天记录 -->
    <section id="history">
      <div class="table-section">
        <div class="table-header">近 30 天记录（Umicore）</div>
        <table>
          <tbody>{table_rows}</tbody>
        </table>
      </div>
    </section>

    <!-- 订阅 · 预约 -->
    <section id="subscribe">
      {build_cta_html()}
    </section>

    <div class="generated">{BRAND['name_cn']} · {BRAND['name']}　|　生成于 {today}　|　数据源 Umicore / JM / BASF / 91金属网</div>

  </main>
</div>

<!-- 对话式行情助手 -->
{build_qa_assistant_html(analysis, powder)}

<script>
const datasets = {datasets_json};

// 计算数据的时间范围（以最新数据日为基准，而非浏览器当前时间）
let maxT = 0, minT = Infinity;
datasets.forEach(ds => ds.data.forEach(p => {{
  const t = new Date(p.x).getTime();
  if (t > maxT) maxT = t;
  if (t < minT) minT = t;
}}));
const DAY_MS = 86400000;
// 四个时间维度：窗口天数 + x 轴刻度单位
const RANGES = {{
  '天':   {{ days: 30,   unit: 'day'     }},
  '周':   {{ days: 90,   unit: 'week'    }},
  '月':   {{ days: 365,  unit: 'month'   }},
  '季度': {{ days: null, unit: 'quarter' }},
}};
const DEFAULT_RANGE = '天';
const _ir = RANGES[DEFAULT_RANGE];
const _initMin = _ir.days ? Math.max(minT, maxT - _ir.days * DAY_MS) : minT;

const priceChart = new Chart(document.getElementById('priceChart'), {{
  type: 'line',
  data: {{ datasets }},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{
        labels: {{ color: '#94a3b8', font: {{ size: 11 }}, boxWidth: 24 }}
      }},
      tooltip: {{
        backgroundColor: '#1e2535',
        titleColor: '#94a3b8',
        bodyColor: '#e2e8f0',
        callbacks: {{
          label: ctx => ` ${{ctx.dataset.label}}: $${{ctx.parsed.y.toLocaleString()}} / troy oz`
        }}
      }}
    }},
    scales: {{
      x: {{
        type: 'time',
        min: _initMin,
        max: maxT,
        time: {{ unit: _ir.unit, tooltipFormat: 'yyyy-MM-dd' }},
        ticks: {{ color: '#64748b', maxTicksLimit: 12, autoSkip: true }},
        grid: {{ color: '#1e2535' }}
      }},
      yRu: {{
        position: 'left',
        ticks: {{
          color: '#e8a838',
          callback: v => '$' + v.toLocaleString()
        }},
        grid: {{ color: '#1e2535' }},
        title: {{ display: true, text: '钌 USD/troy oz', color: '#e8a838', font: {{ size: 10 }} }}
      }},
      yIr: {{
        position: 'right',
        ticks: {{
          color: '#4f8ef7',
          callback: v => '$' + v.toLocaleString()
        }},
        grid: {{ drawOnChartArea: false }},
        title: {{ display: true, text: '铱 USD/troy oz', color: '#4f8ef7', font: {{ size: 10 }} }}
      }}
    }}
  }}
}});

/* 时间维度切换：天 / 周 / 月 / 季度 */
function _fmtDate(ms) {{
  const d = new Date(ms);
  return d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0');
}}
function applyRange(key) {{
  const r = RANGES[key] || RANGES[DEFAULT_RANGE];
  const cutoff = r.days ? Math.max(minT, maxT - r.days * DAY_MS) : minT;
  priceChart.options.scales.x.min = cutoff;
  priceChart.options.scales.x.max = maxT;
  priceChart.options.scales.x.time.unit = r.unit;
  priceChart.update();
  document.querySelectorAll('.range-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.range === key));
  const hint = document.getElementById('rangeHint');
  if (hint) hint.textContent = '显示区间：' + _fmtDate(cutoff) + '  ~  ' + _fmtDate(maxT);
}}
document.querySelectorAll('.range-btn').forEach(b =>
  b.addEventListener('click', () => applyRange(b.dataset.range)));
applyRange(DEFAULT_RANGE);

/* ── 对话式行情助手 ── */
const qaData = JSON.parse(document.getElementById('qaData').textContent);
function toggleQA() {{
  const p = document.getElementById('qaPanel');
  const open = p.classList.toggle('open');
  p.setAttribute('aria-hidden', open ? 'false' : 'true');
  document.getElementById('qaLauncher').classList.toggle('hidden', open);
}}
function openQA() {{
  const p = document.getElementById('qaPanel');
  if (!p.classList.contains('open')) toggleQA();
}}
function qaAsk(i) {{
  const item = qaData[i];
  if (!item) return;
  const body = document.getElementById('qaBody');
  const u = document.createElement('div');
  u.className = 'qa-msg user'; u.textContent = item.q; body.appendChild(u);
  const t = document.createElement('div');
  t.className = 'qa-msg bot typing'; t.textContent = '正在分析…'; body.appendChild(t);
  body.scrollTop = body.scrollHeight;
  setTimeout(() => {{
    t.classList.remove('typing'); t.innerHTML = item.a;
    body.scrollTop = body.scrollHeight;
  }}, 450);
}}
document.querySelectorAll('.qa-chip').forEach(c =>
  c.addEventListener('click', () => qaAsk(parseInt(c.dataset.qi))));

/* ── 订阅（静态站点用 mailto 收集）── */
function submitSubscribe() {{
  const inp = document.getElementById('subEmail');
  const email = inp ? inp.value.trim() : '';
  const subj = encodeURIComponent('订阅每日钌铱行情 — PGM Insight');
  const body = encodeURIComponent('我想订阅每日钌/铱/钌粉行情与归因摘要。\\n我的邮箱：' + email);
  window.location.href = 'mailto:{BRAND['email']}?subject=' + subj + '&body=' + body;
}}

/* 侧边栏高亮当前节 */
const tocLinks = document.querySelectorAll('.toc-link');
const sections = document.querySelectorAll('section[id], div[id]');
const observer = new IntersectionObserver(entries => {{
  entries.forEach(e => {{
    if (e.isIntersecting) {{
      tocLinks.forEach(a => a.classList.remove('active'));
      const active = document.querySelector(`.toc-link[href="#${{e.target.id}}"]`);
      if (active) active.classList.add('active');
    }}
  }});
}}, {{ rootMargin: '-20% 0px -70% 0px' }});
sections.forEach(s => observer.observe(s));
</script>
</body>
</html>"""


def _build_inline_scripts() -> str:
    """将 Chart.js 文件内容内嵌为 <script> 标签，生成完全离线的单文件。"""
    parts = []
    for path, cdn in zip(CHARTJS_FILES, CHARTJS_CDNS):
        if path.exists():
            js = path.read_text(encoding="utf-8")
            print(f"  内嵌：{path.name}（{len(js)//1024} KB）")
        else:
            print(f"  本地文件不存在，从 CDN 下载：{cdn}")
            import urllib.request
            js = urllib.request.urlopen(cdn, timeout=20).read().decode("utf-8")
            path.write_text(js, encoding="utf-8")
        parts.append(f"<script>{js}</script>")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="生成贵金属价格仪表盘")
    parser.add_argument("--offline", action="store_true",
                        help="生成离线单文件版（嵌入 Chart.js，可脱机使用）")
    parser.add_argument("--no-open", action="store_true", help="生成后不自动打开浏览器")
    args = parser.parse_args()

    print("读取数据库...")
    rows, latest, recent, analysis, source_compare, powder, today_alerts = load_data()
    print(f"  共 {len(rows)} 条价格记录，{len(powder)} 条钌粉报价，{len(today_alerts)} 条今日预警")

    # 决定使用 CDN 还是内嵌脚本
    if args.offline:
        scripts = _build_inline_scripts()
        out_path = OUTPUT_OFF
        mode = "离线单文件"
    else:
        scripts = (
            '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>'
        )
        out_path = OUTPUT
        mode = "在线版"

    print(f"生成 HTML 仪表盘（{mode}）...")
    html = build_html(rows, latest, recent, analysis, source_compare, powder, today_alerts).replace("{chartjs_scripts}", scripts)
    out_path.write_text(html, encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    print(f"  已生成：{out_path}（{size_kb} KB）")

    if not args.no_open:
        print("在浏览器中打开...")
        if sys.platform == "darwin":
            subprocess.run(["open", str(out_path)])
        elif sys.platform == "win32":
            os.startfile(str(out_path))
        else:
            subprocess.run(["xdg-open", str(out_path)])


if __name__ == "__main__":
    main()
