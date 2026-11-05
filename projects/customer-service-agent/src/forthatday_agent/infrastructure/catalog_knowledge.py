from __future__ import annotations

import re

from forthatday_agent.infrastructure.catalog_store import CatalogInternalProduct, CatalogStore
from forthatday_agent.infrastructure.markdown_knowledge import MarkdownKnowledgeProvider


ASCII_TERM = re.compile(r"[a-zA-Z0-9.+-]+")
CJK = re.compile(r"[\u3400-\u9fff]")
GENERIC_TERMS = {
    "婚纱",
    "款式",
    "推荐",
    "喜欢",
    "想要",
    "看看",
    "适合",
    "有没有",
    "轻纱",
    "礼服",
    "晨袍",
}


# These tags are deterministic and explainable. Image-derived tags can be merged
# into the same vocabulary later without changing the dialogue contract.
STYLE_TAG_RULES = {
    "clean": ("简约", "极简", "干净", "利落", "缎面", "米卡多", "丝绸", "micado"),
    "airy": ("轻盈", "轻纱", "网纱", "纱裙", "仙", "飘逸"),
    "romantic": ("浪漫", "法式", "蝴蝶结", "玫瑰", "蕾丝", "荷叶边"),
    "decorative": ("华丽", "亮片", "肌理", "花", "蝴蝶结", "玫瑰"),
    "fitted": ("修身", "显瘦", "鱼尾", "收腰", "h型"),
    "a_line": ("a字", "蓬裙"),
    "strapless": ("抹胸",),
    "halter": ("挂脖", "项圈"),
    "off_shoulder": ("一字肩", "露肩"),
    "square_neck": ("方领",),
    "v_neck": ("v领",),
    "long_sleeve": ("长袖", "遮手臂", "保守"),
    "short_sleeve": ("短袖", "中袖"),
    "sleeveless": ("无袖", "吊带"),
    "two_piece": ("两件套", "三件套", "三穿"),
    "color": ("红色", "紫色", "蓝色", "黄色", "粉色", "黑色", "银色"),
}

STYLE_TAG_LABELS = {
    "clean": "简约利落",
    "airy": "轻盈",
    "romantic": "浪漫",
    "decorative": "装饰感",
    "fitted": "修身",
    "a_line": "A字",
    "strapless": "抹胸",
    "halter": "挂脖",
    "off_shoulder": "一字肩",
    "square_neck": "方领",
    "v_neck": "V领",
    "long_sleeve": "长袖",
    "short_sleeve": "短袖或中袖",
    "sleeveless": "无袖或吊带",
    "two_piece": "多件式",
    "color": "彩色",
}


class CatalogKnowledgeProvider:
    """Combines public business knowledge with privacy-safe product facts."""

    def __init__(
        self,
        markdown: MarkdownKnowledgeProvider,
        catalog: CatalogStore,
    ) -> None:
        self.markdown = markdown
        self.catalog = catalog

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        products = self._matching_products(query, limit=min(4, limit))
        product_chunks = [self._customer_safe_chunk(product, query) for product in products]
        markdown_chunks = self.markdown.search(query, limit=max(0, limit - len(product_chunks)))
        return product_chunks + markdown_chunks

    def _matching_products(
        self,
        query: str,
        *,
        limit: int,
    ) -> tuple[CatalogInternalProduct, ...]:
        normalized_query = query.casefold().strip()
        if not normalized_query:
            return ()
        query_terms = _specific_terms(normalized_query)
        query_tags = _style_tags(normalized_query)
        ranked: list[tuple[int, str, CatalogInternalProduct]] = []
        for product in self.catalog.internal_products(limit=500):
            brand = product.brand_internal.casefold().strip()
            style = product.style_name.casefold().strip()
            code = product.model_code.casefold().strip()
            brand_mentioned = bool(brand and brand in normalized_query)
            style_mentioned = bool(style and style in normalized_query)
            code_mentioned = bool(code and code in normalized_query)
            product_terms = _specific_terms(
                " ".join((product.style_name, product.description, product.catalog_type))
            )
            product_tags = _style_tags(" ".join((product.style_name, product.description)))
            term_hits = len(query_terms & product_terms)
            score = term_hits * 3 + len(query_tags & product_tags) * 8
            if product.catalog_type == "bridal" and any(
                term in normalized_query for term in ("婚纱", "主纱", "仪式纱")
            ):
                score += 5
            if product.catalog_type == "lightwear" and any(
                term in normalized_query for term in ("礼服", "轻纱", "晨袍")
            ):
                score += 5
            if brand_mentioned:
                score += 12
            if style_mentioned:
                score += 18
            if code_mentioned:
                score += 24
            if score:
                ranked.append((score, product.model_code, product))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in ranked[:limit])

    @staticmethod
    def _customer_safe_chunk(
        product: CatalogInternalProduct,
        query: str,
    ) -> dict[str, str]:
        brand_mentioned = bool(
            product.brand_internal
            and product.brand_internal.casefold() in query.casefold()
        )
        category = "婚纱" if product.catalog_type == "bridal" else "轻纱、礼服或晨袍"
        description = re.sub(r"^[a-zA-Z0-9.+-]+", "", product.description).strip()
        tags = tuple(
            STYLE_TAG_LABELS[tag]
            for tag in sorted(_style_tags(" ".join((product.style_name, product.description))))
        )
        facts = [
            f"类别：{category}",
            f"款式名称：{product.style_name or '暂未命名'}",
            f"款式特点：{description or '暂缺详细描述'}",
            f"租赁：{'可咨询' if product.rental_available else '不提供'}",
            f"购买或定制：{'可咨询' if product.purchase_or_custom_available else '不提供'}",
            "库存与档期：必须查询实体库存并由店员确认，不得直接承诺",
        ]
        if tags:
            facts.append(f"推荐标签：{'、'.join(tags)}")
        facts.append(f"款式主图：{'可发送' if product.image_paths else '暂不可发送'}")
        if brand_mentioned:
            facts.append(
                f"顾客已主动提到品牌 {product.brand_internal}，可以确认该款品牌；不要延伸透露其他品牌"
            )
        return {
            "source": "customer_safe_catalog",
            "title": product.style_name or category,
            "text": "\n".join(facts),
            "catalog_ref": product.model_code,
            "recommendation_tags": ",".join(tags),
            "image_available": "true" if product.image_paths else "false",
        }


def _specific_terms(text: str) -> set[str]:
    lowered = text.casefold()
    terms = {item for item in ASCII_TERM.findall(lowered) if len(item) >= 2}
    cjk = "".join(char for char in lowered if CJK.match(char))
    terms.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return {term for term in terms if term not in GENERIC_TERMS}


def _style_tags(text: str) -> set[str]:
    normalized = text.casefold().replace(" ", "")
    return {
        tag
        for tag, triggers in STYLE_TAG_RULES.items()
        if any(trigger.casefold().replace(" ", "") in normalized for trigger in triggers)
    }
