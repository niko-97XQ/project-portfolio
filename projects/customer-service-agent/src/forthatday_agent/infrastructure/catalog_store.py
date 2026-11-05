from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


CHINA_TIMEZONE = timezone(timedelta(hours=8))


CATALOG_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS catalog_products (
    model_code TEXT PRIMARY KEY,
    catalog_type TEXT NOT NULL,
    brand_internal TEXT NOT NULL DEFAULT '',
    style_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    rental_price_yuan INTEGER,
    purchase_price_yuan INTEGER,
    rental_available INTEGER NOT NULL DEFAULT 0,
    purchase_or_custom_available INTEGER NOT NULL DEFAULT 0,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    inventory_state TEXT NOT NULL DEFAULT 'unverified',
    public_price_hint TEXT NOT NULL DEFAULT '',
    source_document TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (catalog_type IN ('bridal', 'lightwear')),
    CHECK (lifecycle_state IN ('active', 'inactive', 'draft')),
    CHECK (inventory_state IN ('available', 'reserved', 'cleaning', 'alteration', 'offsite', 'unverified'))
);

CREATE TABLE IF NOT EXISTS catalog_product_images (
    id TEXT PRIMARY KEY,
    model_code TEXT NOT NULL REFERENCES catalog_products(model_code) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(model_code, storage_path)
);

CREATE INDEX IF NOT EXISTS catalog_product_images_product
ON catalog_product_images(model_code, sort_order);

CREATE TABLE IF NOT EXISTS catalog_inventory_blocks (
    id TEXT PRIMARY KEY,
    model_code TEXT NOT NULL REFERENCES catalog_products(model_code) ON DELETE CASCADE,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    block_type TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    CHECK (block_type IN ('reserved', 'cleaning', 'alteration', 'offsite')),
    CHECK (ends_at > starts_at)
);

