from __future__ import annotations

import argparse
import mimetypes
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forthatday_agent.application.inventory_web import (  # noqa: E402
    render_inventory_dashboard,
    render_appointment_calendar,
    render_appointment_detail,
    render_inventory_analytics,
    render_inventory_planning,
    render_inventory_product_detail,
    render_inventory_products,
    render_inventory_schedule,
)
from forthatday_agent.infrastructure.catalog_store import CatalogStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview the inventory dashboard locally.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "agent_v2.db")
    parser.add_argument("--catalog", type=Path, default=PROJECT_ROOT / "private" / "catalog")
    arguments = parser.parse_args()

    store = CatalogStore(arguments.database, catalog_root=arguments.catalog)
    class PreviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            if parsed.path == "/":
                self.send_response(302)
                self.send_header("Location", "/staff/inventory")
                self.end_headers()
                return
            if parsed.path == "/staff/inventory":
                body = render_inventory_dashboard(store.dashboard_summary(), staff_id="本地检查")
                self._respond(200, body)
                return
            if parsed.path == "/staff/inventory/products":
                catalog_type = query.get("type", "")
                try:
                    products = store.internal_products(query.get("q", ""), catalog_type=catalog_type)
                except ValueError:
                    products = ()
                body = render_inventory_products(
                    products,
                    staff_id="本地检查",
                    query=query.get("q", ""),
                    catalog_type=catalog_type,
                )
                self._respond(200, body)
                return
            if parsed.path in {
                "/staff/inventory/appointments",
                "/staff/inventory/flow",
                "/staff/inventory/schedule",
            }:
                inventory_view = parsed.path == "/staff/inventory/flow" or (
                    parsed.path == "/staff/inventory/schedule"
                    and query.get("view") == "inventory"
                )
                if inventory_view:
                    body = render_inventory_schedule(
                        store.internal_products(limit=500),
                        store.unit_blocks(),
                        staff_id="本地检查",
                        csrf_token="preview-csrf",
                        selected_unit=query.get("unit", ""),
                        saved=query.get("saved") == "1",
                    )
                else:
                    try:
                        selected_date = date.fromisoformat(query.get("date", ""))
                    except ValueError:
                        selected_date = datetime.now().date()
                    calendar_view = "week" if query.get("view") == "week" else "month"
                    if calendar_view == "week":
                        period_start = selected_date - timedelta(days=selected_date.weekday())
                        period_days = 7
                    else:
                        period_start = selected_date.replace(day=1)
                        period_days = monthrange(period_start.year, period_start.month)[1]
                    body = render_appointment_calendar(
                        store.calendar_appointments(period_start, days=period_days),
                        period_start,
                        staff_id="本地检查",
                        csrf_token="preview-csrf",
                        view=calendar_view,
                        saved=query.get("saved") == "1",
                    )
                self._respond(200, body)
                return
            appointment_prefix = next(
                (
                    prefix
                    for prefix in (
                        "/staff/inventory/appointments/",
                        "/staff/inventory/schedule/appointments/",
                    )
                    if parsed.path.startswith(prefix)
                ),
                "",
            )
            if appointment_prefix:
                appointment_id = unquote(parsed.path.removeprefix(appointment_prefix))
                if not appointment_id or "/" in appointment_id:
                    self.send_error(404)
                    return
                try:
                    appointment = store.calendar_appointment(appointment_id)
                    styles = store.appointment_style_options(appointment_id)
                except ValueError:
                    self.send_error(404)
                    return
                self._respond(
                    200,
                    render_appointment_detail(
                        appointment,
                        styles,
                        staff_id="本地检查",
                        csrf_token="preview-csrf",
                        saved=query.get("saved") == "1",
                    ),
                )
                return
            if parsed.path == "/staff/inventory/analytics":
                try:
                    days = int(query.get("days", "90"))
                    summary = store.analytics_summary(window_days=days)
                except ValueError:
                    summary = store.analytics_summary(window_days=90)
                self._respond(
                    200,
                    render_inventory_analytics(summary, staff_id="本地检查"),
                )
                return
            if parsed.path == "/staff/inventory/planning":
                try:
                    days = int(query.get("days", "90"))
                    summary = store.planning_summary(window_days=days)
                except ValueError:
                    summary = store.planning_summary(window_days=90)
                self._respond(
                    200,
                    render_inventory_planning(summary, staff_id="本地检查"),
                )
                return
            if parsed.path.startswith("/staff/inventory/assets/"):
                model_code = unquote(parsed.path.removeprefix("/staff/inventory/assets/"))
                try:
                    image_path = store.catalog_image_path(model_code)
                    self._respond(
                        200,
                        image_path.read_bytes(),
                        mimetypes.guess_type(image_path.name)[0] or "image/jpeg",
                    )
                except (OSError, ValueError):
                    self.send_error(404)
                return
            if parsed.path.startswith("/staff/inventory/products/"):
                model_code = unquote(parsed.path.removeprefix("/staff/inventory/products/"))
                try:
                    product = store.internal_product(model_code)
                except ValueError:
                    self.send_error(404)
                    return
                body = render_inventory_product_detail(
                    product,
                    staff_id="本地检查",
                    csrf_token="preview-csrf",
                    saved=query.get("saved") == "1",
                )
                self._respond(200, body)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            is_units = parsed.path.startswith("/staff/inventory/units")
            is_appointment_create = parsed.path == "/staff/inventory/appointments"
            is_schedule = parsed.path in {
                "/staff/inventory/flow",
                "/staff/inventory/schedule",
            } or (
                (
                    parsed.path.startswith("/staff/inventory/flow/")
                    or parsed.path.startswith("/staff/inventory/schedule/")
                )
                and parsed.path.endswith("/cancel")
            )
            style_prefix = next(
                (
                    prefix
                    for prefix in (
                        "/staff/inventory/appointments/",
                        "/staff/inventory/schedule/appointments/",
                    )
                    if parsed.path.startswith(prefix)
                ),
                "",
            )
            is_styles = bool(style_prefix) and parsed.path.endswith("/styles")
            if not is_units and not is_schedule and not is_styles and not is_appointment_create:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                form = {key: items[-1] for key, items in values.items()}
                if form.get("csrf") != "preview-csrf":
                    raise ValueError("invalid preview form")
                if is_appointment_create:
                    appointment_date = form.get("appointment_date", "")
                    store.add_manual_appointment(
                        customer_name=form.get("customer_name", ""),
                        customer_phone=form.get("customer_phone", ""),
                        starts_at=f"{appointment_date}T{form.get('appointment_time', '')}",
                        status=form.get("status", "time_pending_staff"),
                    )
                elif is_styles:
                    appointment_id = unquote(
                        parsed.path.removeprefix(style_prefix).removesuffix("/styles")
                    )
                    store.set_appointment_style_interests(
                        appointment_id,
                        tuple(
                            unquote(key.removeprefix("style_"))
                            for key, value in form.items()
                            if key.startswith("style_") and value == "1"
                        ),
                    )
                elif is_schedule:
                    if parsed.path in {"/staff/inventory/flow", "/staff/inventory/schedule"}:
                        store.add_unit_block(
                            form.get("unit_code", ""),
                            form.get("starts_at", ""),
                            form.get("ends_at", ""),
                            form.get("block_type", ""),
                            notes=form.get("notes", ""),
                        )
                    else:
                        block_prefix = (
                            "/staff/inventory/flow/"
                            if parsed.path.startswith("/staff/inventory/flow/")
                            else "/staff/inventory/schedule/"
                        )
                        block_id = parsed.path.removeprefix(block_prefix).removesuffix("/cancel")
                        store.cancel_unit_block(block_id)
                elif parsed.path == "/staff/inventory/units":
                    model_code = form.get("model_code", "")
                    store.add_inventory_unit(
                        model_code,
                        unit_code=form.get("unit_code", ""),
                        size_label=form.get("size_label", ""),
                        bust_cm=_number(form.get("bust_cm", "")),
                        waist_cm=_number(form.get("waist_cm", "")),
                        hip_cm=_number(form.get("hip_cm", "")),
                        dress_length_cm=_number(form.get("dress_length_cm", "")),
                        location=form.get("location", ""),
                        inventory_state=form.get("inventory_state", "unverified"),
                        condition_state=form.get("condition_state", "good"),
                        notes=form.get("notes", ""),
                    )
                else:
                    unit_code = unquote(parsed.path.removeprefix("/staff/inventory/units/"))
                    products = store.internal_products(limit=500)
                    model_code = next(
                        item.model_code for item in products
                        if any(unit.unit_code == unit_code for unit in item.units)
                    )
                    store.update_inventory_unit(
                        unit_code,
                        size_label=form.get("size_label", ""),
                        bust_cm=_number(form.get("bust_cm", "")),
                        waist_cm=_number(form.get("waist_cm", "")),
                        hip_cm=_number(form.get("hip_cm", "")),
                        dress_length_cm=_number(form.get("dress_length_cm", "")),
                        location=form.get("location", ""),
                        inventory_state=form.get("inventory_state", "unverified"),
                        condition_state=form.get("condition_state", "good"),
                        notes=form.get("notes", ""),
                    )
            except (StopIteration, UnicodeError, ValueError):
                self.send_error(400)
                return
            self.send_response(303)
            if is_appointment_create:
                self.send_header(
                    "Location",
                    f"/staff/inventory/appointments?date={quote(appointment_date, safe='-')}&saved=1",
                )
            elif is_styles:
                self.send_header(
                    "Location",
                    f"/staff/inventory/appointments/{quote(appointment_id, safe='-._~')}?saved=1",
                )
            elif is_schedule:
                self.send_header("Location", "/staff/inventory/flow?saved=1")
            else:
                self.send_header(
                    "Location",
                    f"/staff/inventory/products/{quote(model_code, safe='-._~')}?saved=1",
                )
            self.end_headers()

        def _respond(
            self,
            status: int,
            body: bytes,
            content_type: str = "text/html; charset=utf-8",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((arguments.host, arguments.port), PreviewHandler)
    print(f"Inventory preview: http://{arguments.host}:{server.server_address[1]}/")
    server.serve_forever()


def _number(value: str) -> float | None:
    return float(value) if value.strip() else None


if __name__ == "__main__":
    main()
