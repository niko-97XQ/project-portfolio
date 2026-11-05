"""Reviewed public-intake policy excerpt; no staff or provider credentials."""
from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit
from uuid import UUID
from bridal_demo.store import IntakeError

CHINA = timezone(timedelta(hours=8))

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
