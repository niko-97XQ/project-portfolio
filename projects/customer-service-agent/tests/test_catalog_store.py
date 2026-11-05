from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.infrastructure.catalog_store import CatalogStore  # noqa: E402
from forthatday_agent.infrastructure.catalog_knowledge import CatalogKnowledgeProvider  # noqa: E402
from forthatday_agent.infrastructure.markdown_knowledge import MarkdownKnowledgeProvider  # noqa: E402


class CatalogStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "images" / "bridal").mkdir(parents=True)
        (self.root / "images" / "bridal" / "bau-01.jpg").write_bytes(b"dress-image")
        self.manifest = self.root / "bridal.csv"
        with self.manifest.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=(
                    "catalog_type", "brand_internal", "style_name", "model_code", "description",
                    "rental_price_internal", "purchase_price_internal", "rental_available",
                    "purchase_or_custom_available", "image_path", "source_document",
                ),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "catalog_type": "bridal",
                    "brand_internal": "Bride and You",
                    "style_name": "Verona 无袖",
                    "model_code": "bau-01",
                    "description": "无袖缎面婚纱 简约",
                    "rental_price_internal": "6600",
                    "purchase_price_internal": "15800",
                    "rental_available": "yes",
                    "purchase_or_custom_available": "yes",
                    "image_path": "images/bridal/bau-01.jpg",
                    "source_document": "catalog.docx",
                }
            )
        self.store = CatalogStore(self.root / "catalog.db", catalog_root=self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_import_keeps_private_price_out_of_customer_safe_product(self) -> None:
        self.assertEqual(self.store.import_verified_manifest(self.manifest), 1)

        product = self.store.customer_safe_products("缎面")[0]

        self.assertEqual(product.model_code, "bau-01")
        self.assertEqual(product.image_paths, ("images/bridal/bau-01.jpg",))
        self.assertNotIn("price", product.__dataclass_fields__)
        self.assertNotIn("brand", product.__dataclass_fields__)
        self.assertEqual(self.store.internal_prices("bau-01"), (6600, 15800))

    def test_catalog_knowledge_hides_brand_until_customer_mentions_it(self) -> None:
        self.store.import_verified_manifest(self.manifest)
        knowledge_root = self.root / "knowledge"
        knowledge_root.mkdir()
        provider = CatalogKnowledgeProvider(
            MarkdownKnowledgeProvider(knowledge_root),
            self.store,
        )

        generic = provider.search("想看看缎面婚纱")
        branded = provider.search("Bride and You 的 Verona 是什么款式")

        self.assertEqual(generic[0]["catalog_ref"], "bau-01")
        self.assertNotIn("Bride and You", generic[0]["text"])
        self.assertNotIn("6600", generic[0]["text"])
        self.assertIn("Bride and You", branded[0]["text"])
        self.assertIn("必须查询实体库存", generic[0]["text"])

    def test_catalog_knowledge_matches_explainable_preference_tags(self) -> None:
        self.store.import_verified_manifest(self.manifest)
        connection = sqlite3.connect(self.store.database_path)
        try:
            connection.execute(
                "UPDATE catalog_products SET description = ? WHERE model_code = 'bau-01'",
                ("缎面婚纱",),
            )
            connection.commit()
        finally:
            connection.close()
        knowledge_root = self.root / "knowledge-tags"
        knowledge_root.mkdir()
        provider = CatalogKnowledgeProvider(
            MarkdownKnowledgeProvider(knowledge_root),
            self.store,
        )

        result = provider.search("想看简约干净的婚纱")

        self.assertEqual(result[0]["catalog_ref"], "bau-01")
        self.assertIn("简约利落", result[0]["recommendation_tags"])
        self.assertEqual(result[0]["image_available"], "true")

    def test_planning_withholds_advice_until_data_thresholds_are_met(self) -> None:
        self.store.import_verified_manifest(self.manifest)

        summary = self.store.planning_summary(window_days=90)

        self.assertEqual(summary.confidence, "insufficient")
        self.assertEqual(summary.recommendations, ())
        self.assertTrue(any("尚未建立实体件档案" in item for item in summary.data_gaps))

    def test_planning_recommends_high_demand_low_supply_with_evidence(self) -> None:
        self.store.import_verified_manifest(self.manifest)
        self.store.add_inventory_unit("bau-01", size_label="M")
        for index in range(98):
            self.store.record_demand_event("inquiry", "bau-01", source=f"test-{index}")
        self.store.record_demand_event("lost_demand", "bau-01", source="test-lost-1")
        self.store.record_demand_event("lost_demand", "bau-01", source="test-lost-2")
        old_date = (datetime.now(timezone.utc) - timedelta(days=95)).isoformat()
        connection = sqlite3.connect(self.store.database_path)
        try:
            connection.execute(
                "UPDATE catalog_inventory_events SET occurred_at = ? WHERE id = (SELECT id FROM catalog_inventory_events LIMIT 1)",
                (old_date,),
            )
            connection.commit()
        finally:
            connection.close()

        summary = self.store.planning_summary(window_days=180)

        self.assertEqual(summary.confidence, "reliable")
        self.assertEqual(summary.recommendations[0].category, "replenish")
        self.assertEqual(summary.recommendations[0].priority, "high")
        self.assertIn("需求流失", summary.recommendations[0].reason)
        self.assertEqual(summary.units_without_measurements, 1)

    def test_calendar_parses_chinese_time_and_checks_selected_style_conflict(self) -> None:
        self.store.import_verified_manifest(self.manifest)
        unit_code = self.store.add_inventory_unit(
            "bau-01",
            size_label="M",
            inventory_state="available",
            location="门店",
        )
        connection = sqlite3.connect(self.store.database_path)
        try:
            connection.execute(
                """
                CREATE TABLE appointments (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
                    customer_name TEXT NOT NULL DEFAULT '', customer_phone TEXT NOT NULL DEFAULT '',
                    requested_time_text TEXT NOT NULL DEFAULT '', confirmed_time_text TEXT NOT NULL DEFAULT '',
                    proposed_reschedule_text TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                """
                INSERT INTO appointments (
                    id, conversation_id, status, customer_name, customer_phone,
                    requested_time_text, confirmed_time_text, proposed_reschedule_text,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    (
                        "appointment-1", "conversation-1", "confirmed", "小许", "17300000000",
                        "8月6日下午三点", "8月6日下午三点", "2026-08-01", "2026-08-01",
                    ),
                    (
                        "appointment-2", "conversation-2", "time_pending_staff", "小林", "",
                        "下周末下午", "", "2026-08-01", "2026-08-01",
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        appointments = self.store.calendar_appointments(date(2026, 8, 3))
        parsed = next(item for item in appointments if item.id == "appointment-1")
        unparsed = next(item for item in appointments if item.id == "appointment-2")
        self.assertEqual(parsed.starts_at, "2026-08-06T15:00+08:00")
        self.assertEqual(parsed.ends_at, "2026-08-06T17:00+08:00")
        self.assertFalse(unparsed.parsed)

        self.store.set_appointment_style_interests("appointment-1", ("bau-01",))
        available = self.store.appointment_style_options("appointment-1")[0]
        self.assertTrue(available.selected)
        self.assertEqual(available.availability, "available")
        self.store.add_unit_block(
            unit_code,
            "2026-08-06T15:30",
            "2026-08-06T16:30",
            "reserved",
        )
        conflict = self.store.appointment_style_options("appointment-1")[0]
        self.assertEqual(conflict.availability, "conflict")
        analytics = self.store.analytics_summary(window_days=90)
        row = next(item for item in analytics.rows if item.model_code == "bau-01")
        self.assertEqual(row.demand_signals, 1)

    def test_inventory_blocks_prevent_overlapping_booking(self) -> None:
        self.store.import_verified_manifest(self.manifest)
        self.store.set_inventory_state("bau-01", "available")
        self.assertTrue(self.store.is_available_for("bau-01", "2026-08-10T10:00:00", "2026-08-10T12:00:00"))

        self.store.add_inventory_block(
            "bau-01", "2026-08-10T10:00:00", "2026-08-10T12:00:00", "reserved"
        )

        self.assertFalse(self.store.is_available_for("bau-01", "2026-08-10T11:00:00", "2026-08-10T13:00:00"))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.store.add_inventory_block(
                "bau-01", "2026-08-10T11:00:00", "2026-08-10T13:00:00", "cleaning"
            )

    def test_piui_drafts_do_not_become_products_before_mapping(self) -> None:
        draft_manifest = self.root / "piui.csv"
        with draft_manifest.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=("model_code", "catalog_type", "brand_internal", "style_name", "image_path", "source_document"),
            )
            writer.writeheader()
            writer.writerow(
                {
                    "model_code": "piui-pending-01",
                    "catalog_type": "bridal",
                    "brand_internal": "Piui",
                    "style_name": "Piui 新款 1（待款号匹配）",
                    "image_path": "images/piui-pending/dress1/a.jpg",
                    "source_document": "piui.zip",
                }
            )

        self.assertEqual(self.store.import_pending_drafts(draft_manifest), 1)
        self.assertEqual(self.store.counts(), {"products": 0, "images": 0, "drafts": 1})

    def test_dashboard_distinguishes_styles_from_physical_units(self) -> None:
        self.store.import_verified_manifest(self.manifest)

        summary = self.store.dashboard_summary()

        self.assertEqual(summary.products, 1)
        self.assertEqual(summary.bridal_products, 1)
        self.assertEqual(summary.images, 1)
        self.assertEqual(summary.inventory_units, 0)
        self.assertEqual(summary.products_without_units, 1)

    def test_physical_units_have_independent_size_location_and_state(self) -> None:
        self.store.import_verified_manifest(self.manifest)

        unit_code = self.store.add_inventory_unit(
            "bau-01",
            size_label="S",
            bust_cm=84,
            waist_cm=66,
            location="店内 A 区",
            inventory_state="available",
        )
        self.store.update_inventory_unit(
            unit_code,
            size_label="S",
            bust_cm=84,
            waist_cm=66,
            hip_cm=None,
            dress_length_cm=None,
            location="清洗区",
            inventory_state="cleaning",
            condition_state="good",
            notes="试纱后清洗",
        )

        product = self.store.internal_product("bau-01")
        summary = self.store.dashboard_summary()
        self.assertEqual(unit_code, "bau-01-01")
        self.assertEqual(product.units[0].waist_cm, 66)
        self.assertEqual(product.units[0].location, "清洗区")
        self.assertEqual(product.units[0].inventory_state, "cleaning")
        self.assertEqual(summary.inventory_units, 1)
        self.assertEqual(summary.products_without_units, 0)
        self.assertEqual(summary.maintenance_units, 1)

    def test_unit_schedule_rejects_overlap_and_can_be_cancelled(self) -> None:
        self.store.import_verified_manifest(self.manifest)
        unit_code = self.store.add_inventory_unit(
            "bau-01",
            size_label="M",
            inventory_state="available",
        )

        block_id = self.store.add_unit_block(
            unit_code,
            "2026-08-10T10:00",
            "2026-08-12T18:00",
            "reserved",
            notes="顾客婚礼",
        )

        self.assertFalse(
            self.store.is_unit_available_for(
                unit_code,
                "2026-08-11T09:00",
                "2026-08-11T12:00",
            )
        )
        with self.assertRaisesRegex(ValueError, "已有安排"):
            self.store.add_unit_block(
                unit_code,
                "2026-08-11T09:00",
                "2026-08-13T12:00",
                "cleaning",
            )
        self.assertEqual(self.store.unit_blocks()[0].notes, "顾客婚礼")

        self.store.cancel_unit_block(block_id)

        self.assertEqual(self.store.unit_blocks(), ())
        self.assertTrue(
            self.store.is_unit_available_for(
                unit_code,
                "2026-08-11T09:00",
                "2026-08-11T12:00",
            )
        )

    def test_analytics_separates_demand_from_operational_events(self) -> None:
        self.store.import_verified_manifest(self.manifest)
        first_unit = self.store.add_inventory_unit(
            "bau-01", size_label="S", inventory_state="available"
        )
        self.store.add_inventory_unit(
            "bau-01", size_label="M", inventory_state="available"
        )
        self.store.record_demand_event("inquiry", "bau-01", source="ai")
        self.store.record_demand_event("try_on", "bau-01", source="staff")
        self.store.record_demand_event("lost_demand", "bau-01", source="staff")
        self.store.add_unit_block(
            first_unit,
            "2026-08-10T10:00",
            "2026-08-12T18:00",
            "reserved",
        )

        summary = self.store.analytics_summary(window_days=90)
        row = next(item for item in summary.rows if item.model_code == "bau-01")

        self.assertEqual(summary.demand_events, 3)
        self.assertGreaterEqual(summary.operational_events, 3)
        self.assertEqual(summary.coverage_percent, 100)
        self.assertEqual(summary.confidence, "insufficient")
        self.assertEqual(row.inventory_units, 2)
        self.assertEqual(row.demand_signals, 3)
        self.assertEqual(row.try_ons, 1)
        self.assertEqual(row.effective_bookings, 1)
        self.assertEqual(row.lost_demand, 1)


if __name__ == "__main__":
    unittest.main()