CREATE INDEX IF NOT EXISTS catalog_inventory_blocks_window
ON catalog_inventory_blocks(model_code, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS catalog_inventory_units (
    unit_code TEXT PRIMARY KEY,
    model_code TEXT NOT NULL REFERENCES catalog_products(model_code) ON DELETE CASCADE,
    size_label TEXT NOT NULL DEFAULT '',
    bust_cm REAL,
    waist_cm REAL,
    hip_cm REAL,
    dress_length_cm REAL,
    location TEXT NOT NULL DEFAULT '',
    condition_state TEXT NOT NULL DEFAULT 'good',
    inventory_state TEXT NOT NULL DEFAULT 'unverified',
    notes TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (condition_state IN ('excellent', 'good', 'repair_needed', 'retired')),
    CHECK (inventory_state IN ('available', 'reserved', 'cleaning', 'alteration', 'offsite', 'unverified'))
);

CREATE INDEX IF NOT EXISTS catalog_inventory_units_product
ON catalog_inventory_units(model_code, inventory_state);

CREATE TABLE IF NOT EXISTS catalog_unit_blocks (
    id TEXT PRIMARY KEY,
    unit_code TEXT NOT NULL REFERENCES catalog_inventory_units(unit_code) ON DELETE CASCADE,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    block_type TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    cancelled_at TEXT,
    created_at TEXT NOT NULL,
    CHECK (block_type IN ('reserved', 'cleaning', 'alteration', 'offsite')),
    CHECK (ends_at > starts_at)
);

CREATE INDEX IF NOT EXISTS catalog_unit_blocks_window
ON catalog_unit_blocks(unit_code, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS catalog_inventory_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    model_code TEXT REFERENCES catalog_products(model_code) ON DELETE SET NULL,
    unit_code TEXT REFERENCES catalog_inventory_units(unit_code) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'system',
    notes TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS catalog_inventory_events_time
ON catalog_inventory_events(occurred_at, event_type);

CREATE INDEX IF NOT EXISTS catalog_inventory_events_product
ON catalog_inventory_events(model_code, occurred_at);

CREATE TABLE IF NOT EXISTS catalog_appointment_styles (
    appointment_id TEXT NOT NULL,
    model_code TEXT NOT NULL REFERENCES catalog_products(model_code) ON DELETE CASCADE,
    selected_at TEXT NOT NULL,
    PRIMARY KEY (appointment_id, model_code)
);

CREATE INDEX IF NOT EXISTS catalog_appointment_styles_appointment
ON catalog_appointment_styles(appointment_id, selected_at);

CREATE TABLE IF NOT EXISTS catalog_import_drafts (
    draft_key TEXT PRIMARY KEY,
    catalog_type TEXT NOT NULL,
    brand_internal TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    image_paths TEXT NOT NULL DEFAULT '',
    source_document TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending_mapping',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (status IN ('pending_mapping', 'mapped', 'discarded'))
);
"""


@dataclass(frozen=True, slots=True)
class CatalogProduct:
    model_code: str
    catalog_type: str
    style_name: str
    description: str
    rental_available: bool
    purchase_or_custom_available: bool
    lifecycle_state: str
    inventory_state: str
    public_price_hint: str
    image_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogDashboardSummary:
    products: int
    bridal_products: int
    lightwear_products: int
    images: int
    pending_drafts: int
    inventory_units: int
    products_without_units: int
    available_units: int
    reserved_units: int
    maintenance_units: int
    offsite_units: int
    unverified_units: int


@dataclass(frozen=True, slots=True)
class CatalogInventoryUnit:
    unit_code: str
    model_code: str
    size_label: str
    bust_cm: float | None
    waist_cm: float | None
    hip_cm: float | None
    dress_length_cm: float | None
    location: str
    condition_state: str
    inventory_state: str
    notes: str


@dataclass(frozen=True, slots=True)
class CatalogInternalProduct:
    model_code: str
    catalog_type: str
    brand_internal: str
    style_name: str
    description: str
    rental_price_yuan: int | None
    purchase_price_yuan: int | None
    rental_available: bool
    purchase_or_custom_available: bool
    image_paths: tuple[str, ...]
    units: tuple[CatalogInventoryUnit, ...]


@dataclass(frozen=True, slots=True)
class CatalogUnitBlock:
    id: str
    unit_code: str
    model_code: str
    style_name: str
    size_label: str
    starts_at: str
    ends_at: str
    block_type: str
    notes: str
    status: str


@dataclass(frozen=True, slots=True)
class CatalogCalendarAppointment:
    id: str
    conversation_id: str
    customer_name: str
    customer_phone: str
    status: str
    time_text: str
    starts_at: str
    ends_at: str
    parsed: bool
    selected_styles: int


@dataclass(frozen=True, slots=True)
class CatalogAppointmentStyleAvailability:
    model_code: str
    style_name: str
    brand_internal: str
    catalog_type: str
    selected: bool
    availability: str
    availability_note: str
    total_units: int
    available_units: int


@dataclass(frozen=True, slots=True)
class CatalogUtilizationRow:
    model_code: str
    style_name: str
    catalog_type: str
    inventory_units: int
    demand_signals: int
    try_ons: int
    effective_bookings: int
    lost_demand: int
    demand_score: int


@dataclass(frozen=True, slots=True)
class CatalogAnalyticsSummary:
    window_days: int
    tracked_days: int
    active_products: int
    inventory_units: int
    products_with_units: int
    demand_events: int
    operational_events: int
    active_future_bookings: int
    coverage_percent: int
    confidence: str
    rows: tuple[CatalogUtilizationRow, ...]


@dataclass(frozen=True, slots=True)
class CatalogPlanningRecommendation:
    model_code: str
    style_name: str
    category: str
    priority: str
    reason: str
    demand_signals: int
    effective_bookings: int
    lost_demand: int
    inventory_units: int


@dataclass(frozen=True, slots=True)
class CatalogPlanningSummary:
    window_days: int
    tracked_days: int
    confidence: str
    coverage_percent: int
    active_products: int
    inventory_units: int
    products_without_units: int
    units_without_size: int
    units_without_measurements: int
    recommendations: tuple[CatalogPlanningRecommendation, ...]
    observations: tuple[CatalogPlanningRecommendation, ...]
    data_gaps: tuple[str, ...]


class CatalogStore:
    """Private product and availability data, kept separate from customer-facing knowledge."""

    def __init__(self, database_path: str | Path, *, catalog_root: str | Path) -> None:
        self.database_path = str(database_path)
        self.catalog_root = Path(catalog_root)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(CATALOG_SCHEMA)
            self._ensure_schema_migrations(connection)
            connection.commit()

    def import_verified_manifest(self, manifest_path: str | Path) -> int:
        imported = 0
        now = _now()
        with Path(manifest_path).open(encoding="utf-8-sig", newline="") as source:
            rows = csv.DictReader(source)
            with self._transaction() as connection:
                for row in rows:
                    model_code = _required(row, "model_code").lower()
                    catalog_type = _required(row, "catalog_type")
                    if catalog_type not in {"bridal", "lightwear"}:
                        raise ValueError(f"unsupported catalog type: {catalog_type}")
                    rental_price = _money(row.get("rental_price_internal", ""))
                    purchase_price = _money(row.get("purchase_price_internal", ""))
                    connection.execute(
                        """
                        INSERT INTO catalog_products (
                            model_code, catalog_type, brand_internal, style_name, description,
                            rental_price_yuan, purchase_price_yuan, rental_available,
                            purchase_or_custom_available, lifecycle_state, inventory_state,
                            source_document, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 'unverified', ?, ?, ?)
                        ON CONFLICT(model_code) DO UPDATE SET
                            catalog_type = excluded.catalog_type,
                            brand_internal = excluded.brand_internal,
                            style_name = excluded.style_name,
                            description = excluded.description,
                            rental_price_yuan = excluded.rental_price_yuan,
                            purchase_price_yuan = excluded.purchase_price_yuan,
                            rental_available = excluded.rental_available,
                            purchase_or_custom_available = excluded.purchase_or_custom_available,
                            lifecycle_state = 'active',
                            source_document = excluded.source_document,
                            updated_at = excluded.updated_at
                        """,
                        (
                            model_code,
                            catalog_type,
                            row.get("brand_internal", "").strip(),
                            row.get("style_name", "").strip(),
                            row.get("description", "").strip(),
                            rental_price,
                            purchase_price,
                            _yes(row.get("rental_available", "")),
                            _yes(row.get("purchase_or_custom_available", "")),
                            row.get("source_document", "").strip(),
                            now,
                            now,
                        ),
                    )
                    self._replace_images(connection, model_code, row.get("image_path", ""), now)
                    imported += 1
        return imported

    def import_pending_drafts(self, manifest_path: str | Path) -> int:
        imported = 0
        now = _now()
        with Path(manifest_path).open(encoding="utf-8-sig", newline="") as source:
            rows = csv.DictReader(source)
            with self._transaction() as connection:
                for row in rows:
                    draft_key = _required(row, "model_code").lower()
                    connection.execute(
                        """
                        INSERT INTO catalog_import_drafts (
                            draft_key, catalog_type, brand_internal, display_name, image_paths,
                            source_document, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'pending_mapping', ?, ?)
                        ON CONFLICT(draft_key) DO UPDATE SET
                            catalog_type = excluded.catalog_type,
                            brand_internal = excluded.brand_internal,
                            display_name = excluded.display_name,
                            image_paths = excluded.image_paths,
                            source_document = excluded.source_document,
                            updated_at = excluded.updated_at
                        """,
                        (
                            draft_key,
                            _required(row, "catalog_type"),
                            row.get("brand_internal", "").strip(),
                            row.get("style_name", "").strip(),
                            row.get("image_path", "").strip(),
                            row.get("source_document", "").strip(),
                            now,
                            now,
                        ),
                    )
                    imported += 1
        return imported

    def customer_safe_products(self, query: str = "", *, limit: int = 12) -> tuple[CatalogProduct, ...]:
        terms = [term.lower() for term in query.split() if term.strip()]
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT p.*
                FROM catalog_products p
                WHERE p.lifecycle_state = 'active'
                ORDER BY p.model_code
                """
            ).fetchall()
            products = []
            for row in rows:
                haystack = " ".join((row["style_name"], row["description"], row["catalog_type"])).lower()
                if terms and not all(term in haystack for term in terms):
                    continue
                images = connection.execute(
                    "SELECT storage_path FROM catalog_product_images WHERE model_code = ? ORDER BY sort_order, id",
                    (row["model_code"],),
                ).fetchall()
                products.append(
                    CatalogProduct(
                        model_code=str(row["model_code"]),
                        catalog_type=str(row["catalog_type"]),
                        style_name=str(row["style_name"]),
                        description=str(row["description"]),
                        rental_available=bool(row["rental_available"]),
                        purchase_or_custom_available=bool(row["purchase_or_custom_available"]),
                        lifecycle_state=str(row["lifecycle_state"]),
                        inventory_state=str(row["inventory_state"]),
                        public_price_hint=str(row["public_price_hint"]),
                        image_paths=tuple(str(image["storage_path"]) for image in images),
                    )
                )
                if len(products) >= limit:
                    break
            return tuple(products)

    def set_inventory_state(self, model_code: str, state: str, *, verified_at: str | None = None) -> None:
        if state not in {"available", "reserved", "cleaning", "alteration", "offsite", "unverified"}:
            raise ValueError("unsupported inventory state")
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE catalog_products
                SET inventory_state = ?, last_verified_at = ?, updated_at = ?
                WHERE model_code = ?
                """,
                (state, verified_at or _now(), _now(), model_code.lower()),
            ).rowcount
            if not updated:
                raise ValueError("unknown model code")

    def add_inventory_block(
        self,
        model_code: str,
        starts_at: str,
        ends_at: str,
        block_type: str,
        *,
        notes: str = "",
    ) -> str:
        if block_type not in {"reserved", "cleaning", "alteration", "offsite"}:
            raise ValueError("unsupported inventory block type")
        if ends_at <= starts_at:
            raise ValueError("inventory block end must be after start")
        block_id = str(uuid4())
        with self._transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM catalog_products WHERE model_code = ?",
                (model_code.lower(),),
            ).fetchone()
            if not exists:
                raise ValueError("unknown model code")
            conflict = connection.execute(
                """
                SELECT 1 FROM catalog_inventory_blocks
                WHERE model_code = ? AND starts_at < ? AND ends_at > ?
                LIMIT 1
                """,
                (model_code.lower(), ends_at, starts_at),
            ).fetchone()
            if conflict:
                raise ValueError("inventory time conflicts with an existing block")
            connection.execute(
                """
                INSERT INTO catalog_inventory_blocks (
                    id, model_code, starts_at, ends_at, block_type, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (block_id, model_code.lower(), starts_at, ends_at, block_type, notes.strip(), _now()),
            )
        return block_id

    def is_available_for(self, model_code: str, starts_at: str, ends_at: str) -> bool:
        with closing(self._connect()) as connection:
            product = connection.execute(
                "SELECT lifecycle_state, inventory_state FROM catalog_products WHERE model_code = ?",
                (model_code.lower(),),
            ).fetchone()
            if not product or product["lifecycle_state"] != "active":
                return False
            if product["inventory_state"] not in {"available", "unverified"}:
                return False
            return not connection.execute(
                """
                SELECT 1 FROM catalog_inventory_blocks
                WHERE model_code = ? AND starts_at < ? AND ends_at > ?
                LIMIT 1
                """,
                (model_code.lower(), ends_at, starts_at),
            ).fetchone()

    def is_unit_available_for(self, unit_code: str, starts_at: str, ends_at: str) -> bool:
        _validate_time_window(starts_at, ends_at)
        with closing(self._connect()) as connection:
            unit = connection.execute(
                "SELECT inventory_state, condition_state FROM catalog_inventory_units WHERE unit_code = ?",
                (unit_code.strip().lower(),),
            ).fetchone()
            if not unit or unit["condition_state"] == "retired":
                return False
            if unit["inventory_state"] not in {"available", "unverified"}:
                return False
            return not connection.execute(
                """
                SELECT 1 FROM catalog_unit_blocks
                WHERE unit_code = ? AND status = 'active'
                  AND starts_at < ? AND ends_at > ?
                LIMIT 1
                """,
                (unit_code.strip().lower(), ends_at, starts_at),
            ).fetchone()

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            return {
                "products": int(connection.execute("SELECT COUNT(*) FROM catalog_products").fetchone()[0]),
                "images": int(connection.execute("SELECT COUNT(*) FROM catalog_product_images").fetchone()[0]),
                "drafts": int(connection.execute("SELECT COUNT(*) FROM catalog_import_drafts").fetchone()[0]),
            }

    def dashboard_summary(self) -> CatalogDashboardSummary:
        with closing(self._connect()) as connection:
            product_counts = {
                str(row["catalog_type"]): int(row["total"])
                for row in connection.execute(
                    """
                    SELECT catalog_type, COUNT(*) AS total
                    FROM catalog_products
                    WHERE lifecycle_state = 'active'
                    GROUP BY catalog_type
                    """
                ).fetchall()
            }
            unit_counts = {
                str(row["inventory_state"]): int(row["total"])
                for row in connection.execute(
                    """
                    SELECT inventory_state, COUNT(*) AS total
                    FROM catalog_inventory_units
                    GROUP BY inventory_state
                    """
                ).fetchall()
            }
            products = sum(product_counts.values())
            products_with_units = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT model_code) FROM catalog_inventory_units"
                ).fetchone()[0]
            )
            return CatalogDashboardSummary(
                products=products,
                bridal_products=product_counts.get("bridal", 0),
                lightwear_products=product_counts.get("lightwear", 0),
                images=int(
                    connection.execute("SELECT COUNT(*) FROM catalog_product_images").fetchone()[0]
                ),
                pending_drafts=int(
                    connection.execute(
                        "SELECT COUNT(*) FROM catalog_import_drafts WHERE status = 'pending_mapping'"
                    ).fetchone()[0]
                ),
                inventory_units=sum(unit_counts.values()),
                products_without_units=max(0, products - products_with_units),
                available_units=unit_counts.get("available", 0),
                reserved_units=unit_counts.get("reserved", 0),
                maintenance_units=(
                    unit_counts.get("cleaning", 0) + unit_counts.get("alteration", 0)
                ),
                offsite_units=unit_counts.get("offsite", 0),
                unverified_units=unit_counts.get("unverified", 0),
            )

    def internal_products(
        self,
        query: str = "",
        *,
        catalog_type: str = "",
        limit: int = 120,
    ) -> tuple[CatalogInternalProduct, ...]:
        if catalog_type and catalog_type not in {"bridal", "lightwear"}:
            raise ValueError("unsupported catalog type")
        search = f"%{query.strip().lower()}%"
        clauses = ["p.lifecycle_state = 'active'"]
        arguments: list[object] = []
        if query.strip():
            clauses.append(
                "LOWER(p.model_code || ' ' || p.brand_internal || ' ' || p.style_name || ' ' || p.description) LIKE ?"
            )
            arguments.append(search)
        if catalog_type:
            clauses.append("p.catalog_type = ?")
            arguments.append(catalog_type)
        arguments.append(max(1, min(limit, 500)))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT p.*
                FROM catalog_products p
                WHERE {' AND '.join(clauses)}
                ORDER BY p.catalog_type, p.model_code
                LIMIT ?
                """,
                tuple(arguments),
            ).fetchall()
            return tuple(self._internal_product_from_row(connection, row) for row in rows)

    def internal_product(self, model_code: str) -> CatalogInternalProduct:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM catalog_products WHERE model_code = ? AND lifecycle_state = 'active'",
                (model_code.lower(),),
            ).fetchone()
            if not row:
                raise ValueError("unknown model code")
            return self._internal_product_from_row(connection, row)

    def catalog_image_path(self, model_code: str, position: int = 0) -> Path:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT storage_path FROM catalog_product_images
                WHERE model_code = ? ORDER BY sort_order, id
                """,
                (model_code.lower(),),
            ).fetchall()
        if position < 0 or position >= len(rows):
            raise ValueError("catalog image is unavailable")
        root = self.catalog_root.resolve()
        image_path = (root / str(rows[position]["storage_path"])).resolve()
        if not image_path.is_relative_to(root) or not image_path.is_file():
            raise ValueError("catalog image is unavailable")
        return image_path

    def add_inventory_unit(
        self,
        model_code: str,
        *,
        unit_code: str = "",
        size_label: str = "",
        bust_cm: float | None = None,
        waist_cm: float | None = None,
        hip_cm: float | None = None,
        dress_length_cm: float | None = None,
        location: str = "",
        inventory_state: str = "unverified",
        condition_state: str = "good",
        notes: str = "",
    ) -> str:
        model_code = model_code.strip().lower()
        _validate_unit_state(inventory_state, condition_state)
        now = _now()
        with self._transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM catalog_products WHERE model_code = ? AND lifecycle_state = 'active'",
                (model_code,),
            ).fetchone():
                raise ValueError("unknown model code")
            final_unit_code = unit_code.strip().lower() or self._next_unit_code(connection, model_code)
            try:
                connection.execute(
                    """
                    INSERT INTO catalog_inventory_units (
                        unit_code, model_code, size_label, bust_cm, waist_cm, hip_cm,
                        dress_length_cm, location, condition_state, inventory_state,
                        notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        final_unit_code, model_code, size_label.strip(), bust_cm, waist_cm,
                        hip_cm, dress_length_cm, location.strip(), condition_state,
                        inventory_state, notes.strip(), now, now,
                    ),
                )
                self._record_event(
                    connection,
                    "unit_created",
                    model_code=model_code,
                    unit_code=final_unit_code,
                    notes=size_label.strip(),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("实物编号已存在或资料不完整") from error
        return final_unit_code

    def update_inventory_unit(
        self,
        unit_code: str,
        *,
        size_label: str,
        bust_cm: float | None,
        waist_cm: float | None,
        hip_cm: float | None,
        dress_length_cm: float | None,
        location: str,
        inventory_state: str,
        condition_state: str,
        notes: str = "",
    ) -> None:
        _validate_unit_state(inventory_state, condition_state)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT model_code, inventory_state FROM catalog_inventory_units WHERE unit_code = ?",
                (unit_code.strip().lower(),),
            ).fetchone()
            if not existing:
                raise ValueError("unknown inventory unit")
            updated = connection.execute(
                """
                UPDATE catalog_inventory_units
                SET size_label = ?, bust_cm = ?, waist_cm = ?, hip_cm = ?, dress_length_cm = ?,
                    location = ?, inventory_state = ?, condition_state = ?,
                    notes = ?, last_verified_at = ?, updated_at = ?
                WHERE unit_code = ?
                """,
                (
                    size_label.strip(), bust_cm, waist_cm, hip_cm, dress_length_cm,
                    location.strip(), inventory_state, condition_state,
                    notes.strip(), _now(), _now(), unit_code.strip().lower(),
                ),
            ).rowcount
            if not updated:
                raise ValueError("unknown inventory unit")
            event_type = (
                "state_changed"
                if str(existing["inventory_state"]) != inventory_state
                else "unit_updated"
            )
            self._record_event(
                connection,
                event_type,
                model_code=str(existing["model_code"]),
                unit_code=unit_code.strip().lower(),
                notes=f"{existing['inventory_state']}->{inventory_state}",
            )

    def unit_blocks(
        self,
        *,
        starts_before: str = "",
        ends_after: str = "",
        include_cancelled: bool = False,
    ) -> tuple[CatalogUnitBlock, ...]:
        clauses = [] if include_cancelled else ["b.status = 'active'"]
        arguments: list[object] = []
        if starts_before:
            clauses.append("b.starts_at < ?")
            arguments.append(starts_before)
        if ends_after:
            clauses.append("b.ends_at > ?")
            arguments.append(ends_after)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT b.*, u.model_code, u.size_label, p.style_name
                FROM catalog_unit_blocks b
                JOIN catalog_inventory_units u ON u.unit_code = b.unit_code
                JOIN catalog_products p ON p.model_code = u.model_code
                {where}
                ORDER BY b.starts_at, b.ends_at, b.unit_code
                """,
                tuple(arguments),
            ).fetchall()
        return tuple(
            CatalogUnitBlock(
                id=str(row["id"]),
                unit_code=str(row["unit_code"]),
                model_code=str(row["model_code"]),
                style_name=str(row["style_name"]),
                size_label=str(row["size_label"]),
                starts_at=str(row["starts_at"]),
                ends_at=str(row["ends_at"]),
                block_type=str(row["block_type"]),
                notes=str(row["notes"]),
                status=str(row["status"]),
            )
            for row in rows
        )

    def add_unit_block(
        self,
        unit_code: str,
        starts_at: str,
        ends_at: str,
        block_type: str,
        *,
        notes: str = "",
    ) -> str:
        if block_type not in {"reserved", "cleaning", "alteration", "offsite"}:
            raise ValueError("unsupported unit block type")
        _validate_time_window(starts_at, ends_at)
        block_id = str(uuid4())
        with self._transaction() as connection:
            unit_code = unit_code.strip().lower()
            if not connection.execute(
                "SELECT 1 FROM catalog_inventory_units WHERE unit_code = ?",
                (unit_code,),
            ).fetchone():
                raise ValueError("实物件不存在")
            conflict = connection.execute(
                """
                SELECT 1 FROM catalog_unit_blocks
                WHERE unit_code = ? AND status = 'active'
                  AND starts_at < ? AND ends_at > ?
                LIMIT 1
                """,
                (unit_code, ends_at, starts_at),
            ).fetchone()
            if conflict:
                raise ValueError("该实物件在这个时间段已有安排")
            connection.execute(
                """
                INSERT INTO catalog_unit_blocks (
                    id, unit_code, starts_at, ends_at, block_type, notes, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (block_id, unit_code, starts_at, ends_at, block_type, notes.strip(), _now()),
            )
            model_code = str(
                connection.execute(
                    "SELECT model_code FROM catalog_inventory_units WHERE unit_code = ?",
                    (unit_code,),
                ).fetchone()[0]
            )
            self._record_event(
                connection,
                "booking_created" if block_type == "reserved" else "flow_created",
                model_code=model_code,
                unit_code=unit_code,
                notes=block_type,
            )
        return block_id

    def cancel_unit_block(self, block_id: str) -> None:
        with self._transaction() as connection:
            block = connection.execute(
                """
                SELECT b.block_type, b.unit_code, u.model_code
                FROM catalog_unit_blocks b
                JOIN catalog_inventory_units u ON u.unit_code = b.unit_code
                WHERE b.id = ? AND b.status = 'active'
                """,
                (block_id.strip(),),
            ).fetchone()
            if not block:
                raise ValueError("档期不存在或已经取消")
            updated = connection.execute(
                """
                UPDATE catalog_unit_blocks
                SET status = 'cancelled', cancelled_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (_now(), block_id.strip()),
            ).rowcount
            if not updated:
                raise ValueError("档期不存在或已经取消")
            self._record_event(
                connection,
                "booking_cancelled" if block["block_type"] == "reserved" else "flow_cancelled",
                model_code=str(block["model_code"]),
                unit_code=str(block["unit_code"]),
                notes=str(block["block_type"]),
            )

    def calendar_appointments(
        self,
        week_start: date,
        *,
        days: int = 7,
    ) -> tuple[CatalogCalendarAppointment, ...]:
        if days < 1 or days > 31:
            raise ValueError("calendar range must be between 1 and 31 days")
        range_start = datetime.combine(week_start, datetime.min.time(), CHINA_TIMEZONE)
        range_end = range_start + timedelta(days=days)
        with closing(self._connect()) as connection:
            if not _table_exists(connection, "appointments"):
                return ()
            rows = connection.execute(
                """
                SELECT a.*,
                       (SELECT COUNT(*) FROM catalog_appointment_styles s
                        WHERE s.appointment_id = a.id) AS selected_styles
                FROM appointments a
                WHERE a.status NOT IN ('cancelled')
                ORDER BY a.created_at DESC
                """
            ).fetchall()
        appointments: list[CatalogCalendarAppointment] = []
        for row in rows:
            appointment = _calendar_appointment_from_row(row)
            if not appointment.parsed:
                appointments.append(appointment)
                continue
            starts_at = datetime.fromisoformat(appointment.starts_at)
            if range_start <= starts_at < range_end:
                appointments.append(appointment)
        appointments.sort(
            key=lambda item: (
                not item.parsed,
                item.starts_at or "9999",
                item.customer_name,
            )
        )
        return tuple(appointments)

    def calendar_appointment(self, appointment_id: str) -> CatalogCalendarAppointment:
        with closing(self._connect()) as connection:
            if not _table_exists(connection, "appointments"):
                raise ValueError("预约不存在")
            row = connection.execute(
                """
                SELECT a.*,
                       (SELECT COUNT(*) FROM catalog_appointment_styles s
                        WHERE s.appointment_id = a.id) AS selected_styles
                FROM appointments a WHERE a.id = ?
                """,
                (appointment_id.strip(),),
            ).fetchone()
        if not row:
            raise ValueError("预约不存在")
        return _calendar_appointment_from_row(row)

    def add_manual_appointment(
        self,
        *,
        customer_name: str,
        customer_phone: str,
        starts_at: str,
        status: str = "time_pending_staff",
    ) -> str:
        customer_name = customer_name.strip()
        customer_phone = customer_phone.strip()
        if not customer_name:
            raise ValueError("请填写顾客姓名")
        if not customer_phone:
            raise ValueError("请填写联系方式")
        if status not in {"time_pending_staff", "awaiting_payment", "confirmed"}:
            raise ValueError("预约状态无效")
        try:
            appointment_time = datetime.fromisoformat(starts_at.strip())
        except ValueError as error:
            raise ValueError("请选择有效的预约日期和时间") from error
        if appointment_time.tzinfo is None:
            appointment_time = appointment_time.replace(tzinfo=CHINA_TIMEZONE)
        appointment_id = f"manual-{uuid4().hex}"
        conversation_id = f"manual-conversation-{uuid4().hex}"
        now = _now()
        time_text = appointment_time.astimezone(CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M")
        with self._transaction() as connection:
            if not _table_exists(connection, "appointments"):
                raise ValueError("预约数据表尚未初始化")
            if _table_exists(connection, "conversations"):
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id, channel, channel_account_id, external_user_id,
                        owner, created_at, updated_at
                    ) VALUES (?, 'staff_manual', 'inventory_calendar', ?, 'human', ?, ?)
                    """,
                    (conversation_id, appointment_id, now, now),
                )
            connection.execute(
                """
                INSERT INTO appointments (
                    id, conversation_id, status, customer_name, customer_phone,
                    requested_time_text, confirmed_time_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    appointment_id,
                    conversation_id,
                    status,
                    customer_name,
                    customer_phone,
                    time_text,
                    time_text if status == "confirmed" else "",
                    now,
                    now,
                ),
            )
        return appointment_id

    def appointment_style_options(
        self,
        appointment_id: str,
        *,
        query: str = "",
    ) -> tuple[CatalogAppointmentStyleAvailability, ...]:
        appointment = self.calendar_appointment(appointment_id)
        terms = [term.casefold() for term in query.split() if term.strip()]
        with closing(self._connect()) as connection:
            selected = {
                str(row[0])
                for row in connection.execute(
                    "SELECT model_code FROM catalog_appointment_styles WHERE appointment_id = ?",
                    (appointment.id,),
                ).fetchall()
            }
            rows = connection.execute(
                """
                SELECT model_code, style_name, catalog_type, brand_internal, description
                FROM catalog_products
                WHERE lifecycle_state = 'active'
                ORDER BY catalog_type, model_code
                """
            ).fetchall()
            options: list[CatalogAppointmentStyleAvailability] = []
            for row in rows:
                haystack = " ".join(str(row[key]) for key in row.keys()).casefold()
                if terms and not all(term in haystack for term in terms):
                    continue
                availability, note, total_units, available_units = _style_availability(
                    connection,
                    str(row["model_code"]),
                    appointment.starts_at,
                    appointment.ends_at,
                )
                options.append(
                    CatalogAppointmentStyleAvailability(
                        model_code=str(row["model_code"]),
                        style_name=str(row["style_name"]),
                        brand_internal=str(row["brand_internal"]),
                        catalog_type=str(row["catalog_type"]),
                        selected=str(row["model_code"]) in selected,
                        availability=availability,
                        availability_note=note,
                        total_units=total_units,
                        available_units=available_units,
                    )
                )
        return tuple(options)

    def set_appointment_style_interests(
        self,
        appointment_id: str,
        model_codes: tuple[str, ...],
    ) -> None:
        clean_codes = tuple(dict.fromkeys(code.strip().lower() for code in model_codes if code.strip()))
        if len(clean_codes) > 6:
            raise ValueError("一次最多选择 6 款")
        with self._transaction() as connection:
            if not _table_exists(connection, "appointments") or not connection.execute(
                "SELECT 1 FROM appointments WHERE id = ?",
                (appointment_id.strip(),),
            ).fetchone():
                raise ValueError("预约不存在")
            if clean_codes:
                placeholders = ",".join("?" for _ in clean_codes)
                valid = {
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT model_code FROM catalog_products WHERE lifecycle_state = 'active' AND model_code IN ({placeholders})",
                        clean_codes,
                    ).fetchall()
                }
                if valid != set(clean_codes):
                    raise ValueError("包含无效款式")
            existing = {
                str(row[0])
                for row in connection.execute(
                    "SELECT model_code FROM catalog_appointment_styles WHERE appointment_id = ?",
                    (appointment_id.strip(),),
                ).fetchall()
            }
            connection.execute(
                "DELETE FROM catalog_appointment_styles WHERE appointment_id = ?",
                (appointment_id.strip(),),
            )
            now = _now()
            connection.executemany(
                "INSERT INTO catalog_appointment_styles (appointment_id, model_code, selected_at) VALUES (?, ?, ?)",
                ((appointment_id.strip(), code, now) for code in clean_codes),
            )
            for code in set(clean_codes) - existing:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO catalog_inventory_events (
                        id, event_type, model_code, unit_code, source, notes, occurred_at
                    ) VALUES (?, 'shortlisted', ?, NULL, 'staff_calendar', ?, ?)
                    """,
                    (f"shortlist:{appointment_id}:{code}", code, f"appointment_id={appointment_id}", now),
                )

    def record_demand_event(
        self,
        event_type: str,
        model_code: str,
        *,
        source: str,
        notes: str = "",
    ) -> str:
        if event_type not in {"inquiry", "recommended", "try_on", "lost_demand"}:
            raise ValueError("unsupported demand event type")
        with self._transaction() as connection:
            model_code = model_code.strip().lower()
            if not connection.execute(
                "SELECT 1 FROM catalog_products WHERE model_code = ? AND lifecycle_state = 'active'",
                (model_code,),
            ).fetchone():
                raise ValueError("unknown model code")
            return self._record_event(
                connection,
                event_type,
                model_code=model_code,
                source=source.strip() or "system",
                notes=notes,
            )

    def analytics_summary(self, *, window_days: int = 90) -> CatalogAnalyticsSummary:
        if window_days not in {30, 90, 180, 365}:
            raise ValueError("unsupported analytics window")
        now = datetime.now(timezone.utc)
        starts_at = (now - timedelta(days=window_days)).isoformat()
        with closing(self._connect()) as connection:
            active_products = int(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_products WHERE lifecycle_state = 'active'"
                ).fetchone()[0]
            )
            inventory_units = int(
                connection.execute("SELECT COUNT(*) FROM catalog_inventory_units").fetchone()[0]
            )
            products_with_units = int(
                connection.execute(
                    "SELECT COUNT(DISTINCT model_code) FROM catalog_inventory_units"
                ).fetchone()[0]
            )
            event_counts = {
                str(row["event_type"]): int(row["total"])
                for row in connection.execute(
                    """
                    SELECT event_type, COUNT(*) AS total
                    FROM catalog_inventory_events
                    WHERE occurred_at >= ?
                    GROUP BY event_type
                    """,
                    (starts_at,),
                ).fetchall()
            }
            demand_events = sum(
                event_counts.get(name, 0)
                for name in ("inquiry", "recommended", "shortlisted", "try_on", "lost_demand")
            )
            operational_events = sum(event_counts.values()) - demand_events
            active_future_bookings = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM catalog_unit_blocks
                    WHERE status = 'active' AND block_type = 'reserved' AND ends_at >= ?
                    """,
                    (datetime.now().strftime("%Y-%m-%dT%H:%M"),),
                ).fetchone()[0]
            )
            first_event = connection.execute(
                "SELECT MIN(occurred_at) FROM catalog_inventory_events"
            ).fetchone()[0]
            tracked_days = 0
            if first_event:
                try:
                    tracked_days = max(1, (now - datetime.fromisoformat(str(first_event))).days + 1)
                except ValueError:
                    tracked_days = 0
            rows = connection.execute(
                """
                WITH unit_counts AS (
                    SELECT model_code, COUNT(*) AS inventory_units
                    FROM catalog_inventory_units
                    GROUP BY model_code
                ), event_counts AS (
                    SELECT
                        model_code,
                        SUM(CASE WHEN event_type IN ('inquiry','recommended','shortlisted','try_on','lost_demand') THEN 1 ELSE 0 END) AS demand_signals,
                        SUM(CASE WHEN event_type = 'try_on' THEN 1 ELSE 0 END) AS try_ons,
                        MAX(0,
                            SUM(CASE WHEN event_type = 'booking_created' THEN 1 ELSE 0 END) -
                            SUM(CASE WHEN event_type = 'booking_cancelled' THEN 1 ELSE 0 END)
                        ) AS effective_bookings,
                        SUM(CASE WHEN event_type = 'lost_demand' THEN 1 ELSE 0 END) AS lost_demand
                    FROM catalog_inventory_events
                    WHERE occurred_at >= ?
                    GROUP BY model_code
                )
                SELECT
                    p.model_code, p.style_name, p.catalog_type,
                    COALESCE(u.inventory_units, 0) AS inventory_units,
                    COALESCE(e.demand_signals, 0) AS demand_signals,
                    COALESCE(e.try_ons, 0) AS try_ons,
                    COALESCE(e.effective_bookings, 0) AS effective_bookings,
                    COALESCE(e.lost_demand, 0) AS lost_demand
                FROM catalog_products p
                LEFT JOIN unit_counts u ON u.model_code = p.model_code
                LEFT JOIN event_counts e ON e.model_code = p.model_code
                WHERE p.lifecycle_state = 'active'
                ORDER BY p.model_code
                """,
                (starts_at,),
            ).fetchall()
        coverage_percent = round(products_with_units / active_products * 100) if active_products else 0
        if tracked_days >= 90 and demand_events >= 100 and coverage_percent >= 80:
            confidence = "reliable"
        elif tracked_days >= 30 and demand_events >= 20 and coverage_percent >= 50:
            confidence = "developing"
        else:
            confidence = "insufficient"
        utilization_rows = tuple(
            CatalogUtilizationRow(
                model_code=str(row["model_code"]),
                style_name=str(row["style_name"]),
                catalog_type=str(row["catalog_type"]),
                inventory_units=int(row["inventory_units"] or 0),
                demand_signals=int(row["demand_signals"] or 0),
                try_ons=int(row["try_ons"] or 0),
                effective_bookings=int(row["effective_bookings"] or 0),
                lost_demand=int(row["lost_demand"] or 0),
                demand_score=(
                    int(row["demand_signals"] or 0)
                    + int(row["try_ons"] or 0) * 2
                    + int(row["effective_bookings"] or 0) * 5
                    + int(row["lost_demand"] or 0) * 3
                ),
            )
            for row in rows
        )
        return CatalogAnalyticsSummary(
            window_days=window_days,
            tracked_days=tracked_days,
            active_products=active_products,
            inventory_units=inventory_units,
            products_with_units=products_with_units,
            demand_events=demand_events,
            operational_events=operational_events,
            active_future_bookings=active_future_bookings,
            coverage_percent=coverage_percent,
            confidence=confidence,
            rows=utilization_rows,
        )

    def planning_summary(self, *, window_days: int = 90) -> CatalogPlanningSummary:
        analytics = self.analytics_summary(window_days=window_days)
        with closing(self._connect()) as connection:
            units_without_size = int(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_inventory_units WHERE TRIM(size_label) = ''"
                ).fetchone()[0]
            )
            units_without_measurements = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM catalog_inventory_units
                    WHERE bust_cm IS NULL OR waist_cm IS NULL OR hip_cm IS NULL
                    """
                ).fetchone()[0]
            )

        products_without_units = max(
            0,
            analytics.active_products - analytics.products_with_units,
        )
        data_gaps: list[str] = []
        if products_without_units:
            data_gaps.append(f"{products_without_units} 款尚未建立实体件档案")
        if units_without_size:
            data_gaps.append(f"{units_without_size} 件实体缺少尺码")
        if units_without_measurements:
            data_gaps.append(f"{units_without_measurements} 件实体缺少完整三围")
        if analytics.tracked_days < 30:
            data_gaps.append(f"需求数据仅积累 {analytics.tracked_days} 天，至少需要 30 天")
        if analytics.demand_events < 20:
            data_gaps.append(f"当前仅有 {analytics.demand_events} 条需求信号，至少需要 20 条")
        if analytics.coverage_percent < 50:
            data_gaps.append(f"实体覆盖率为 {analytics.coverage_percent}%，至少需要 50%")

        recommendations: list[CatalogPlanningRecommendation] = []
        observations: list[CatalogPlanningRecommendation] = []
        for row in analytics.rows:
            if row.demand_signals <= 0 and row.effective_bookings <= 0:
                if (
                    analytics.confidence == "reliable"
                    and row.inventory_units > 0
                    and analytics.tracked_days >= 90
                ):
                    recommendations.append(
                        _planning_item(
                            row,
                            category="low_use",
                            priority="low",
                            reason="统计周期内没有咨询、试纱或有效预订，建议复核陈列与保留数量",
                        )
                    )
                continue

            high_pressure = (
                row.lost_demand >= 2
                or (row.inventory_units == 0 and row.demand_signals >= 3)
                or row.demand_score >= max(10, row.inventory_units * 8)
            )
            if analytics.confidence != "insufficient" and high_pressure:
                reasons: list[str] = []
                if row.inventory_units == 0:
                    reasons.append("尚无实体件")
                if row.lost_demand:
                    reasons.append(f"出现 {row.lost_demand} 次需求流失")
                if row.effective_bookings:
                    reasons.append(f"已有 {row.effective_bookings} 次有效预订")
                reasons.append(f"累计 {row.demand_signals} 条需求信号")
                recommendations.append(
                    _planning_item(
                        row,
                        category="replenish",
                        priority="high" if row.lost_demand >= 2 or row.inventory_units == 0 else "medium",
                        reason="，".join(reasons),
                    )
                )
            else:
                observations.append(
                    _planning_item(
                        row,
                        category="observe",
                        priority="medium" if high_pressure else "low",
                        reason=(
                            f"已出现 {row.demand_signals} 条需求信号、"
                            f"{row.effective_bookings} 次有效预订；数据门槛达到后再判断是否补充"
                        ),
                    )
                )

        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(
            key=lambda item: (
                priority_order[item.priority],
                -item.lost_demand,
                -item.demand_signals,
                item.model_code,
            )
        )
        observations.sort(key=lambda item: (-item.demand_signals, item.model_code))
        return CatalogPlanningSummary(
            window_days=analytics.window_days,
            tracked_days=analytics.tracked_days,
            confidence=analytics.confidence,
            coverage_percent=analytics.coverage_percent,
            active_products=analytics.active_products,
            inventory_units=analytics.inventory_units,
            products_without_units=products_without_units,
            units_without_size=units_without_size,
            units_without_measurements=units_without_measurements,
            recommendations=tuple(recommendations),
            observations=tuple(observations[:12]),
            data_gaps=tuple(data_gaps),
        )

    def internal_prices(self, model_code: str) -> tuple[int | None, int | None]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT rental_price_yuan, purchase_price_yuan FROM catalog_products WHERE model_code = ?",
                (model_code.lower(),),
            ).fetchone()
            if not row:
                raise ValueError("unknown model code")
            return row["rental_price_yuan"], row["purchase_price_yuan"]

    def _replace_images(self, connection: sqlite3.Connection, model_code: str, paths: str, now: str) -> None:
        connection.execute("DELETE FROM catalog_product_images WHERE model_code = ?", (model_code,))
        for position, relative_path in enumerate(item.strip() for item in paths.split(";") if item.strip()):
            image_path = self.catalog_root / relative_path
            if not image_path.is_file():
                raise ValueError(f"catalog image is missing: {relative_path}")
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            connection.execute(
                """
                INSERT INTO catalog_product_images (id, model_code, storage_path, sha256, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), model_code, relative_path, digest, position, now),
            )

    def _internal_product_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> CatalogInternalProduct:
        images = connection.execute(
            "SELECT storage_path FROM catalog_product_images WHERE model_code = ? ORDER BY sort_order, id",
            (row["model_code"],),
        ).fetchall()
        unit_rows = connection.execute(
            "SELECT * FROM catalog_inventory_units WHERE model_code = ? ORDER BY unit_code",
            (row["model_code"],),
        ).fetchall()
        return CatalogInternalProduct(
            model_code=str(row["model_code"]),
            catalog_type=str(row["catalog_type"]),
            brand_internal=str(row["brand_internal"]),
            style_name=str(row["style_name"]),
            description=str(row["description"]),
            rental_price_yuan=row["rental_price_yuan"],
            purchase_price_yuan=row["purchase_price_yuan"],
            rental_available=bool(row["rental_available"]),
            purchase_or_custom_available=bool(row["purchase_or_custom_available"]),
            image_paths=tuple(str(image["storage_path"]) for image in images),
            units=tuple(
                CatalogInventoryUnit(
                    unit_code=str(unit["unit_code"]),
                    model_code=str(unit["model_code"]),
                    size_label=str(unit["size_label"]),
                    bust_cm=unit["bust_cm"],
                    waist_cm=unit["waist_cm"],
                    hip_cm=unit["hip_cm"],
                    dress_length_cm=unit["dress_length_cm"],
                    location=str(unit["location"]),
                    condition_state=str(unit["condition_state"]),
                    inventory_state=str(unit["inventory_state"]),
                    notes=str(unit["notes"]),
                )
                for unit in unit_rows
            ),
        )

    @staticmethod
    def _next_unit_code(connection: sqlite3.Connection, model_code: str) -> str:
        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT unit_code FROM catalog_inventory_units WHERE model_code = ?",
                (model_code,),
            ).fetchall()
        }
        position = 1
        while f"{model_code}-{position:02d}" in existing:
            position += 1
        return f"{model_code}-{position:02d}"

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        event_type: str,
        *,
        model_code: str | None = None,
        unit_code: str | None = None,
        source: str = "system",
        notes: str = "",
    ) -> str:
        event_id = str(uuid4())
        connection.execute(
            """
            INSERT INTO catalog_inventory_events (
                id, event_type, model_code, unit_code, source, notes, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_type,
                model_code,
                unit_code,
                source.strip() or "system",
                notes.strip(),
                _now(),
            ),
        )
        return event_id

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _ensure_schema_migrations(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(catalog_unit_blocks)").fetchall()
        }
        if "status" not in columns:
            connection.execute(
                "ALTER TABLE catalog_unit_blocks ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
        if "cancelled_at" not in columns:
            connection.execute("ALTER TABLE catalog_unit_blocks ADD COLUMN cancelled_at TEXT")

    def _transaction(self):
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        return _Transaction(connection)


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    )


def _calendar_appointment_from_row(row: sqlite3.Row) -> CatalogCalendarAppointment:
    status = str(row["status"])
    proposed = str(row["proposed_reschedule_text"] or "")
    confirmed = str(row["confirmed_time_text"] or "")
    requested = str(row["requested_time_text"] or "")
    time_text = (
        proposed
        if status == "reschedule_pending_staff" and proposed
        else confirmed or requested or proposed
    )
    starts = _parse_appointment_time(time_text)
    ends = starts + timedelta(hours=2) if starts else None
    return CatalogCalendarAppointment(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        customer_name=str(row["customer_name"] or ""),
        customer_phone=str(row["customer_phone"] or ""),
        status=status,
        time_text=time_text,
        starts_at=starts.isoformat(timespec="minutes") if starts else "",
        ends_at=ends.isoformat(timespec="minutes") if ends else "",
        parsed=starts is not None,
        selected_styles=int(row["selected_styles"] or 0),
    )


def _parse_appointment_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    iso_match = re.search(
        r"(?P<year>20\d{2})[-/年](?P<month>\d{1,2})[-/月](?P<day>\d{1,2})(?:日)?[ T]+(?P<hour>\d{1,2})(?::(?P<minute>\d{1,2}))?",
        text,
    )
    if iso_match:
        try:
            return datetime(
                int(iso_match.group("year")),
                int(iso_match.group("month")),
                int(iso_match.group("day")),
                int(iso_match.group("hour")),
                int(iso_match.group("minute") or 0),
                tzinfo=CHINA_TIMEZONE,
            )
        except ValueError:
            return None

    chinese_match = re.search(
        r"(?:(?P<year>20\d{2})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})(?:日|号)?"
        r".*?(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上)?"
        r"(?P<hour>[零〇一二两三四五六七八九十\d]{1,3})点"
        r"(?P<minute>半|一刻|三刻|[零〇一二两三四五六七八九十\d]{1,3}分)?",
        text,
    )
    if not chinese_match:
        return None
    try:
        year = int(chinese_match.group("year") or datetime.now(CHINA_TIMEZONE).year)
        month = int(chinese_match.group("month"))
        day = int(chinese_match.group("day"))
        hour = _chinese_number(chinese_match.group("hour"))
        minute_text = chinese_match.group("minute") or ""
        minute = 30 if minute_text == "半" else 15 if minute_text == "一刻" else 45 if minute_text == "三刻" else _chinese_number(minute_text.removesuffix("分")) if minute_text else 0
        if chinese_match.group("period") in {"下午", "傍晚", "晚上"} and hour < 12:
            hour += 12
        if chinese_match.group("period") == "中午" and hour < 11:
            hour += 12
        return datetime(year, month, day, hour, minute, tzinfo=CHINA_TIMEZONE)
    except (TypeError, ValueError):
        return None


def _chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    if value in digits:
        return digits[value]
    raise ValueError("unsupported Chinese number")


def _style_availability(
    connection: sqlite3.Connection,
    model_code: str,
    starts_at: str,
    ends_at: str,
) -> tuple[str, str, int, int]:
    units = connection.execute(
        """
        SELECT unit_code, inventory_state, condition_state, location
        FROM catalog_inventory_units WHERE model_code = ?
        ORDER BY unit_code
        """,
        (model_code,),
    ).fetchall()
    if not units:
        return "no_units", "尚未建立实体件档案", 0, 0
    available = 0
    needs_verification = 0
    conflicts = 0
    offsite = 0
    maintenance = 0
    for unit in units:
        state = str(unit["inventory_state"])
        condition = str(unit["condition_state"])
        if state == "offsite":
            offsite += 1
            continue
        if state in {"cleaning", "alteration"} or condition in {"repair_needed", "retired"}:
            maintenance += 1
            continue
        has_conflict = False
        if starts_at and ends_at:
            has_conflict = bool(
                connection.execute(
                    """
                    SELECT 1 FROM catalog_unit_blocks
                    WHERE unit_code = ? AND status = 'active'
                      AND starts_at < ? AND ends_at > ?
                    LIMIT 1
                    """,
                    (str(unit["unit_code"]), ends_at, starts_at),
                ).fetchone()
            )
        if has_conflict or state == "reserved":
            conflicts += 1
        elif state == "available":
            available += 1
        else:
            needs_verification += 1
    total = len(units)
    if available:
        note = f"{available}/{total} 件在店且该时段无冲突" if starts_at else f"{available}/{total} 件当前标记在店"
        return "available", note, total, available
    if needs_verification:
        note = "有实体件，但需先核对位置" if starts_at else "时间和实体位置都需要核对"
        return "verify", note, total, 0
    if conflicts:
        return "conflict", f"{conflicts}/{total} 件在该时段已占用", total, 0
    if offsite == total:
        return "offsite", "全部实体件当前不在店", total, 0
    if maintenance:
        return "maintenance", "实体件正在清洗、修改或维修", total, 0
    return "verify", "需要店员核对实体位置", total, 0


def _planning_item(
    row: CatalogUtilizationRow,
    *,
    category: str,
    priority: str,
    reason: str,
) -> CatalogPlanningRecommendation:
    return CatalogPlanningRecommendation(
        model_code=row.model_code,
        style_name=row.style_name,
        category=category,
        priority=priority,
        reason=reason,
        demand_signals=row.demand_signals,
        effective_bookings=row.effective_bookings,
        lost_demand=row.lost_demand,
        inventory_units=row.inventory_units,
    )


class _Transaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type:
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required(row: dict[str, str], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ValueError(f"manifest requires {key}")
    return value


def _money(value: str) -> int | None:
    value = value.strip()
    # Source price lists use text such as "不卖" for rental-only products.
    # Keep those prices unset rather than inventing a numeric value.
    return int(value) if value.isdecimal() else None


def _yes(value: str) -> int:
    return int(value.strip().lower() in {"yes", "true", "1"})


def _validate_unit_state(inventory_state: str, condition_state: str) -> None:
    if inventory_state not in {
        "available", "reserved", "cleaning", "alteration", "offsite", "unverified"
    }:
        raise ValueError("unsupported inventory state")
    if condition_state not in {"excellent", "good", "repair_needed", "retired"}:
        raise ValueError("unsupported condition state")


def _validate_time_window(starts_at: str, ends_at: str) -> None:
    try:
        start = datetime.fromisoformat(starts_at)
        end = datetime.fromisoformat(ends_at)
    except ValueError as error:
        raise ValueError("开始和结束时间格式不正确") from error
    if end <= start:
        raise ValueError("结束时间必须晚于开始时间")
    if end - start > timedelta(days=180):
        raise ValueError("单次档期不能超过 180 天")
