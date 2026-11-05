from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.parse import quote

from forthatday_agent.infrastructure.catalog_store import (
    CatalogAnalyticsSummary,
    CatalogAppointmentStyleAvailability,
    CatalogCalendarAppointment,
    CatalogDashboardSummary,
    CatalogInternalProduct,
    CatalogPlanningRecommendation,
    CatalogPlanningSummary,
    CatalogUnitBlock,
)


CHINA_TIMEZONE = timezone(timedelta(hours=8))


def render_inventory_dashboard(
    summary: CatalogDashboardSummary,
    *,
    staff_id: str = "",
) -> bytes:
    updated_at = datetime.now(CHINA_TIMEZONE).strftime("%m月%d日 %H:%M")
    staff_label = escape(staff_id or "预览模式")
    maintenance = summary.maintenance_units
    attention_total = summary.products_without_units + summary.pending_drafts
    readiness = 0 if summary.products == 0 else round(
        (summary.products - summary.products_without_units) / summary.products * 100
    )

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>库存总览 · Forthatday</title>
  <style>
    :root {{
      --canvas: #f4f6f4;
      --surface: #ffffff;
      --ink: #18211d;
      --muted: #66716b;
      --line: #d9dfdc;
      --spruce: #285846;
      --blue: #3b6f8e;
      --amber: #b67a2f;
      --coral: #c5534c;
      --radius: 8px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--canvas); color: var(--ink); font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif; }}
    body {{ margin: 0; min-width: 320px; }}
    a {{ color: inherit; }}
    .app {{ min-height: 100vh; display: grid; grid-template-columns: 224px minmax(0, 1fr); }}
    .sidebar {{ position: sticky; top: 0; height: 100vh; padding: 24px 18px; background: #1d2c26; color: #f7faf8; display: flex; flex-direction: column; }}
    .brand {{ height: 42px; display: flex; align-items: center; gap: 11px; margin-bottom: 34px; }}
    .brand-mark {{ width: 32px; height: 32px; display: grid; place-items: center; border: 1px solid #688075; border-radius: 6px; font: 700 11px/1 "Cascadia Mono", monospace; }}
    .brand-name {{ font-size: 15px; font-weight: 700; }}
    .brand-sub {{ margin-top: 2px; color: #aebdb6; font-size: 11px; }}
    .nav-label {{ margin: 0 10px 8px; color: #8da097; font-size: 11px; }}
    .nav-item {{ min-height: 42px; display: flex; align-items: center; justify-content: space-between; padding: 0 12px; margin-bottom: 4px; border-radius: 6px; color: #b9c6c0; font-size: 13px; text-decoration: none; }}
    .nav-item.active {{ background: #eff5f1; color: var(--ink); font-weight: 700; }}
    .nav-item.muted {{ color: #74867d; }}
    .nav-dot {{ width: 6px; height: 6px; border-radius: 50%; background: var(--spruce); }}
    .sidebar-foot {{ margin-top: auto; padding: 14px 10px 0; border-top: 1px solid #34473e; color: #aebdb6; font-size: 11px; line-height: 1.7; }}
    .sidebar-foot a {{ display: inline-block; margin-top: 6px; color: #d9e3de; }}
    .content {{ min-width: 0; padding-bottom: 44px; }}
    .topbar {{ height: 72px; padding: 0 clamp(22px, 4vw, 52px); display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); background: rgba(244, 246, 244, .96); }}
    .crumb {{ color: var(--muted); font-size: 12px; }}
    .staff {{ display: flex; align-items: center; gap: 9px; font-size: 12px; color: var(--muted); }}
    .staff-badge {{ width: 28px; height: 28px; display: grid; place-items: center; border-radius: 50%; background: var(--spruce); color: white; font-weight: 700; }}
    main {{ width: min(1180px, calc(100% - 44px)); margin: 0 auto; }}
    .page-head {{ padding: 34px 0 24px; display: flex; align-items: end; justify-content: space-between; gap: 20px; }}
    h1 {{ margin: 0; font-size: clamp(24px, 3vw, 34px); line-height: 1.2; letter-spacing: 0; }}
    .page-kicker {{ margin: 7px 0 0; color: var(--muted); font-size: 13px; }}
    .updated {{ color: var(--muted); font-size: 11px; white-space: nowrap; }}
    .metric-strip {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); overflow: hidden; }}
    .metric {{ min-height: 112px; padding: 21px 22px; border-right: 1px solid var(--line); }}
    .metric:last-child {{ border-right: 0; }}
    .metric-label {{ color: var(--muted); font-size: 12px; }}
    .metric-value {{ margin-top: 9px; font: 600 28px/1 "Cascadia Mono", "Microsoft YaHei", monospace; }}
    .metric-note {{ margin-top: 9px; color: var(--muted); font-size: 11px; }}
    .metric.attention .metric-value {{ color: var(--coral); }}
    .section {{ margin-top: 28px; }}
    .section-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }}
    h2 {{ margin: 0; font-size: 15px; letter-spacing: 0; }}
    .section-note {{ color: var(--muted); font-size: 11px; }}
    .rail {{ position: relative; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 0; padding: 25px 12px 22px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); overflow: hidden; }}
    .rail::before {{ content: ""; position: absolute; top: 46px; left: 10%; right: 10%; height: 2px; background: var(--line); }}
    .rail-stop {{ position: relative; z-index: 1; text-align: center; }}
    .rail-node {{ width: 44px; height: 44px; margin: 0 auto 11px; display: grid; place-items: center; border: 2px solid var(--line); border-radius: 50%; background: var(--surface); font: 700 13px/1 "Cascadia Mono", monospace; }}
    .rail-stop:first-child .rail-node {{ border-color: var(--amber); color: var(--amber); background: #fffaf2; }}
    .rail-name {{ font-size: 12px; font-weight: 700; }}
    .rail-sub {{ margin-top: 4px; color: var(--muted); font-size: 10px; }}
    .workspace-grid {{ display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(300px, .75fr); gap: 28px; align-items: start; }}
    .panel {{ border-top: 1px solid var(--ink); }}
    .task {{ min-height: 74px; display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; gap: 13px; align-items: center; border-bottom: 1px solid var(--line); }}
    .task-index {{ color: var(--muted); font: 500 11px/1 "Cascadia Mono", monospace; }}
    .task-title {{ font-size: 13px; font-weight: 700; }}
    .task-note {{ margin-top: 4px; color: var(--muted); font-size: 11px; }}
    .task-count {{ min-width: 52px; text-align: right; font: 600 14px/1 "Cascadia Mono", monospace; color: var(--coral); }}
    .breakdown {{ border-top: 1px solid var(--ink); }}
    .breakdown-row {{ min-height: 52px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); font-size: 12px; }}
    .breakdown-row strong {{ font: 600 13px/1 "Cascadia Mono", monospace; }}
    .progress {{ height: 5px; margin-top: 16px; border-radius: 3px; background: #dce3df; overflow: hidden; }}
    .progress > span {{ display: block; height: 100%; width: {readiness}%; background: var(--spruce); }}
    .progress-meta {{ display: flex; justify-content: space-between; margin-top: 8px; color: var(--muted); font-size: 10px; }}
    .mobile-nav {{ display: none; }}
    :focus-visible {{ outline: 3px solid rgba(59, 111, 142, .4); outline-offset: 2px; }}
    @media (max-width: 840px) {{
      .app {{ display: block; }}
      .sidebar {{ display: none; }}
      .content {{ padding-bottom: 84px; }}
      .topbar {{ height: 58px; padding: 0 18px; }}
      main {{ width: min(100% - 28px, 680px); }}
      .page-head {{ padding: 24px 0 18px; align-items: start; }}
      .updated {{ display: none; }}
      .metric-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .metric {{ min-height: 100px; border-bottom: 1px solid var(--line); }}
      .metric:nth-child(2) {{ border-right: 0; }}
      .metric:nth-child(3), .metric:nth-child(4) {{ border-bottom: 0; }}
      .rail {{ padding-inline: 3px; }}
      .rail::before {{ left: 9%; right: 9%; }}
      .rail-node {{ width: 38px; height: 38px; }}
      .rail-name {{ font-size: 10px; }}
      .rail-sub {{ display: none; }}
      .workspace-grid {{ grid-template-columns: 1fr; gap: 26px; }}
      .mobile-nav {{ position: fixed; z-index: 10; left: 0; right: 0; bottom: 0; height: 64px; display: grid; grid-template-columns: repeat(7, 1fr); border-top: 1px solid var(--line); background: rgba(255, 255,255, .97); }}
      .mobile-nav a, .mobile-nav span {{ display: grid; place-items: center; color: #8b948f; font-size: 11px; text-decoration: none; }}
      .mobile-nav .active {{ color: var(--spruce); font-weight: 700; }}
    }}
    @media (max-width: 430px) {{
      h1 {{ font-size: 25px; }}
      .page-kicker {{ max-width: 260px; line-height: 1.6; }}
      .metric {{ padding: 18px 16px; }}
      .metric-value {{ font-size: 25px; }}
      .task {{ grid-template-columns: 24px minmax(0, 1fr) auto; gap: 8px; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; }} }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar" aria-label="主导航">
      <div class="brand"><div class="brand-mark">FTD</div><div><div class="brand-name">Forthatday</div><div class="brand-sub">库存工作台</div></div></div>
      <div class="nav-label">工作区</div>
      <a class="nav-item active" href="/staff/inventory" aria-current="page"><span>总览</span><span class="nav-dot"></span></a>
      <a class="nav-item" href="/staff/inventory/products">款式与实物</a>
      <a class="nav-item" href="/staff/inventory/appointments">试纱预约</a>
      <a class="nav-item" href="/staff/inventory/website">网站预约</a>
      <a class="nav-item" href="/staff/inventory/flow">实物流转</a>
      <a class="nav-item" href="/staff/inventory/analytics">数据分析</a>
      <a class="nav-item" href="/staff/inventory/planning">库存规划</a>
      <div class="sidebar-foot">内部系统<br>当前账号：{staff_label}<br><a href="/staff/logout">退出登录</a></div>
    </aside>
    <div class="content">
      <header class="topbar"><div class="crumb">库存工作台 / 总览</div><div class="staff"><span>{staff_label}</span><span class="staff-badge">店</span></div></header>
      <main>
        <div class="page-head"><div><h1>库存总览</h1><p class="page-kicker">先把款式、实物件和档期整理成同一份可信数据。</p></div><div class="updated">更新于 {updated_at}</div></div>

        <section class="metric-strip" aria-label="库存关键数据">
          <div class="metric"><div class="metric-label">已确认款式</div><div class="metric-value">{summary.products}</div><div class="metric-note">婚纱与轻纱礼服</div></div>
          <div class="metric"><div class="metric-label">实物件</div><div class="metric-value">{summary.inventory_units}</div><div class="metric-note">尺码档案已建立</div></div>
          <div class="metric"><div class="metric-label">已归档图片</div><div class="metric-value">{summary.images}</div><div class="metric-note">用于内部识别</div></div>
          <div class="metric attention"><div class="metric-label">需要整理</div><div class="metric-value">{attention_total}</div><div class="metric-note">款式建档 + 待匹配图片</div></div>
        </section>

        <section class="section" aria-labelledby="rail-title">
          <div class="section-head"><h2 id="rail-title">库存轨道</h2><span class="section-note">实物件录入后，状态会在这里流转</span></div>
          <div class="rail">
            <div class="rail-stop"><div class="rail-node">{summary.products_without_units}</div><div class="rail-name">待建档</div><div class="rail-sub">缺少实物与尺码</div></div>
            <div class="rail-stop"><div class="rail-node">{summary.available_units}</div><div class="rail-name">可用</div><div class="rail-sub">可预约或外借</div></div>
            <div class="rail-stop"><div class="rail-node">{summary.reserved_units}</div><div class="rail-name">已预订</div><div class="rail-sub">已有时间占用</div></div>
            <div class="rail-stop"><div class="rail-node">{maintenance}</div><div class="rail-name">维护中</div><div class="rail-sub">清洗或修改</div></div>
            <div class="rail-stop"><div class="rail-node">{summary.offsite_units}</div><div class="rail-name">外借</div><div class="rail-sub">当前不在店</div></div>
          </div>
        </section>

        <section class="section workspace-grid">
          <div>
            <div class="section-head"><h2>当前待办</h2><span class="section-note">按数据依赖顺序排列</span></div>
            <div class="panel">
              <div class="task"><span class="task-index">01</span><div><div class="task-title">建立实物件与尺码档案</div><div class="task-note">每个款式可对应多件不同尺码的实物</div></div><span class="task-count">{summary.products_without_units} 款</span></div>
              <div class="task"><span class="task-index">02</span><div><div class="task-title">匹配 Piui 新款图片</div><div class="task-note">确认图片对应的准确款号后再进入正式目录</div></div><span class="task-count">{summary.pending_drafts} 组</span></div>
              <div class="task"><span class="task-index">03</span><div><div class="task-title">核对实物状态</div><div class="task-note">可用、预订、清洗、修改与外借状态</div></div><span class="task-count">{summary.unverified_units} 件</span></div>
            </div>
          </div>
          <div>
            <div class="section-head"><h2>资料构成</h2><span class="section-note">已确认目录</span></div>
            <div class="breakdown">
              <div class="breakdown-row"><span>婚纱</span><strong>{summary.bridal_products} 款</strong></div>
              <div class="breakdown-row"><span>轻纱 / 礼服 / 晨袍</span><strong>{summary.lightwear_products} 款</strong></div>
              <div class="breakdown-row"><span>待匹配图片组</span><strong>{summary.pending_drafts} 组</strong></div>
            </div>
            <div class="progress" aria-label="实物件建档完成度"><span></span></div>
            <div class="progress-meta"><span>实物建档完成度</span><span>{readiness}%</span></div>
          </div>
        </section>
      </main>
    </div>
  </div>
  <nav class="mobile-nav" aria-label="移动端主导航"><span class="active">总览</span><a href="/staff/inventory/products">款式</a><a href="/staff/inventory/appointments">预约</a><a href="/staff/inventory/website">网站</a><a href="/staff/inventory/flow">流转</a><a href="/staff/inventory/analytics">分析</a><a href="/staff/inventory/planning">规划</a></nav>
</body>
</html>"""
    return html.encode("utf-8")


def render_inventory_products(
    products: tuple[CatalogInternalProduct, ...],
    *,
    staff_id: str,
    query: str = "",
    catalog_type: str = "",
) -> bytes:
    rows = "".join(_product_row(product) for product in products)
    if not rows:
        rows = '<div class="empty">没有找到符合条件的款式</div>'
    bridal_selected = " selected" if catalog_type == "bridal" else ""
    lightwear_selected = " selected" if catalog_type == "lightwear" else ""
    body = f"""
      <div class="page-head"><div><h1>款式与实物</h1><p>搜索内部款号、品牌或款式名称，进入后录入不同尺码的实物件。</p></div><span class="count">{len(products)} 款</span></div>
      <form class="filters" method="get" action="/staff/inventory/products">
        <label><span>搜索</span><input name="q" value="{escape(query, quote=True)}" placeholder="款号、品牌、款式"></label>
        <label><span>分类</span><select name="type"><option value="">全部</option><option value="bridal"{bridal_selected}>婚纱</option><option value="lightwear"{lightwear_selected}>轻纱 / 礼服 / 晨袍</option></select></label>
        <button type="submit">筛选</button>
      </form>
      <div class="product-list">{rows}</div>
    """
    return _inventory_shell("款式与实物", body, staff_id=staff_id, active="products")


def render_inventory_product_detail(
    product: CatalogInternalProduct,
    *,
    staff_id: str,
    csrf_token: str,
    saved: bool = False,
    error: str = "",
) -> bytes:
    model_code = escape(product.model_code, quote=True)
    model_url = quote(product.model_code, safe="-._~")
    image = f'<img src="/staff/inventory/assets/{model_url}" alt="{escape(product.style_name)}">'
    rental = _money_label(product.rental_price_yuan) if product.rental_available else "不可租"
    purchase = _money_label(product.purchase_price_yuan) if product.purchase_or_custom_available else "不出售"
    units = "".join(_unit_form(unit, csrf_token) for unit in product.units)
    if not units:
        units = '<div class="empty compact">还没有实物件。先录入第一件，系统会自动生成实物编号。</div>'
    notice = '<div class="notice ok">已保存，库存总览已同步更新。</div>' if saved else ""
    if error:
        notice = f'<div class="notice error">{escape(error)}</div>'
    body = f"""
      <div class="breadcrumbs"><a href="/staff/inventory/products">款式与实物</a><span>/</span><strong>{escape(product.model_code)}</strong></div>
      {notice}
      <section class="product-detail">
        <div class="product-image">{image}</div>
        <div class="product-copy"><div class="eyebrow">{escape(product.brand_internal)} · {escape(product.model_code)}</div><h1>{escape(product.style_name or product.model_code)}</h1><p>{escape(product.description or '暂无款式说明')}</p>
          <dl><div><dt>内部租赁价</dt><dd>{rental}</dd></div><div><dt>内部购买价</dt><dd>{purchase}</dd></div><div><dt>分类</dt><dd>{_type_label(product.catalog_type)}</dd></div><div><dt>实物件</dt><dd>{len(product.units)} 件</dd></div></dl>
        </div>
      </section>
      <section class="detail-section"><div class="section-title"><h2>实物件与尺码</h2><span>每件实物独立记录状态和位置</span></div><div class="unit-list">{units}</div></section>
      <section class="detail-section add-unit"><div class="section-title"><h2>录入实物件</h2><span>编号留空会按款号自动生成</span></div>
        <form method="post" action="/staff/inventory/units" class="unit-editor new-unit">
          <input type="hidden" name="csrf" value="{escape(csrf_token, quote=True)}"><input type="hidden" name="model_code" value="{model_code}">
          {_field('unit_code', '实物编号', '', '例如 bau-01-01')}{_field('size_label', '尺码', '', '例如 S / 160')}{_field('bust_cm', '胸围 cm', '', '可稍后补充', 'number')}{_field('waist_cm', '腰围 cm', '', '可稍后补充', 'number')}{_field('hip_cm', '臀围 cm', '', '可稍后补充', 'number')}{_field('dress_length_cm', '衣长 cm', '', '可稍后补充', 'number')}{_field('location', '当前位置', '', '例如 店内 A 区')}
          {_state_select('inventory_state', '库存状态', 'unverified')}{_condition_select('condition_state', '实物情况', 'good')}
          <label class="wide"><span>备注</span><input name="notes" placeholder="瑕疵、修改或特殊说明"></label><button type="submit">保存实物件</button>
        </form>
      </section>
    """
    return _inventory_shell(product.style_name or product.model_code, body, staff_id=staff_id, active="products")


def render_appointment_calendar(
    appointments: tuple[CatalogCalendarAppointment, ...],
    period_start: date,
    *,
    staff_id: str,
    csrf_token: str,
    view: str = "month",
    saved: bool = False,
    error: str = "",
) -> bytes:
    parsed = tuple(item for item in appointments if item.parsed)
    unparsed = tuple(item for item in appointments if not item.parsed)
    if view == "week":
        calendar_content, period_label, previous, following, current = _week_calendar(
            parsed,
            period_start,
        )
        navigation_label = "本周"
    else:
        calendar_content = _month_calendar(parsed, period_start)
        period_label = period_start.strftime("%Y年%m月")
        previous = _shift_month(period_start, -1).isoformat()
        following = _shift_month(period_start, 1).isoformat()
        current = date.today().replace(day=1).isoformat()
        navigation_label = "本月"
    pending = "".join(_unparsed_appointment_row(item) for item in unparsed)
    if not pending:
        pending = '<div class="analysis-empty">没有需要整理时间的预约。</div>'
    notice = '<div class="notice ok">预约已添加。</div>' if saved else ""
    if error:
        notice = f'<div class="notice error">{escape(error)}</div>'
    view_query = f"&view={view}" if view == "week" else ""
    month_active = " active" if view == "month" else ""
    week_active = " active" if view == "week" else ""
    body = f"""
      <div class="page-head calendar-head"><div><h1>试纱预约</h1><p>{period_label}</p></div><div class="calendar-actions"><div class="calendar-zoom" aria-label="日历缩放"><a class="{month_active}" href="/staff/inventory/appointments?date={period_start.isoformat()}">月</a><a class="{week_active}" href="/staff/inventory/appointments?date={period_start.isoformat()}&view=week">周</a></div><button class="add-appointment" type="button" id="open-appointment">新增预约</button><a class="page-cross-link" href="/staff/inventory/flow">查看实物流转</a></div></div>
      {notice}
      <div class="calendar-toolbar"><div><a href="/staff/inventory/appointments?date={previous}{view_query}" aria-label="上一周期">‹</a><a href="/staff/inventory/appointments?date={current}{view_query}">{navigation_label}</a><a href="/staff/inventory/appointments?date={following}{view_query}" aria-label="下一周期">›</a></div><span>{len(parsed)} 个可排期预约 · {len(unparsed)} 个时间待整理</span></div>
      {calendar_content}
      <section class="unparsed-section"><div class="section-title"><h2>时间待整理</h2><span>无法可靠转换为日历时间</span></div><div class="unparsed-list">{pending}</div></section>
      {_manual_appointment_dialog(csrf_token, period_start)}
    """
    return _inventory_shell("试纱预约", body, staff_id=staff_id, active="appointments")


def _week_calendar(
    appointments: tuple[CatalogCalendarAppointment, ...],
    week_start: date,
) -> tuple[str, str, str, str, str]:
    previous = (week_start - timedelta(days=7)).isoformat()
    following = (week_start + timedelta(days=7)).isoformat()
    today = datetime.now(CHINA_TIMEZONE).date()
    current_week = today - timedelta(days=today.weekday())
    day_headers = "".join(
        f'<div class="calendar-day-head{" today" if week_start + timedelta(days=index) == today else ""}"><strong>{(week_start + timedelta(days=index)).strftime("%m/%d")}</strong><span>{_weekday_label(index)}</span></div>'
        for index in range(7)
    )
    lanes = "".join(
        _calendar_day_lane(
            week_start + timedelta(days=index),
            tuple(item for item in appointments if datetime.fromisoformat(item.starts_at).date() == week_start + timedelta(days=index)),
        )
        for index in range(7)
    )
    hours = "".join(f"<span>{hour:02d}:00</span>" for hour in range(9, 20))
    agenda = "".join(
        _agenda_day(
            week_start + timedelta(days=index),
            tuple(item for item in appointments if datetime.fromisoformat(item.starts_at).date() == week_start + timedelta(days=index)),
        )
        for index in range(7)
    )
    content = f"""
      <section class="calendar-week">
        <div class="calendar-week-head"><div></div>{day_headers}</div>
        <div class="calendar-week-body"><div class="calendar-hours">{hours}</div>{lanes}</div>
      </section>
      <section class="calendar-agenda">{agenda}</section>
    """
    label = f"{week_start.strftime('%Y年%m月%d日')} 至 {(week_start + timedelta(days=6)).strftime('%m月%d日')}"
    return content, label, previous, following, current_week.isoformat()


def _month_calendar(
    appointments: tuple[CatalogCalendarAppointment, ...],
    month_start: date,
) -> str:
    days_in_month = monthrange(month_start.year, month_start.month)[1]
    by_day = {
        day: tuple(
            item
            for item in appointments
            if datetime.fromisoformat(item.starts_at).date() == day
        )
        for day in (
            month_start + timedelta(days=index) for index in range(days_in_month)
        )
    }
    leading = month_start.weekday()
    total_cells = ((leading + days_in_month + 6) // 7) * 7
    today = datetime.now(CHINA_TIMEZONE).date()
    cells: list[str] = []
    for index in range(total_cells):
        day_number = index - leading + 1
        if day_number < 1 or day_number > days_in_month:
            cells.append('<div class="month-day outside-month" aria-hidden="true"></div>')
            continue
        day = month_start.replace(day=day_number)
        events = "".join(_month_appointment(item) for item in by_day[day])
        cells.append(
            f'<div class="month-day{" today" if day == today else ""}"><div class="month-date"><strong>{day_number}</strong><span>{len(by_day[day]) or ""}</span></div><div class="month-events">{events}</div></div>'
        )
    weekday_headers = "".join(
        f"<span>{label}</span>" for label in ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    )
    return f'<section class="calendar-month"><div class="month-weekdays">{weekday_headers}</div><div class="month-grid">{"".join(cells)}</div></section>'


def _month_appointment(appointment: CatalogCalendarAppointment) -> str:
    starts = datetime.fromisoformat(appointment.starts_at)
    return f"""
      <a class="month-event" href="/staff/inventory/appointments/{quote(appointment.id, safe='-._~')}" title="查看预约详情">
        <time>{starts.strftime('%H:%M')}</time><strong>{escape(appointment.customer_name or '未填姓名')}</strong><span>{escape(appointment.customer_phone or '未填联系方式')}</span>
      </a>
    """


def _shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def _manual_appointment_dialog(csrf_token: str, period_start: date) -> str:
    default_date = (
        datetime.now(CHINA_TIMEZONE).date()
        if period_start.year == datetime.now(CHINA_TIMEZONE).year
        and period_start.month == datetime.now(CHINA_TIMEZONE).month
        else period_start
    )
    return f"""
      <dialog class="appointment-dialog" id="appointment-dialog">
        <form method="post" action="/staff/inventory/appointments" class="appointment-create">
          <input type="hidden" name="csrf" value="{escape(csrf_token, quote=True)}">
          <div class="dialog-head"><div><h2>新增预约</h2><p>保存后会同时进入月历和周历。</p></div><button type="button" class="dialog-close" id="close-appointment" aria-label="关闭">×</button></div>
          <div class="appointment-fields">
            <label><span>顾客姓名</span><input name="customer_name" required autocomplete="name"></label>
            <label><span>联系方式</span><input name="customer_phone" required autocomplete="tel"></label>
            <label><span>预约日期</span><input type="date" name="appointment_date" value="{default_date.isoformat()}" required></label>
            <label><span>开始时间</span><input type="time" name="appointment_time" min="09:00" max="19:00" step="900" required></label>
            <label class="wide"><span>预约状态</span><select name="status"><option value="time_pending_staff">待确认</option><option value="awaiting_payment">待付款</option><option value="confirmed">已锁定</option></select></label>
          </div>
          <div class="dialog-actions"><button type="button" class="secondary" id="cancel-appointment">取消</button><button type="submit">保存预约</button></div>
        </form>
      </dialog>
      <script>
      (() => {{
        const dialog = document.getElementById('appointment-dialog');
        const open = document.getElementById('open-appointment');
        const close = () => dialog.close();
        open.addEventListener('click', () => dialog.showModal());
        document.getElementById('close-appointment').addEventListener('click', close);
        document.getElementById('cancel-appointment').addEventListener('click', close);
        dialog.addEventListener('click', event => {{ if (event.target === dialog) close(); }});
      }})();
      </script>
    """


def render_appointment_detail(
    appointment: CatalogCalendarAppointment,
    styles: tuple[CatalogAppointmentStyleAvailability, ...],
    *,
    staff_id: str,
    csrf_token: str,
    saved: bool = False,
    error: str = "",
) -> bytes:
    notice = '<div class="notice ok">心仪款式已更新。</div>' if saved else ""
    if error:
        notice = f'<div class="notice error">{escape(error)}</div>'
    style_rows = "".join(_appointment_style_row(item) for item in styles)
    if not style_rows:
        style_rows = '<div class="empty compact">没有匹配的款式。</div>'
    display_time = (
        f"{_display_datetime(appointment.starts_at)} 至 {datetime.fromisoformat(appointment.ends_at).strftime('%H:%M')}"
        if appointment.parsed
        else appointment.time_text or "时间待整理"
    )
    body = f"""
      <div class="breadcrumbs"><a href="/staff/inventory/appointments">试纱预约</a><span>/</span><strong>{escape(appointment.customer_name or '未填写姓名')}</strong></div>
      <div class="appointment-detail-head"><div><span class="appointment-status {_appointment_status_class(appointment.status)}">{_appointment_status_label(appointment.status)}</span><h1>{escape(appointment.customer_name or '未填写姓名')}</h1><p>{escape(display_time)}</p></div><dl><div><dt>联系电话</dt><dd>{escape(appointment.customer_phone or '未填写')}</dd></div><div><dt>心仪款式</dt><dd>{appointment.selected_styles}/6 款</dd></div><div><dt>原始时间</dt><dd>{escape(appointment.time_text or '未填写')}</dd></div></dl></div>
      {notice}
      <section class="detail-section style-selection"><div class="section-title"><h2>心仪款式与在店状态</h2><span id="style-count">已选 {appointment.selected_styles}/6</span></div>
        <label class="style-search"><span>搜索款式</span><input id="style-filter" type="search" placeholder="款式名、品牌或内部款号"></label>
        <form method="post" action="/staff/inventory/appointments/{escape(appointment.id, quote=True)}/styles" id="style-form">
          <input type="hidden" name="csrf" value="{escape(csrf_token, quote=True)}">
          <div class="appointment-style-list">{style_rows}</div>
          <button class="save-styles" type="submit">保存心仪款式</button>
        </form>
      </section>
      <script>
      (() => {{
        const filter = document.getElementById('style-filter');
        const rows = [...document.querySelectorAll('.appointment-style-row')];
        const boxes = [...document.querySelectorAll('input[name^="style_"]')];
        const count = document.getElementById('style-count');
        const update = () => {{
          const selected = boxes.filter(box => box.checked).length;
          count.textContent = `已选 ${{selected}}/6`;
          boxes.forEach(box => box.disabled = !box.checked && selected >= 6);
        }};
        filter.addEventListener('input', () => {{
          const value = filter.value.trim().toLowerCase();
          rows.forEach(row => row.hidden = value && !row.dataset.search.includes(value));
        }});
        boxes.forEach(box => box.addEventListener('change', update));
        update();
      }})();
      </script>
    """
    return _inventory_shell("预约详情", body, staff_id=staff_id, active="appointments")


def render_inventory_schedule(
    products: tuple[CatalogInternalProduct, ...],
    blocks: tuple[CatalogUnitBlock, ...],
    *,
    staff_id: str,
    csrf_token: str,
    selected_unit: str = "",
    saved: bool = False,
    error: str = "",
) -> bytes:
    units = [
        (unit, product)
        for product in products
        for unit in product.units
        if unit.condition_state != "retired"
    ]
    options = "".join(
        f'<option value="{escape(unit.unit_code, quote=True)}"{" selected" if unit.unit_code == selected_unit else ""}>{escape(product.style_name or product.model_code)} · {escape(unit.unit_code)} · {escape(unit.size_label or "尺码待补")}</option>'
        for unit, product in units
    )
    if not options:
        options = '<option value="">请先在款式页录入实物件</option>'
    block_rows = "".join(_schedule_row(block, csrf_token) for block in blocks)
    if not block_rows:
        block_rows = '<div class="empty compact">目前没有进行中或未来的档期安排。</div>'
    reserved = sum(block.block_type == "reserved" for block in blocks)
    maintenance = sum(block.block_type in {"cleaning", "alteration"} for block in blocks)
    offsite = sum(block.block_type == "offsite" for block in blocks)
    notice = '<div class="notice ok">档期已保存，冲突检查已更新。</div>' if saved else ""
    if error:
        notice = f'<div class="notice error">{escape(error)}</div>'
    body = f"""
      <div class="page-head calendar-head"><div><h1>实物流转</h1><p>按实物件管理预订、清洗、修改和外借时间。</p></div><a class="page-cross-link" href="/staff/inventory/appointments">查看试纱预约</a></div>
      {notice}
      <section class="schedule-stats"><div><span>预订</span><strong>{reserved}</strong></div><div><span>清洗 / 修改</span><strong>{maintenance}</strong></div><div><span>外借</span><strong>{offsite}</strong></div><div><span>可排实物</span><strong>{len(units)}</strong></div></section>
      <section class="schedule-grid">
        <div><div class="section-title"><h2>当前与未来安排</h2><span>按开始时间排序</span></div><div class="schedule-list">{block_rows}</div></div>
        <div class="schedule-create"><div class="section-title"><h2>新增安排</h2><span>重叠时间会被阻止</span></div>
          <form method="post" action="/staff/inventory/flow" class="schedule-form">
            <input type="hidden" name="csrf" value="{escape(csrf_token, quote=True)}">
            <label><span>实物件</span><select name="unit_code" required>{options}</select></label>
            <label><span>流转类型</span><select name="block_type"><option value="reserved">预订</option><option value="cleaning">清洗</option><option value="alteration">修改</option><option value="offsite">外借</option></select></label>
            <label><span>开始时间</span><input type="datetime-local" name="starts_at" required></label>
            <label><span>结束时间</span><input type="datetime-local" name="ends_at" required></label>
            <label><span>说明</span><input name="notes" placeholder="顾客、用途或交接说明"></label>
            <button type="submit"{" disabled" if not units else ""}>保存安排</button>
          </form>
        </div>
      </section>
    """
    return _inventory_shell("实物流转", body, staff_id=staff_id, active="flow")


def render_inventory_analytics(
    summary: CatalogAnalyticsSummary,
    *,
    staff_id: str,
) -> bytes:
    confidence_label = {
        "insufficient": "数据不足",
        "developing": "正在形成",
        "reliable": "可用于规划",
    }[summary.confidence]
    confidence_copy = {
        "insufficient": "目前只展示已发生的事实，暂不生成补货或低使用率结论。",
        "developing": "可以观察趋势，但采购或淘汰前仍需要店员复核。",
        "reliable": "数据覆盖和积累周期已达到库存规划的基础要求。",
    }[summary.confidence]
    windows = "".join(
        f'<a class="{"active" if days == summary.window_days else ""}" href="/staff/inventory/analytics?days={days}">{days} 天</a>'
        for days in (30, 90, 180, 365)
    )
    signal_rows = sorted(
        (row for row in summary.rows if row.demand_score or row.inventory_units),
        key=lambda row: (row.demand_score, row.effective_bookings, row.try_ons),
        reverse=True,
    )[:12]
    max_score = max((row.demand_score for row in signal_rows), default=0)
    table = "".join(_analytics_row(row, max_score) for row in signal_rows)
    if not table:
        table = '<div class="empty compact">录入实物并积累咨询、试纱或预订后，这里会出现款式对比。</div>'
    replenishment = [
        row for row in signal_rows
        if row.demand_score >= 12 and row.demand_score / max(1, row.inventory_units) >= 8
    ] if summary.confidence != "insufficient" else []
    low_usage = [
        row for row in signal_rows
        if row.inventory_units and row.effective_bookings == 0 and row.try_ons == 0
    ] if summary.tracked_days >= 90 and summary.confidence != "insufficient" else []
    planning = _planning_list(replenishment, "当前没有达到阈值的补充候选")
    low = _planning_list(low_usage, "当前没有可可靠判断的低使用率款式")
    body = f"""
      <div class="page-head analytics-head"><div><h1>数据分析</h1><p>区分顾客需求与库存操作，避免用不完整数据做采购判断。</p></div><div class="window-tabs">{windows}</div></div>
      <section class="confidence {summary.confidence}"><div><span>建议可信度</span><strong>{confidence_label}</strong></div><p>{confidence_copy}</p></section>
      <section class="analytics-stats"><div><span>已积累</span><strong>{summary.tracked_days} 天</strong></div><div><span>顾客需求信号</span><strong>{summary.demand_events}</strong></div><div><span>未来有效预订</span><strong>{summary.active_future_bookings}</strong></div><div><span>实物覆盖率</span><strong>{summary.coverage_percent}%</strong></div></section>
      <section class="analytics-grid">
        <div><div class="section-title"><h2>款式信号</h2><span>需求、试纱、预订和流失综合对比</span></div><div class="analytics-table">{table}</div></div>
        <div class="analytics-side">
          <section><div class="section-title"><h2>数据完整度</h2><span>{summary.products_with_units}/{summary.active_products} 款已建实物</span></div><div class="readiness"><i style="width:{summary.coverage_percent}%"></i></div><ul><li>运营动作 {summary.operational_events} 条</li><li>顾客需求 {summary.demand_events} 条</li><li>建议至少积累 30 天和 20 条需求信号</li></ul></section>
          <section><div class="section-title"><h2>补充候选</h2><span>需求高、实物少</span></div>{planning}</section>
          <section><div class="section-title"><h2>低使用率观察</h2><span>至少 90 天数据</span></div>{low}</section>
        </div>
      </section>
    """
    return _inventory_shell("数据分析", body, staff_id=staff_id, active="analytics")


def render_inventory_planning(
    summary: CatalogPlanningSummary,
    *,
    staff_id: str,
) -> bytes:
    confidence_label = {
        "insufficient": "暂不决策",
        "developing": "趋势观察",
        "reliable": "可供规划",
    }[summary.confidence]
    confidence_copy = {
        "insufficient": "数据门槛尚未达到，以下观察不作为采购或淘汰依据。",
        "developing": "趋势已经出现，执行补充前仍需结合店员判断与资金安排。",
        "reliable": "周期、需求量和实体覆盖率已达到规划门槛，建议仍需人工确认。",
    }[summary.confidence]
    windows = "".join(
        f'<a class="{"active" if days == summary.window_days else ""}" href="/staff/inventory/planning?days={days}">{days} 天</a>'
        for days in (30, 90, 180, 365)
    )
    recommendations = "".join(
        _planning_recommendation_row(item) for item in summary.recommendations
    ) or '<div class="empty compact">当前没有达到可信门槛的库存调整建议。</div>'
    observations = "".join(
        _planning_recommendation_row(item) for item in summary.observations
    ) or '<div class="analysis-empty">当前还没有可观察的款式需求信号。</div>'
    gaps = "".join(f"<li>{escape(item)}</li>" for item in summary.data_gaps)
    if not gaps:
        gaps = "<li>核心数据项已达到当前规划周期的基础要求</li>"
    body = f"""
      <div class="page-head analytics-head"><div><h1>库存规划</h1><p>根据真实需求、有效预订、流失和实体数量生成可追溯建议。</p></div><div class="window-tabs">{windows}</div></div>
      <section class="confidence {summary.confidence}"><div><span>决策状态</span><strong>{confidence_label}</strong></div><p>{confidence_copy}</p></section>
      <section class="analytics-stats"><div><span>调整建议</span><strong>{len(summary.recommendations)}</strong></div><div><span>观察款式</span><strong>{len(summary.observations)}</strong></div><div><span>实体覆盖率</span><strong>{summary.coverage_percent}%</strong></div><div><span>统计周期</span><strong>{summary.tracked_days} 天</strong></div></section>
      <section class="planning-grid">
        <div>
          <div class="section-title"><h2>库存调整建议</h2><span>补充与低使用率复核</span></div>
          <div class="recommendation-list">{recommendations}</div>
          <div class="section-title planning-observe-title"><h2>继续观察</h2><span>已有信号但暂不决策</span></div>
          <div class="recommendation-list muted-list">{observations}</div>
        </div>
        <div class="planning-aside">
          <section><div class="section-title"><h2>数据缺口</h2><span>{len(summary.data_gaps)} 项</span></div><ul>{gaps}</ul></section>
          <section><div class="section-title"><h2>实体完整度</h2><span>{summary.inventory_units} 件</span></div><dl><div><dt>未建实体</dt><dd>{summary.products_without_units} 款</dd></div><div><dt>缺少尺码</dt><dd>{summary.units_without_size} 件</dd></div><div><dt>三围不完整</dt><dd>{summary.units_without_measurements} 件</dd></div></dl></section>
        </div>
      </section>
    """
    return _inventory_shell("库存规划", body, staff_id=staff_id, active="planning")


def _product_row(product: CatalogInternalProduct) -> str:
    code = quote(product.model_code, safe="-._~")
    modes = " / ".join(
        label for enabled, label in (
            (product.rental_available, "可租"),
            (product.purchase_or_custom_available, "可买/定制"),
        ) if enabled
    ) or "待确认"
    return f"""
      <a class="product-row" href="/staff/inventory/products/{code}">
        <span class="thumb"><img src="/staff/inventory/assets/{code}" alt=""></span>
        <span class="product-name"><strong>{escape(product.style_name or product.model_code)}</strong><small>{escape(product.brand_internal)} · {escape(product.model_code)}</small></span>
        <span class="row-data"><small>分类</small>{_type_label(product.catalog_type)}</span>
        <span class="row-data"><small>方式</small>{modes}</span>
        <span class="row-data units"><small>实物</small>{len(product.units)} 件</span>
        <span class="arrow" aria-hidden="true">›</span>
      </a>
    """


def _unit_form(unit, csrf_token: str) -> str:
    return f"""
      <form method="post" action="/staff/inventory/units/{quote(unit.unit_code, safe='-._~')}" class="unit-editor existing">
        <input type="hidden" name="csrf" value="{escape(csrf_token, quote=True)}">
        <div class="unit-code"><strong>{escape(unit.unit_code)}</strong><small>{escape(unit.model_code)}</small><a style="color:#285846;font-size:10px" href="/staff/inventory/flow?unit={quote(unit.unit_code, safe='-._~')}">安排流转</a></div>
        {_field('size_label', '尺码', unit.size_label, '待补充')}{_field('bust_cm', '胸围 cm', _number_value(unit.bust_cm), '待补充', 'number')}{_field('waist_cm', '腰围 cm', _number_value(unit.waist_cm), '待补充', 'number')}{_field('hip_cm', '臀围 cm', _number_value(unit.hip_cm), '待补充', 'number')}{_field('dress_length_cm', '衣长 cm', _number_value(unit.dress_length_cm), '待补充', 'number')}{_field('location', '位置', unit.location, '待补充')}
        {_state_select('inventory_state', '库存状态', unit.inventory_state)}{_condition_select('condition_state', '实物情况', unit.condition_state)}
        <label class="wide"><span>备注</span><input name="notes" value="{escape(unit.notes, quote=True)}" placeholder="瑕疵、修改或特殊说明"></label><button type="submit">更新</button>
      </form>
    """


def _schedule_row(block: CatalogUnitBlock, csrf_token: str) -> str:
    return f"""
      <article class="schedule-row">
        <span class="schedule-kind {_block_class(block.block_type)}">{_block_label(block.block_type)}</span>
        <div><strong>{escape(block.style_name or block.model_code)}</strong><small>{escape(block.unit_code)} · {escape(block.size_label or '尺码待补')}</small></div>
        <div class="schedule-time"><span>{_display_datetime(block.starts_at)}</span><small>至 {_display_datetime(block.ends_at)}</small></div>
        <div class="schedule-note">{escape(block.notes or '无备注')}</div>
        <form method="post" action="/staff/inventory/flow/{escape(block.id, quote=True)}/cancel"><input type="hidden" name="csrf" value="{escape(csrf_token, quote=True)}"><button class="cancel" type="submit">取消</button></form>
      </article>
    """


def _weekday_label(index: int) -> str:
    return ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[index]


def _calendar_day_lane(
    day: date,
    appointments: tuple[CatalogCalendarAppointment, ...],
) -> str:
    scheduled: list[tuple[CatalogCalendarAppointment, int]] = []
    slot_ends: list[datetime] = []
    for item in sorted(appointments, key=lambda appointment: appointment.starts_at):
        starts = datetime.fromisoformat(item.starts_at)
        ends = datetime.fromisoformat(item.ends_at)
        slot = next((index for index, slot_end in enumerate(slot_ends) if slot_end <= starts), len(slot_ends))
        if slot == len(slot_ends):
            slot_ends.append(ends)
        else:
            slot_ends[slot] = ends
        scheduled.append((item, slot))
    columns = max(1, len(slot_ends))
    events = "".join(_calendar_event(item, slot, columns) for item, slot in scheduled)
    return f'<div class="calendar-day-lane" data-date="{day.isoformat()}">{events}</div>'


def _calendar_event(
    appointment: CatalogCalendarAppointment,
    slot: int,
    columns: int,
) -> str:
    starts = datetime.fromisoformat(appointment.starts_at)
    start_minutes = starts.hour * 60 + starts.minute
    top = max(0, min(632, round((start_minutes - 9 * 60) / 60 * 64)))
    outside = start_minutes < 9 * 60 or start_minutes + 120 > 19 * 60
    width = 96 / columns - 1
    left = 2 + slot * (96 / columns)
    return f"""
      <a class="calendar-event {_appointment_status_class(appointment.status)}{' outside' if outside else ''}" href="/staff/inventory/appointments/{quote(appointment.id, safe='-._~')}" style="top:{top}px;left:{left:.2f}%;width:{width:.2f}%">
        <strong>{starts.strftime('%H:%M')} {escape(appointment.customer_name or '未填姓名')}</strong>
        <span>{_appointment_status_label(appointment.status)} · {appointment.selected_styles} 款</span>
      </a>
    """


def _agenda_day(
    day: date,
    appointments: tuple[CatalogCalendarAppointment, ...],
) -> str:
    rows = "".join(
        f'<a href="/staff/inventory/appointments/{quote(item.id, safe="-._~")}"><time>{datetime.fromisoformat(item.starts_at).strftime("%H:%M")}</time><span><strong>{escape(item.customer_name or "未填姓名")}</strong><small>{_appointment_status_label(item.status)} · {item.selected_styles} 款心仪</small></span><i class="{_appointment_status_class(item.status)}"></i></a>'
        for item in appointments
    ) or '<div class="agenda-empty">暂无预约</div>'
    return f'<div class="agenda-day"><div class="agenda-date"><strong>{day.strftime("%m月%d日")}</strong><span>{_weekday_label(day.weekday())}</span></div><div class="agenda-events">{rows}</div></div>'


def _unparsed_appointment_row(appointment: CatalogCalendarAppointment) -> str:
    return f"""
      <a href="/staff/inventory/appointments/{quote(appointment.id, safe='-._~')}">
        <span><strong>{escape(appointment.customer_name or '未填姓名')}</strong><small>{escape(appointment.time_text or '未填写时间')}</small></span>
        <em>{_appointment_status_label(appointment.status)}</em><span class="arrow">›</span>
      </a>
    """


def _appointment_style_row(item: CatalogAppointmentStyleAvailability) -> str:
    availability_label = {
        "available": "在店可核对",
        "verify": "需要核对",
        "conflict": "该时段占用",
        "offsite": "当前不在店",
        "maintenance": "维护中",
        "no_units": "未建实体",
    }[item.availability]
    search_text = " ".join(
        (item.style_name, item.brand_internal, item.model_code)
    ).casefold()
    return f"""
      <label class="appointment-style-row" data-search="{escape(search_text, quote=True)}">
        <input type="checkbox" name="style_{escape(item.model_code, quote=True)}" value="1"{' checked' if item.selected else ''}>
        <span class="style-thumb"><img src="/staff/inventory/assets/{quote(item.model_code, safe='-._~')}" alt=""></span>
        <span class="style-copy"><strong>{escape(item.style_name or item.model_code)}</strong><small>{escape(item.brand_internal)} · {escape(item.model_code)} · {_type_label(item.catalog_type)}</small></span>
        <span class="availability {escape(item.availability)}"><strong>{availability_label}</strong><small>{escape(item.availability_note)}</small></span>
      </label>
    """


def _appointment_status_label(status: str) -> str:
    return {
        "collecting_details": "收集中",
        "time_pending_staff": "待确认时间",
        "awaiting_payment": "待付款",
        "payment_review": "付款复核",
        "confirmed": "已锁定",
        "reschedule_collecting": "改期协商中",
        "reschedule_pending_staff": "改期待确认",
        "cancel_pending_staff": "取消待确认",
        "completed": "已完成",
    }.get(status, "待处理")


def _appointment_status_class(status: str) -> str:
    if status == "confirmed":
        return "confirmed"
    if status in {"awaiting_payment", "payment_review"}:
        return "payment"
    if status in {"time_pending_staff", "reschedule_pending_staff", "cancel_pending_staff"}:
        return "pending"
    if status == "completed":
        return "completed"
    return "collecting"


def _analytics_row(row, max_score: int) -> str:
    width = round(row.demand_score / max_score * 100) if max_score else 0
    return f"""
      <div class="analytics-row">
        <div><strong>{escape(row.style_name or row.model_code)}</strong><small>{escape(row.model_code)} · {_type_label(row.catalog_type)}</small></div>
        <div class="signal"><i style="width:{width}%"></i></div>
        <span><small>需求</small>{row.demand_signals}</span><span><small>试纱</small>{row.try_ons}</span><span><small>预订</small>{row.effective_bookings}</span><span><small>实物</small>{row.inventory_units}</span>
      </div>
    """


def _planning_recommendation_row(item: CatalogPlanningRecommendation) -> str:
    category_label = {
        "replenish": "建议补充",
        "low_use": "低使用复核",
        "observe": "继续观察",
    }[item.category]
    return f"""
      <a class="recommendation-row" href="/staff/inventory/products/{quote(item.model_code, safe='-._~')}">
        <span class="recommendation-badge {escape(item.category)}">{category_label}</span>
        <span class="recommendation-copy"><strong>{escape(item.style_name or item.model_code)}</strong><small>{escape(item.reason)}</small></span>
        <span class="recommendation-evidence"><i>需求 {item.demand_signals}</i><i>预订 {item.effective_bookings}</i><i>流失 {item.lost_demand}</i><i>实物 {item.inventory_units}</i></span>
        <span class="arrow" aria-hidden="true">›</span>
      </a>
    """


def _planning_list(rows, empty_text: str) -> str:
    if not rows:
        return f'<div class="analysis-empty">{empty_text}</div>'
    return '<div class="planning-list">' + "".join(
        f'<a href="/staff/inventory/products/{quote(row.model_code, safe="-._~")}"><strong>{escape(row.style_name or row.model_code)}</strong><span>{row.demand_score} 分 · {row.inventory_units} 件</span></a>'
        for row in rows[:5]
    ) + "</div>"


def _block_label(value: str) -> str:
    return {"reserved": "预订", "cleaning": "清洗", "alteration": "修改", "offsite": "外借"}.get(value, value)


def _block_class(value: str) -> str:
    return {"reserved": "blue", "cleaning": "green", "alteration": "amber", "offsite": "coral"}.get(value, "")


def _display_datetime(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%m月%d日 %H:%M")
    except ValueError:
        return escape(value)


def _field(name: str, label: str, value: str, placeholder: str, kind: str = "text") -> str:
    step = ' step="0.1" min="0"' if kind == "number" else ""
    return f'<label><span>{label}</span><input type="{kind}" name="{name}" value="{escape(str(value), quote=True)}" placeholder="{placeholder}"{step}></label>'


def _state_select(name: str, label: str, current: str) -> str:
    options = (("unverified", "待核验"), ("available", "可用"), ("reserved", "已预订"), ("cleaning", "清洗中"), ("alteration", "修改中"), ("offsite", "外借"))
    return f'<label><span>{label}</span><select name="{name}">' + "".join(
        f'<option value="{value}"{" selected" if value == current else ""}>{text}</option>' for value, text in options
    ) + "</select></label>"


def _condition_select(name: str, label: str, current: str) -> str:
    options = (("excellent", "很好"), ("good", "正常"), ("repair_needed", "需要修复"), ("retired", "停用"))
    return f'<label><span>{label}</span><select name="{name}">' + "".join(
        f'<option value="{value}"{" selected" if value == current else ""}>{text}</option>' for value, text in options
    ) + "</select></label>"


def _money_label(value: int | None) -> str:
    return "待确认" if value is None else f"¥{value:,}"


def _number_value(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)


def _type_label(value: str) -> str:
    return "婚纱" if value == "bridal" else "轻纱 / 礼服"


def _inventory_shell(title: str, body: str, *, staff_id: str, active: str) -> bytes:
    dashboard_class = " active" if active == "dashboard" else ""
    products_class = " active" if active == "products" else ""
    appointments_class = " active" if active == "appointments" else ""
    website_class = " active" if active == "website" else ""
    flow_class = " active" if active == "flow" else ""
    analytics_class = " active" if active == "analytics" else ""
    planning_class = " active" if active == "planning" else ""
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(title)} · Forthatday</title><style>{_interactive_css()}@media(max-width:840px){{.mobile{{grid-template-columns:repeat(7,1fr)}}.agenda-date strong{{white-space:nowrap}}}}</style></head><body>
    <div class="app"><aside><div class="brand"><span>FTD</span><div><strong>Forthatday</strong><small>库存工作台</small></div></div><div class="nav-label">工作区</div><nav><a class="{dashboard_class}" href="/staff/inventory">总览</a><a class="{products_class}" href="/staff/inventory/products">款式与实物</a><a class="{appointments_class}" href="/staff/inventory/appointments">试纱预约</a><a class="{website_class}" href="/staff/inventory/website">网站预约</a><a class="{flow_class}" href="/staff/inventory/flow">实物流转</a><a class="{analytics_class}" href="/staff/inventory/analytics">数据分析</a><a class="{planning_class}" href="/staff/inventory/planning">库存规划</a></nav><div class="account">内部系统<br>{escape(staff_id)}<br><a href="/staff/logout">退出登录</a></div></aside>
    <div class="content"><header>库存工作台 <span>{escape(staff_id)}</span></header><main>{body}</main></div></div>
    <nav class="mobile"><a class="{dashboard_class}" href="/staff/inventory">总览</a><a class="{products_class}" href="/staff/inventory/products">款式</a><a class="{appointments_class}" href="/staff/inventory/appointments">预约</a><a class="{website_class}" href="/staff/inventory/website">网站</a><a class="{flow_class}" href="/staff/inventory/flow">流转</a><a class="{analytics_class}" href="/staff/inventory/analytics">分析</a><a class="{planning_class}" href="/staff/inventory/planning">规划</a></nav></body></html>"""
    return html.encode("utf-8")


def _interactive_css() -> str:
    return """
    .account a{display:inline-block;margin-top:6px;color:#d9e3de}
    .calendar-actions{display:flex;align-items:center;gap:8px}.calendar-zoom{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff}.calendar-zoom a{width:42px;height:36px;display:grid;place-items:center;border-right:1px solid var(--line);color:var(--muted);font-size:11px;text-decoration:none}.calendar-zoom a:last-child{border-right:0}.calendar-zoom a.active{background:var(--spruce);color:#fff;font-weight:700}.add-appointment{height:36px;padding:0 13px;font-size:11px}.calendar-month{border:1px solid var(--line);border-radius:8px;background:#fff;overflow:auto}.month-weekdays,.month-grid{min-width:840px;display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}.month-weekdays{border-bottom:1px solid var(--line)}.month-weekdays span{height:38px;display:grid;place-items:center;border-right:1px solid var(--line);color:var(--muted);font-size:10px}.month-weekdays span:last-child{border-right:0}.month-day{min-height:132px;padding:7px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);overflow:hidden}.month-day:nth-child(7n){border-right:0}.month-day.outside-month{background:#f7f8f7}.month-date{height:24px;display:flex;align-items:center;justify-content:space-between}.month-date strong{width:23px;height:23px;display:grid;place-items:center;border-radius:50%;font:700 11px monospace}.month-date span{color:var(--muted);font:10px monospace}.month-day.today .month-date strong{background:var(--spruce);color:#fff}.month-events{display:grid;gap:4px}.month-event{min-width:0;padding:5px 6px;display:grid;grid-template-columns:38px minmax(0,1fr);gap:2px 5px;border-left:3px solid var(--blue);border-radius:3px;background:#edf3f6;color:var(--ink);text-decoration:none}.month-event time{font:700 9px monospace}.month-event strong{overflow:hidden;font-size:10px;text-overflow:ellipsis;white-space:nowrap}.month-event span{grid-column:2;overflow:hidden;color:var(--muted);font-size:8px;text-overflow:ellipsis;white-space:nowrap}.appointment-dialog{width:min(560px,calc(100% - 28px));padding:0;border:0;border-radius:8px;background:#fff;color:var(--ink);box-shadow:0 22px 60px rgba(24,33,29,.28)}.appointment-dialog::backdrop{background:rgba(15,23,19,.46)}.appointment-create{padding:22px}.dialog-head{display:flex;align-items:start;justify-content:space-between;gap:20px;padding-bottom:18px;border-bottom:1px solid var(--line)}.dialog-head h2{margin:0;font-size:19px}.dialog-head p{margin:6px 0 0;color:var(--muted);font-size:11px}.dialog-close{width:34px;height:34px;padding:0;border-color:var(--line);background:#fff;color:var(--ink);font-size:22px;font-weight:400}.appointment-fields{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:20px 0}.appointment-fields .wide{grid-column:1/-1}.dialog-actions{display:flex;justify-content:flex-end;gap:8px}.dialog-actions button{min-width:86px}.dialog-actions .secondary{border-color:var(--line);background:#fff;color:var(--ink)}
    @media(max-width:840px){.calendar-actions{display:grid;grid-template-columns:auto auto;justify-content:start}.calendar-actions .page-cross-link{grid-column:1/-1}.calendar-month{margin-inline:0}.month-weekdays,.month-grid{min-width:760px}.appointment-fields{grid-template-columns:1fr}.appointment-fields .wide{grid-column:auto}.appointment-create{padding:18px}}
    .page-cross-link{padding:9px 12px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--spruce);font-size:11px;font-weight:700;text-decoration:none}
    @media(max-width:840px){.page-cross-link{width:max-content}.mobile{grid-template-columns:repeat(6,1fr)}}
    .brand{height:42px}
    .calendar-head{align-items:center}.schedule-tabs{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff}.schedule-tabs a{padding:10px 14px;color:var(--muted);font-size:11px;text-decoration:none;border-right:1px solid var(--line)}.schedule-tabs a:last-child{border-right:0}.schedule-tabs a.active{background:var(--spruce);color:#fff}.calendar-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}.calendar-toolbar>div{display:flex}.calendar-toolbar a{min-width:38px;height:34px;padding:0 10px;display:grid;place-items:center;border:1px solid var(--line);border-right:0;background:#fff;color:var(--ink);text-decoration:none;font-size:12px}.calendar-toolbar a:first-child{border-radius:6px 0 0 6px}.calendar-toolbar a:last-child{border-right:1px solid var(--line);border-radius:0 6px 6px 0}.calendar-toolbar span{color:var(--muted);font-size:11px}.calendar-week{border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}.calendar-week-head{display:grid;grid-template-columns:56px repeat(7,minmax(0,1fr));border-bottom:1px solid var(--line)}.calendar-day-head{height:58px;padding:9px;display:grid;align-content:center;gap:3px;border-left:1px solid var(--line);text-align:center}.calendar-day-head strong{font-size:12px}.calendar-day-head span{color:var(--muted);font-size:9px}.calendar-day-head.today{background:#edf3ef;color:var(--spruce)}.calendar-week-body{height:640px;display:grid;grid-template-columns:56px repeat(7,minmax(0,1fr))}.calendar-hours{position:relative;border-right:1px solid var(--line)}.calendar-hours span{position:relative;height:64px;padding:5px 7px 0 0;display:block;color:var(--muted);font:9px monospace;text-align:right;transform:translateY(-1px)}.calendar-day-lane{position:relative;border-right:1px solid var(--line);background:repeating-linear-gradient(to bottom,transparent 0,transparent 63px,var(--line) 64px)}.calendar-day-lane:last-child{border-right:0}.calendar-event{position:absolute;z-index:1;height:122px;padding:7px 6px;border-left:3px solid;border-radius:4px;display:grid;align-content:start;gap:5px;color:inherit;text-decoration:none;overflow:hidden}.calendar-event strong{font-size:10px;line-height:1.35}.calendar-event span{font-size:8px;line-height:1.35}.calendar-event.confirmed{background:#e7f1eb;border-color:#285846;color:#214b3b}.calendar-event.payment{background:#fbf0df;border-color:#b67a2f;color:#80521d}.calendar-event.pending{background:#e7f0f5;border-color:#3b6f8e;color:#2f617e}.calendar-event.collecting{background:#edf0ee;border-color:#829089;color:#53615a}.calendar-event.completed{background:#f0f1f0;border-color:#a5aaa7;color:#777}.calendar-event.outside{height:58px;background:#fae9e7;border-color:#c5534c;color:#8d332e}.calendar-agenda{display:none}.unparsed-section{margin-top:28px}.unparsed-list{border-top:1px solid var(--ink)}.unparsed-list>a{min-height:64px;padding:10px 4px;display:grid;grid-template-columns:minmax(0,1fr) 100px 18px;gap:12px;align-items:center;border-bottom:1px solid var(--line);color:inherit;text-decoration:none}.unparsed-list>a>span:first-child{display:grid;gap:4px}.unparsed-list small{color:var(--muted);font-size:10px}.unparsed-list em{font-style:normal;color:var(--muted);font-size:10px}.appointment-detail-head{display:flex;justify-content:space-between;gap:28px;padding:12px 0 28px;border-bottom:1px solid var(--line)}.appointment-detail-head h1{margin:12px 0 5px;font-size:30px;letter-spacing:0}.appointment-detail-head p{margin:0;color:var(--muted);font-size:12px}.appointment-status{display:inline-flex;padding:6px 8px;border-radius:4px;font-size:10px;font-weight:700}.appointment-status.confirmed{background:#e7f1eb;color:#285846}.appointment-status.payment{background:#fbf0df;color:#80521d}.appointment-status.pending{background:#e7f0f5;color:#2f617e}.appointment-status.collecting,.appointment-status.completed{background:#edf0ee;color:#53615a}.appointment-detail-head dl{width:min(520px,52%);margin:0;display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid var(--ink)}.appointment-detail-head dl div{padding:14px;border-bottom:1px solid var(--line)}.style-search{max-width:420px;margin-bottom:14px}.appointment-style-list{border-top:1px solid var(--ink)}.appointment-style-row{min-height:82px;padding:8px 4px;display:grid;grid-template-columns:28px 54px minmax(170px,1fr) minmax(160px,.6fr);gap:12px;align-items:center;border-bottom:1px solid var(--line);cursor:pointer}.appointment-style-row[hidden]{display:none}.appointment-style-row>input{width:18px;height:18px}.style-thumb{width:46px;height:62px;overflow:hidden;border:1px solid var(--line);border-radius:4px;background:#fff}.style-thumb img{width:100%;height:100%;object-fit:contain}.style-copy,.availability{display:grid;gap:4px}.style-copy small,.availability small{color:var(--muted);font-size:9px}.availability{justify-self:end;text-align:right}.availability strong{font-size:11px}.availability.available strong{color:#285846}.availability.verify strong,.availability.conflict strong{color:#80521d}.availability.offsite strong,.availability.maintenance strong{color:#963c36}.availability.no_units strong{color:#66716b}.save-styles{position:sticky;bottom:74px;margin:16px 0 0 auto;display:block;min-width:150px;box-shadow:0 8px 24px rgba(24,33,29,.16)}
    @media(max-width:900px){.calendar-week{display:none}.calendar-agenda{display:grid;border-top:1px solid var(--ink)}.agenda-day{display:grid;grid-template-columns:92px minmax(0,1fr);border-bottom:1px solid var(--line)}.agenda-day>div{padding:14px 8px;display:grid;align-content:start;gap:4px}.agenda-day>div span{color:var(--muted);font-size:9px}.agenda-day>a{min-height:66px;padding:10px 4px;display:grid;grid-template-columns:52px minmax(0,1fr) 10px;gap:10px;align-items:center;border-bottom:1px solid var(--line);color:inherit;text-decoration:none}.agenda-day>a:last-child{border-bottom:0}.agenda-day time{font:700 11px monospace}.agenda-day>a span{display:grid;gap:4px}.agenda-day small{color:var(--muted);font-size:9px}.agenda-day i{width:8px;height:8px;border-radius:50%}.agenda-day i.confirmed{background:#285846}.agenda-day i.payment{background:#b67a2f}.agenda-day i.pending{background:#3b6f8e}.agenda-day i.collecting,.agenda-day i.completed{background:#829089}.agenda-empty{min-height:58px!important;color:var(--muted);font-size:10px}.appointment-detail-head{display:grid}.appointment-detail-head dl{width:100%}}
    @media(max-width:900px){.agenda-day>.agenda-date{padding:14px 8px;display:grid;align-content:start;gap:4px}.agenda-events{padding:0!important;display:block!important}.agenda-events>a{min-height:66px;padding:10px 4px;display:grid;grid-template-columns:52px minmax(0,1fr) 10px;gap:10px;align-items:center;border-bottom:1px solid var(--line);color:inherit;text-decoration:none}.agenda-events>a:last-child{border-bottom:0}.agenda-events>a span{display:grid;gap:4px}.agenda-events time{font:700 11px monospace}.agenda-events small{color:var(--muted);font-size:9px}.agenda-events i{width:8px;height:8px;border-radius:50%}.agenda-events i.confirmed{background:#285846}.agenda-events i.payment{background:#b67a2f}.agenda-events i.pending{background:#3b6f8e}.agenda-events i.collecting,.agenda-events i.completed{background:#829089}.agenda-events .agenda-empty{min-height:58px;padding:14px 4px;color:var(--muted);font-size:10px}}
    @media(max-width:840px){.calendar-head{display:grid;gap:14px;align-items:start}.schedule-tabs{width:100%}.schedule-tabs a{flex:1;text-align:center}.calendar-toolbar{align-items:flex-end}.calendar-toolbar span{max-width:150px;text-align:right}.appointment-style-row{grid-template-columns:26px 50px minmax(0,1fr)}.availability{grid-column:3;justify-self:start;text-align:left;padding-bottom:8px}}
    @media(max-width:520px){.agenda-day{grid-template-columns:76px minmax(0,1fr)}.appointment-detail-head dl{grid-template-columns:1fr}.appointment-style-row{grid-template-columns:24px 46px minmax(0,1fr);gap:8px}.style-thumb{width:42px;height:56px}.style-copy small{white-space:normal}.save-styles{width:100%}}
    @media(max-width:840px){.page-head.analytics-head{display:grid;grid-template-columns:1fr;gap:14px;align-items:start}.page-head.analytics-head .window-tabs{width:100%}}
    .planning-grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(280px,.55fr);gap:30px;margin-top:28px;align-items:start}.recommendation-list{border-top:1px solid var(--ink)}.recommendation-row{min-height:86px;display:grid;grid-template-columns:82px minmax(180px,1fr) 210px 18px;gap:14px;align-items:center;border-bottom:1px solid var(--line);color:inherit;text-decoration:none}.recommendation-row:hover{background:#eef2ef}.recommendation-badge{width:76px;min-height:28px;padding:6px;display:grid;place-items:center;border-radius:4px;font-size:10px;font-weight:700;text-align:center}.recommendation-badge.replenish{background:#fae9e7;color:#963c36}.recommendation-badge.low_use{background:#fbf0df;color:#80521d}.recommendation-badge.observe{background:#e7f0f5;color:#2f617e}.recommendation-copy{display:grid;gap:5px}.recommendation-copy small{color:var(--muted);font-size:10px;line-height:1.5}.recommendation-evidence{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}.recommendation-evidence i{font-style:normal;color:var(--muted);font-size:9px;text-align:center}.muted-list .recommendation-row{min-height:76px}.planning-observe-title{margin-top:34px}.planning-aside{display:grid;gap:24px}.planning-aside section{padding:17px;border:1px solid var(--line);border-radius:8px;background:#fff}.planning-aside ul{margin:8px 0 0;padding-left:18px;color:var(--muted);font-size:11px;line-height:1.8}.planning-aside dl{margin:0;display:grid}.planning-aside dl div{display:flex;justify-content:space-between;padding:11px 0;border-bottom:1px solid var(--line)}.planning-aside dl div:last-child{border-bottom:0}.planning-aside dd{margin:0;font:700 12px monospace}@media(max-width:900px){.planning-grid{grid-template-columns:1fr}}@media(max-width:840px){.recommendation-row{grid-template-columns:78px minmax(0,1fr) 16px}.recommendation-evidence{grid-column:2;grid-template-columns:repeat(4,minmax(0,1fr));padding-bottom:12px}.planning-aside{grid-template-columns:1fr 1fr}}@media(max-width:520px){.planning-aside{grid-template-columns:1fr}.recommendation-row{gap:10px}.recommendation-badge{width:72px}}
    .analytics-head{align-items:center}.window-tabs{display:flex;border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff}.window-tabs a{padding:9px 12px;border-right:1px solid var(--line);color:var(--muted);font-size:11px;text-decoration:none}.window-tabs a:last-child{border-right:0}.window-tabs a.active{background:var(--spruce);color:#fff}.confidence{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:15px 18px;border-radius:8px;margin-bottom:16px}.confidence>div{display:grid;gap:3px}.confidence span{font-size:10px}.confidence strong{font-size:15px}.confidence p{margin:0;font-size:11px}.confidence.insufficient{background:#fbf0df;color:#80521d}.confidence.developing{background:#e7f0f5;color:#2f617e}.confidence.reliable{background:#e7f1eb;color:#285846}.analytics-stats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}.analytics-stats>div{min-height:92px;padding:18px;border-right:1px solid var(--line);display:grid;align-content:center;gap:8px}.analytics-stats>div:last-child{border-right:0}.analytics-stats span{color:var(--muted);font-size:11px}.analytics-stats strong{font:700 21px monospace}.analytics-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(290px,.65fr);gap:28px;margin-top:28px}.analytics-table{border-top:1px solid var(--ink)}.analytics-row{min-height:70px;display:grid;grid-template-columns:minmax(150px,1.2fr) minmax(100px,1fr) repeat(4,48px);gap:10px;align-items:center;border-bottom:1px solid var(--line)}.analytics-row>div:first-child{display:grid;gap:4px}.analytics-row small{display:block;color:var(--muted);font-size:9px}.analytics-row>span{text-align:center;font:700 12px monospace}.signal{height:6px;border-radius:3px;background:#dfe5e1;overflow:hidden}.signal i{display:block;height:100%;background:var(--blue)}.analytics-side{display:grid;gap:26px}.analytics-side section{padding-bottom:18px;border-bottom:1px solid var(--line)}.readiness{height:6px;border-radius:3px;background:#dfe5e1;overflow:hidden}.readiness i{display:block;height:100%;background:var(--spruce)}.analytics-side ul{margin:12px 0 0;padding-left:18px;color:var(--muted);font-size:11px;line-height:1.8}.analysis-empty{padding:18px 0;color:var(--muted);font-size:11px}.planning-list{display:grid}.planning-list a{display:flex;justify-content:space-between;padding:11px 0;border-bottom:1px solid var(--line);color:inherit;text-decoration:none;font-size:11px}.planning-list span{color:var(--muted)}@media(max-width:900px){.analytics-grid{grid-template-columns:1fr}}@media(max-width:840px){.analytics-head{align-items:flex-start;display:grid}.window-tabs{width:100%}.window-tabs a{flex:1;text-align:center}.confidence{align-items:flex-start;display:grid}.analytics-stats{grid-template-columns:repeat(2,1fr)}.analytics-stats>div:nth-child(2){border-right:0}.analytics-stats>div:nth-child(-n+2){border-bottom:1px solid var(--line)}.analytics-row{grid-template-columns:minmax(120px,1fr) 70px repeat(2,40px)}.analytics-row>span:nth-last-child(n+3){display:none}}
    .nav-label{margin:0 12px 8px;color:#8da097;font-size:11px}.schedule-stats{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}.schedule-stats>div{min-height:92px;padding:18px;border-right:1px solid var(--line);display:grid;align-content:center;gap:8px}.schedule-stats>div:last-child{border-right:0}.schedule-stats span{color:var(--muted);font-size:11px}.schedule-stats strong{font:700 23px monospace}.schedule-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(310px,.65fr);gap:28px;margin-top:28px;align-items:start}.schedule-list{border-top:1px solid var(--ink)}.schedule-row{min-height:88px;display:grid;grid-template-columns:52px minmax(130px,1fr) minmax(130px,.9fr) minmax(90px,.7fr) 58px;gap:12px;align-items:center;border-bottom:1px solid var(--line)}.schedule-kind{width:44px;height:28px;display:grid;place-items:center;border-radius:4px;font-size:11px;font-weight:700}.schedule-kind.blue{background:#e7f0f5;color:#2f617e}.schedule-kind.green{background:#e7f1eb;color:#285846}.schedule-kind.amber{background:#fbf0df;color:#8c5b1f}.schedule-kind.coral{background:#fae9e7;color:#963c36}.schedule-row div{display:grid;gap:4px}.schedule-row small,.schedule-note{color:var(--muted);font-size:10px}.schedule-time span{font-size:12px;font-weight:700}.schedule-row form{display:block}.schedule-row .cancel{height:32px;padding:0 9px;border-color:#d9b5b1;background:#fff;color:var(--coral);font-size:11px}.schedule-create{position:sticky;top:94px}.schedule-form{display:grid;gap:12px;padding:16px;border:1px solid var(--line);border-radius:8px;background:#fff}.schedule-form button:disabled{cursor:not-allowed;opacity:.45}@media(max-width:900px){.schedule-grid{grid-template-columns:1fr}.schedule-create{position:static}}@media(max-width:840px){.schedule-stats{grid-template-columns:repeat(2,1fr)}.schedule-stats>div:nth-child(2){border-right:0}.schedule-stats>div:nth-child(-n+2){border-bottom:1px solid var(--line)}.schedule-row{grid-template-columns:46px minmax(0,1fr) 58px}.schedule-time{grid-column:2}.schedule-note{display:none}}
    :root{--canvas:#f4f6f4;--surface:#fff;--ink:#18211d;--muted:#66716b;--line:#d9dfdc;--spruce:#285846;--blue:#3b6f8e;--amber:#b67a2f;--coral:#c5534c}*{box-sizing:border-box}html{background:var(--canvas);color:var(--ink);font-family:"Noto Sans SC","Microsoft YaHei",sans-serif}body{margin:0;min-width:320px}.app{min-height:100vh;display:grid;grid-template-columns:224px minmax(0,1fr)}aside{position:sticky;top:0;height:100vh;padding:24px 18px;background:#1d2c26;color:#fff;display:flex;flex-direction:column}.brand{display:flex;gap:11px;align-items:center;margin-bottom:34px}.brand>span{width:32px;height:32px;display:grid;place-items:center;border:1px solid #688075;border-radius:6px;font:700 11px monospace}.brand div{display:grid}.brand small{color:#aebdb6;font-size:11px;margin-top:2px}aside nav{display:grid;gap:4px}aside nav a,aside nav span{min-height:42px;padding:0 12px;display:flex;align-items:center;border-radius:6px;color:#93a49c;text-decoration:none;font-size:13px}aside nav a.active{background:#eff5f1;color:var(--ink);font-weight:700}.account{margin-top:auto;padding:14px 10px 0;border-top:1px solid #34473e;color:#aebdb6;font-size:11px;line-height:1.7}.content{min-width:0;padding-bottom:50px}.content>header{height:72px;padding:0 clamp(22px,4vw,52px);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);color:var(--muted);font-size:12px}.content>header span{color:var(--ink)}main{width:min(1180px,calc(100% - 44px));margin:auto}.page-head{padding:34px 0 22px;display:flex;justify-content:space-between;align-items:end}.page-head h1,.product-copy h1{margin:0;font-size:32px;letter-spacing:0}.page-head p,.product-copy p{margin:7px 0 0;color:var(--muted);font-size:13px}.count{font:600 13px monospace}.filters{display:grid;grid-template-columns:minmax(220px,1fr) 220px 88px;gap:10px;padding:14px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}label{display:grid;gap:6px}label span{font-size:11px;color:var(--muted)}input,select,button{min-width:0;height:40px;border:1px solid #cdd5d1;border-radius:6px;background:#fff;color:var(--ink);font:inherit;padding:0 11px}button{border-color:var(--spruce);background:var(--spruce);color:white;font-weight:700;cursor:pointer}.product-list{margin-top:18px;border-top:1px solid var(--ink)}.product-row{min-height:86px;display:grid;grid-template-columns:62px minmax(180px,1.4fr) minmax(90px,.7fr) minmax(120px,.8fr) 70px 18px;gap:14px;align-items:center;border-bottom:1px solid var(--line);color:inherit;text-decoration:none}.product-row:hover{background:#eef2ef}.thumb{width:52px;height:62px;background:#fff;border:1px solid var(--line);border-radius:5px;overflow:hidden}.thumb img,.product-image img{width:100%;height:100%;object-fit:contain}.product-name{display:grid;gap:5px}.product-name small,.row-data small{display:block;color:var(--muted);font-size:10px;margin-bottom:4px}.row-data{font-size:12px}.units{font-family:monospace}.arrow{color:var(--muted);font-size:24px}.empty{padding:48px 18px;text-align:center;color:var(--muted);border-bottom:1px solid var(--line)}.empty.compact{padding:24px}.breadcrumbs{display:flex;gap:8px;padding:26px 0 16px;color:var(--muted);font-size:12px}.breadcrumbs a{color:var(--spruce)}.product-detail{display:grid;grid-template-columns:minmax(280px,.7fr) minmax(0,1.3fr);gap:34px;padding-bottom:32px;border-bottom:1px solid var(--line)}.product-image{aspect-ratio:4/5;max-height:480px;background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}.product-copy{padding-top:16px}.eyebrow{color:var(--spruce);font-size:11px;text-transform:uppercase}.product-copy dl{margin:28px 0 0;display:grid;grid-template-columns:repeat(2,1fr);border-top:1px solid var(--ink)}.product-copy dl div{padding:15px 0;border-bottom:1px solid var(--line)}dt{color:var(--muted);font-size:10px}dd{margin:6px 0 0;font-weight:700;font-size:13px}.detail-section{padding:28px 0;border-bottom:1px solid var(--line)}.section-title{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:13px}.section-title h2{margin:0;font-size:15px}.section-title span{color:var(--muted);font-size:11px}.unit-list{display:grid;gap:10px}.unit-editor{display:grid;grid-template-columns:140px repeat(4,minmax(110px,1fr)) 90px;gap:10px;align-items:end;padding:14px;border:1px solid var(--line);border-radius:8px;background:#fff}.unit-editor.new-unit{grid-template-columns:repeat(4,minmax(130px,1fr))}.unit-code{align-self:center;display:grid;gap:4px}.unit-code small{color:var(--muted);font-size:10px}.unit-editor .wide{grid-column:span 2}.notice{margin:8px 0 16px;padding:12px 14px;border-radius:6px;font-size:12px}.notice.ok{background:#e7f1eb;color:#1f5a3f}.notice.error{background:#fae9e7;color:#8d332e}.mobile{display:none}:focus-visible{outline:3px solid rgba(59,111,142,.4);outline-offset:2px}@media(max-width:840px){.app{display:block}aside{display:none}.content{padding-bottom:78px}.content>header{height:58px;padding:0 15px}main{width:calc(100% - 28px)}.page-head{padding:24px 0 18px}.page-head h1,.product-copy h1{font-size:25px}.filters{grid-template-columns:1fr 1fr}.filters button{grid-column:span 2}.product-row{grid-template-columns:58px minmax(0,1fr) 48px 16px}.row-data:not(.units){display:none}.product-detail{grid-template-columns:1fr;gap:18px}.product-image{max-height:420px}.product-copy{padding-top:0}.unit-editor,.unit-editor.new-unit{grid-template-columns:repeat(2,minmax(0,1fr))}.unit-code,.unit-editor button{grid-column:span 2}.unit-editor .wide{grid-column:span 2}.mobile{position:fixed;z-index:10;left:0;right:0;bottom:0;height:64px;display:grid;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--line);background:rgba(255,255,255,.97)}.mobile a,.mobile span{display:grid;place-items:center;color:#8b948f;text-decoration:none;font-size:11px}.mobile a.active{color:var(--spruce);font-weight:700}}@media(max-width:430px){.filters{grid-template-columns:1fr}.filters button{grid-column:auto}.section-title{align-items:start;gap:12px}.unit-editor,.unit-editor.new-unit{grid-template-columns:1fr}.unit-code,.unit-editor button,.unit-editor .wide{grid-column:auto}.product-copy dl{grid-template-columns:1fr 1fr}}
    """
