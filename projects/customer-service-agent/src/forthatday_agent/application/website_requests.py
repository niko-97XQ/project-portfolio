from datetime import date, datetime, timedelta, timezone
from html import escape
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

from forthatday_agent.application.inventory_web import _inventory_shell
from forthatday_agent.infrastructure.website_request_store import IntakeError


CHINA = timezone(timedelta(hours=8))
LABELS = {'new': '待联系', 'contacted': '已联系', 'closed': '已结束'}


def public_intake(store, form, headers, base_url):
    expected = urlsplit(base_url)
    origin = f'{expected.scheme}://{expected.netloc}'
    if not expected.netloc or headers.get('Origin', '').rstrip('/') != origin:
        raise IntakeError(403, '请从工作室官网提交申请。')
    if form.get('company'):
        raise IntakeError(422, '申请未能提交，请检查填写内容。')
    if form.get('consent') != 'yes':
        raise IntakeError(422, '请先同意为本次预约联系使用所填信息。')
    name = form.get('customer_name', '').strip()
    phone = form.get('customer_phone', '').strip()
    message = form.get('message', '').strip()
    if not 1 <= len(name) <= 50 or any(ord(c) < 32 for c in name):
        raise IntakeError(422, '请填写有效的称呼，最多 50 个字。')
    if not re.fullmatch(r'[+()\d -]{6,30}', phone, flags=re.ASCII):
        raise IntakeError(422, '请填写可联系到你的电话号码。')
    digits = re.sub(r'\D', '', phone)
    if not 7 <= len(digits) <= 16:
        raise IntakeError(422, '请检查电话号码的长度。')
    phone = ('+' if phone.startswith('+') else '') + digits
    try:
        preferred = date.fromisoformat(form.get('preferred_date', ''))
    except ValueError:
        raise IntakeError(422, '请选择意向试纱日期。')
    today = datetime.now(CHINA).date()
    if not today <= preferred <= today + timedelta(days=365):
        raise IntakeError(422, '请选择今天起一年内的意向日期。')
    preferred_time = form.get('preferred_time', '')
    if preferred_time and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', preferred_time):
        raise IntakeError(422, '请检查意向时间。')
    if len(message) > 500 or any(ord(c) < 32 and c not in '\r\n\t' for c in message):
        raise IntakeError(422, '补充说明最多 500 个字，请勿填写敏感身份信息。')
    key = form.get('idempotency_key', '')
    try:
        key = str(UUID(key))
    except (ValueError, AttributeError):
        raise IntakeError(422, '提交标识已失效，请刷新页面后重试。')
    payload = dict(customer_name=name, customer_phone=phone, preferred_date=preferred.isoformat(), preferred_time=preferred_time, message=message)
    client_ip = headers.get('X-Forwarded-For', headers.get('Remote-Addr', 'unknown')).split(',')[-1].strip()
    reference, created = store.submit(payload, key, client_ip)
    return 201 if created else 200, json.dumps({'reference':reference,'status':'pending','message':'申请已收到，店员会通过所填联系方式与你确认。意向日期尚未锁定，请以店员确认为准。'},ensure_ascii=False).encode()


def render_requests(store, *, staff_id, csrf, query, error=''):
    status = query.get('status', 'new')
    if status not in {*LABELS, ''}:
        status = 'new'
    try:
        page = max(0, min(100000, int(query.get('page', '0'))))
    except ValueError:
        page = 0
    rows, total = store.list_requests(status, page)
    tabs = ''.join(f'<a href="?status={key}">{label}</a> ' for key,label in [*LABELS.items(),('', '全部')])
    entries = []
    for row in rows:
        options = ''.join(f'<option value="{s}" {"selected" if s==row["status"] else ""}>{label}</option>' for s,label in LABELS.items())
        when = datetime.fromtimestamp(row['created_at'],CHINA).strftime('%Y-%m-%d %H:%M')
        entries.append(f'''<article class="web-request"><div class="request-title"><h2>{escape(row['customer_name'])}</h2><span>{LABELS[row['status']]}</span></div>
          <p><a href="tel:{escape(row['customer_phone'],quote=True)}">{escape(row['customer_phone'])}</a></p>
          <p>意向日期：{escape(row['preferred_date'])} {escape(row['preferred_time'] or '时间待沟通')}</p>
          <p class="request-message">{escape(row['message'] or '无补充说明')}</p>
          <p class="request-meta">{escape(row['id'])} · {when} · 网站申请，尚未锁定档期</p>
          <form method="post" action="/staff/inventory/website"><input type="hidden" name="csrf" value="{escape(csrf,quote=True)}"><input type="hidden" name="request_id" value="{escape(row['id'],quote=True)}"><input type="hidden" name="version" value="{row['version']}"><label>处理状态 <select name="status">{options}</select></label><button type="submit">保存状态</button></form></article>''')
    previous = f'<a href="?status={status}&amp;page={page-1}">上一页</a>' if page else ''
    following = f'<a href="?status={status}&amp;page={page+1}">下一页</a>' if (page+1)*25<total else ''
    body = f'''<style>.web-request{{padding:22px 0;border-bottom:1px solid var(--line)}}.request-title{{display:flex;align-items:center;gap:20px}}.web-request h2{{font-size:18px;margin:0}}.web-request p{{font-size:14px;line-height:1.8}}.request-meta{{color:var(--muted);font-size:11px!important}}.request-message{{white-space:pre-wrap;overflow-wrap:anywhere}}.web-request form{{display:flex;align-items:end;gap:12px;flex-wrap:wrap}}.web-request label{{display:grid;gap:8px}}.request-tabs,.request-pages{{display:flex;gap:22px;margin:20px 0;flex-wrap:wrap}}.request-title span{{font-size:12px}}.request-error{{color:#a42620}}</style>
      <div class="page-head"><div><h1>网站预约申请</h1><p>先联系顾客确认意向，再按现有流程录入试纱预约；此处不会自动收款或锁定档期。</p></div></div>
      <nav class="request-tabs" aria-label="申请状态">{tabs}</nav><p>当前筛选共 {total} 条</p>
      <p class="request-error" role="status">{escape(error or ('状态已保存' if query.get('saved') else ''))}</p>
      {''.join(entries) or '<p>暂无此状态的申请。</p>'}<nav class="request-pages">{previous}{following}</nav>
      <p><a href="/staff/inventory/appointments">前往试纱预约日历</a></p>'''
    return _inventory_shell('网站预约申请',body,staff_id=staff_id,active='website')
