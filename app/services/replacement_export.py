"""Order-scoped replacement products for the DTC and DTB export clients."""

from __future__ import annotations

from typing import Any, Callable

from app.services.replacement_images import ReplacementImageSyncError


class ReplacementExportOdoo:
    def __init__(self, client: Any, replacements: list[dict], image_loader: Callable[[str], str]):
        self.client = client
        self.replacements = replacements
        self.image_loader = image_loader
        self.products: dict[int, dict] = {}
        self.images: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def get_order_lines(self, line_ids: list[int]) -> list[dict]:
        lines = self.client.get_order_lines(line_ids)
        by_id = {int(line["id"]): line for line in lines}
        consumed: set[int] = set()
        overrides: dict[int, dict] = {}
        for replacement in self.replacements:
            ids = replacement["source_ids"]
            if not ids or any(i not in by_id or i in consumed for i in ids):
                raise RuntimeError("Replacement source lines changed in Odoo; review the order before exporting to Shopify.")
            source = [by_id[i] for i in ids]
            quantity = float(replacement["quantity"])
            if quantity <= 0 or not quantity.is_integer():
                raise RuntimeError("Shopify replacement quantity must be a positive whole number.")
            asin = replacement["replacement_asin"]
            image = replacement.get("replacement_image_base64")
            if not image:
                try:
                    if asin not in self.images:
                        self.images[asin] = self.image_loader(asin)
                    image = self.images[asin]
                    if not image:
                        raise ValueError("Amazon image unavailable")
                except Exception as exc:
                    raise ReplacementImageSyncError(replacement["id"], asin) from exc
            product_id = -int(replacement["id"])
            title = replacement.get("replacement_product_name") or f"Replacement ASIN {asin}"
            # Virtual products keep the original Odoo product and its image untouched.
            self.products[product_id] = {
                "id": product_id, "name": title, "default_code": asin,
                "barcode": "", "product_tmpl_id": False,
                "description": "", "description_sale": "",
                "image_1920": image,
            }
            line = dict(source[0])
            gross = sum(float(item.get("price_unit") or 0) * float(item.get("product_uom_qty") or 0) for item in source)
            net = sum(float(item.get("price_unit") or 0) * float(item.get("product_uom_qty") or 0) * (1 - float(item.get("discount") or 0) / 100) for item in source)
            line.update(
                name=title, product_id=[product_id, title], product_uom_qty=int(quantity),
                price_unit=gross / quantity,
                discount=(1 - net / gross) * 100 if gross else 0,
                price_total=sum(float(item.get("price_total") or 0) for item in source),
                price_subtotal=sum(float(item.get("price_subtotal") or 0) for item in source),
            )
            overrides[ids[0]] = line
            consumed.update(ids)
        return [overrides[int(line["id"])] if int(line["id"]) in overrides else line
                for line in lines if int(line["id"]) not in consumed or int(line["id"]) in overrides]

    def get_product_product(self, product_id: int) -> dict | None:
        if product_id in self.products:
            return dict(self.products[product_id])
        return self.client.get_product_product(product_id)
