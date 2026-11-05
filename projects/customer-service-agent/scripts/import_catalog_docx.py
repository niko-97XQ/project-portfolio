"""Extract Forthatday catalog rows and their embedded Word images into private storage."""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.oxml.ns import qn


FIELDNAMES = (
    "catalog_type",
    "sequence",
    "brand_internal",
    "style_name",
    "model_code",
    "description",
    "rental_price_internal",
    "purchase_price_internal",
    "rental_available",
    "purchase_or_custom_available",
    "image_path",
    "source_document",
)


def extension_for(content_type: str, part_name: str) -> str:
    suffix = Path(part_name).suffix.lower()
    if suffix:
        return suffix
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/bmp": ".bmp"}.get(
        content_type, ".bin"
    )


def extract_first_image(cell, document: Document):
    for blip in cell._tc.iter(qn("a:blip")):
        relationship_id = blip.get(qn("r:embed"))
        if relationship_id:
            return document.part.related_parts[relationship_id]
    return None


def normalize(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def import_catalog(source: Path, catalog_type: str, output_dir: Path) -> Path:
    document = Document(source)
    if not document.tables:
        raise ValueError(f"No table found in {source}")

    table = document.tables[0]
    images_dir = output_dir / "images" / catalog_type
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / f"{catalog_type}-catalog-internal.csv"

    with manifest_path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in table.rows[2:]:
            cells = row.cells
            values = [normalize(cell.text) for cell in cells]
            if len(values) < 8 or not values[0]:
                continue

            model_code = values[4].lower().replace(" ", "-") or f"{catalog_type}-{values[0]}"
            image = extract_first_image(cells[2], document)
            relative_image_path = ""
            if image is not None:
                extension = extension_for(image.content_type, str(image.partname))
                image_path = images_dir / f"{model_code}{extension}"
                image_path.write_bytes(image.blob)
                relative_image_path = image_path.relative_to(output_dir).as_posix()

            writer.writerow(
                {
                    "catalog_type": catalog_type,
                    "sequence": values[0],
                    "brand_internal": values[1],
                    "style_name": values[3],
                    "model_code": model_code,
                    "description": values[5],
                    "rental_price_internal": values[6],
                    "purchase_price_internal": values[7],
                    "rental_available": "yes" if values[6] else "pending",
                    "purchase_or_custom_available": "yes" if values[7] else "no",
                    "image_path": relative_image_path,
                    "source_document": source.name,
                }
            )
    return manifest_path


def import_piui_photos(source: Path, output_dir: Path) -> Path:
    """Keep Piui additions grouped by the source's dressN folders until mapped by staff."""
    output_images = output_dir / "images" / "piui-pending"
    output_images.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[str]] = {}
    with ZipFile(source) as archive:
        for entry in archive.infolist():
            parts = Path(entry.filename).parts
            group = next((part for part in parts if part.lower().startswith("dress")), None)
            if group is None or entry.is_dir() or not entry.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            destination = output_images / group.lower() / Path(entry.filename).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(entry))
            groups.setdefault(group.lower(), []).append(destination.relative_to(output_dir).as_posix())

    manifest_path = output_dir / "piui-photo-additions-pending.csv"
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDNAMES)
        writer.writeheader()
        for group in sorted(groups, key=lambda value: int(value.removeprefix("dress"))):
            sequence = group.removeprefix("dress")
            writer.writerow(
                {
                    "catalog_type": "bridal",
                    "sequence": sequence,
                    "brand_internal": "Piui",
                    "style_name": f"Piui 新款 {sequence}（待款号匹配）",
                    "model_code": f"piui-pending-{int(sequence):02d}",
                    "description": "待店主确认",
                    "rental_price_internal": "",
                    "purchase_price_internal": "",
                    "rental_available": "pending",
                    "purchase_or_custom_available": "pending",
                    "image_path": ";".join(groups[group]),
                    "source_document": source.name,
                }
            )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("catalog_type", choices=("bridal", "lightwear", "piui-photos"))
    parser.add_argument("--output-dir", type=Path, default=Path("private/catalog"))
    arguments = parser.parse_args()
    manifest = (
        import_piui_photos(arguments.source, arguments.output_dir)
        if arguments.catalog_type == "piui-photos"
        else import_catalog(arguments.source, arguments.catalog_type, arguments.output_dir)
    )
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]
    print(f"Created {manifest} ({digest})")


if __name__ == "__main__":
    main()
