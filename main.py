from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class ProductCategory(str, Enum):
    GROCERY = "Grocery"
    APPAREL = "Apparel"
    ELECTRONICS = "Electronics"
    LUXURY = "Luxury"
    PHARMACY = "Pharmacy"


@dataclass(frozen=True)
class ThresholdTier:
    """Cart subtotal threshold granting a percentage discount."""
    min_subtotal: float
    discount_pct: float


@dataclass(frozen=True)
class RetailBillingPolicy:
    """Defines category tax rates, tiered cart thresholds, and maximum concession caps."""
    category_tax_rates: Dict[ProductCategory, float] = field(
        default_factory=lambda: {
            ProductCategory.GROCERY: 5.0,
            ProductCategory.PHARMACY: 5.0,
            ProductCategory.APPAREL: 12.0,
            ProductCategory.ELECTRONICS: 18.0,
            ProductCategory.LUXURY: 28.0,
        }
    )
    # Tiered cart subtotal discounts (evaluated highest threshold first)
    threshold_tiers: List[ThresholdTier] = field(
        default_factory=lambda: [
            ThresholdTier(min_subtotal=2_000.0, discount_pct=5.0),
            ThresholdTier(min_subtotal=5_000.0, discount_pct=10.0),
            ThresholdTier(min_subtotal=10_000.0, discount_pct=15.0),
        ]
    )
    # Loyalty & promo coupon rules
    loyalty_member_discount_pct: float = 3.0
    valid_promo_codes: Dict[str, float] = field(
        default_factory=lambda: {
            "SAVE10": 10.0,
            "FESTIVE15": 15.0,
            "WELCOME5": 5.0,
        }
    )
    # Maximum concession limits (guardrails against margin erosion)
    max_item_discount_pct: float = 25.0         # Max discount % allowed on any single line item
    max_total_concession_pct: float = 20.0      # Max combined concession % on eligible cart value
    max_total_concession_amount: float = 2_500.0  # Absolute currency cap on total concessions
    allow_discount_stacking: bool = True        # Stack threshold + loyalty + coupon up to cap
    tax_after_discount: bool = True             # Standard GST rule: tax on post-discount net price


@dataclass
class Product:
    """Catalog product definition."""
    sku: str
    name: str
    category: ProductCategory
    unit_price: float
    item_discount_pct: float = 0.0              # Direct product markdown (e.g., clearance %)
    eligible_for_cart_discounts: bool = True    # False for price-controlled items (e.g., medicines)


@dataclass
class CartItem:
    """Line item in a customer's shopping basket."""
    product: Product
    quantity: int


@dataclass
class InvoiceLineItem:
    """Computed breakdown for a single product line on the invoice."""
    sku: str
    name: str
    category: str
    quantity: int
    unit_price: float
    gross_amount: float
    item_discount: float
    allocated_cart_discount: float
    total_discount: float
    taxable_amount: float
    tax_rate_pct: float
    tax_amount: float
    line_total: float


@dataclass
class RetailInvoice:
    """Complete itemized retail invoice with concession audit trail."""
    invoice_id: str
    customer_name: str
    is_loyalty_member: bool
    applied_promo_code: Optional[str]
    line_items: List[InvoiceLineItem]
    gross_subtotal: float
    total_item_discounts: float
    cart_level_discounts_applied: List[str]
    uncapped_cart_discount: float
    total_concession_granted: float
    concession_cap_triggered: bool
    total_taxable_amount: float
    category_tax_summary: Dict[str, float]
    total_tax: float
    grand_total: float


class RetailBillingEngine:
    """Processes cart items, applies multi-tier discount rules & caps, and builds invoices."""

    def __init__(self, policy: Optional[RetailBillingPolicy] = None):
        self.policy = policy or RetailBillingPolicy()

    def _validate_cart(self, items: List[CartItem], promo_code: Optional[str]) -> None:
        if not items:
            raise ValueError("Cart cannot be empty.")

        seen_skus = set()
        for item in items:
            if item.quantity <= 0:
                raise ValueError(f"Quantity must be >= 1 for SKU '{item.product.sku}'.")
            if item.product.unit_price < 0:
                raise ValueError(f"Unit price cannot be negative for SKU '{item.product.sku}'.")
            if not (0.0 <= item.product.item_discount_pct <= 100.0):
                raise ValueError(f"Invalid item_discount_pct on SKU '{item.product.sku}'.")
            if item.product.category not in self.policy.category_tax_rates:
                raise ValueError(f"Missing tax rule for category '{item.product.category}'.")
            if item.product.sku in seen_skus:
                raise ValueError(f"Duplicate SKU '{item.product.sku}' in cart; merge quantities first.")
            seen_skus.add(item.product.sku)

        if promo_code and promo_code.upper() not in self.policy.valid_promo_codes:
            raise ValueError(f"Invalid or expired promo code: '{promo_code}'.")

    def generate_invoice(
        self,
        invoice_id: str,
        customer_name: str,
        items: List[CartItem],
        is_loyalty_member: bool = False,
        promo_code: Optional[str] = None,
    ) -> RetailInvoice:
        """Evaluates line-level & cart-level discounts, enforces max concession limits, and computes tax."""
        self._validate_cart(items, promo_code)
        norm_promo = promo_code.upper() if promo_code else None

        # Step 1: Compute gross amounts and capped item-level discounts
        raw_lines = []
        gross_subtotal = 0.0
        total_item_discounts = 0.0
        cart_discount_eligible_pool = 0.0

        for item in items:
            p = item.product
            gross = p.unit_price * item.quantity
            effective_item_disc_pct = min(p.item_discount_pct, self.policy.max_item_discount_pct)
            item_disc = gross * (effective_item_disc_pct / 100.0)
            post_item_net = gross - item_disc

            gross_subtotal += gross
            total_item_discounts += item_disc
            if p.eligible_for_cart_discounts:
                cart_discount_eligible_pool += post_item_net

            raw_lines.append((item, gross, item_disc, post_item_net))

        # Step 2: Evaluate cart-level discount candidates on eligible pool
        discount_candidates: List[Tuple[str, float]] = []

        # 2a. Tiered threshold discount (highest qualifying tier)
        sorted_tiers = sorted(
            self.policy.threshold_tiers, key=lambda t: t.min_subtotal, reverse=True
        )
        for tier in sorted_tiers:
            if cart_discount_eligible_pool >= tier.min_subtotal:
                amt = cart_discount_eligible_pool * (tier.discount_pct / 100.0)
                discount_candidates.append(
                    (f"Threshold >= {tier.min_subtotal:,.0f} ({tier.discount_pct:.0f}%)", amt)
                )
                break

        # 2b. Loyalty membership concession
        if is_loyalty_member and cart_discount_eligible_pool > 0:
            amt = cart_discount_eligible_pool * (self.policy.loyalty_member_discount_pct / 100.0)
            discount_candidates.append(
                (f"Loyalty Member ({self.policy.loyalty_member_discount_pct:.0f}%)", amt)
            )

        # 2c. Promo coupon discount
        if norm_promo and cart_discount_eligible_pool > 0:
            pct = self.policy.valid_promo_codes[norm_promo]
            amt = cart_discount_eligible_pool * (pct / 100.0)
            discount_candidates.append((f"Promo '{norm_promo}' ({pct:.0f}%)", amt))

        if not discount_candidates:
            uncapped_cart_discount = 0.0
            applied_labels: List[str] = []
        elif self.policy.allow_discount_stacking:
            uncapped_cart_discount = sum(amt for _, amt in discount_candidates)
            applied_labels = [lbl for lbl, _ in discount_candidates]
        else:
            best_lbl, best_amt = max(discount_candidates, key=lambda x: x[1])
            uncapped_cart_discount = best_amt
            applied_labels = [best_lbl]

        # Step 3: Enforce Maximum Concession Limits (percentage cap + absolute ceiling)
        max_allowed_by_pct = gross_subtotal * (self.policy.max_total_concession_pct / 100.0)
        max_total_concession = min(max_allowed_by_pct, self.policy.max_total_concession_amount)

        remaining_concession_budget = max(0.0, max_total_concession - total_item_discounts)
        approved_cart_discount = min(uncapped_cart_discount, remaining_concession_budget)
        cap_triggered = uncapped_cart_discount > approved_cart_discount + 1e-6

        # Step 4: Proportionally allocate approved cart discount across eligible line items & compute tax
        invoice_lines: List[InvoiceLineItem] = []
        category_tax_summary: Dict[str, float] = {}
        total_taxable = 0.0
        total_tax = 0.0

        for item, gross, item_disc, post_item_net in raw_lines:
            p = item.product
            if p.eligible_for_cart_discounts and cart_discount_eligible_pool > 0:
                share = post_item_net / cart_discount_eligible_pool
                alloc_cart_disc = approved_cart_discount * share
            else:
                alloc_cart_disc = 0.0

            total_line_disc = item_disc + alloc_cart_disc
            taxable_amt = (gross - total_line_disc) if self.policy.tax_after_discount else gross
            tax_rate = self.policy.category_tax_rates[p.category]
            tax_amt = taxable_amt * (tax_rate / 100.0)
            line_total = (gross - total_line_disc) + tax_amt

            cat_key = f"{p.category.value} ({tax_rate:.0f}%)"
            category_tax_summary[cat_key] = round(category_tax_summary.get(cat_key, 0.0) + tax_amt, 2)
            total_taxable += taxable_amt
            total_tax += tax_amt

            invoice_lines.append(
                InvoiceLineItem(
                    sku=p.sku,
                    name=p.name,
                    category=p.category.value,
                    quantity=item.quantity,
                    unit_price=round(p.unit_price, 2),
                    gross_amount=round(gross, 2),
                    item_discount=round(item_disc, 2),
                    allocated_cart_discount=round(alloc_cart_disc, 2),
                    total_discount=round(total_line_disc, 2),
                    taxable_amount=round(taxable_amt, 2),
                    tax_rate_pct=tax_rate,
                    tax_amount=round(tax_amt, 2),
                    line_total=round(line_total, 2),
                )
            )

        total_concession = total_item_discounts + approved_cart_discount
        grand_total = (gross_subtotal - total_concession) + total_tax

        return RetailInvoice(
            invoice_id=invoice_id,
            customer_name=customer_name,
            is_loyalty_member=is_loyalty_member,
            applied_promo_code=norm_promo,
            line_items=invoice_lines,
            gross_subtotal=round(gross_subtotal, 2),
            total_item_discounts=round(total_item_discounts, 2),
            cart_level_discounts_applied=applied_labels,
            uncapped_cart_discount=round(uncapped_cart_discount, 2),
            total_concession_granted=round(total_concession, 2),
            concession_cap_triggered=cap_triggered,
            total_taxable_amount=round(total_taxable, 2),
            category_tax_summary=category_tax_summary,
            total_tax=round(total_tax, 2),
            grand_total=round(grand_total, 2),
        )

    @staticmethod
    def format_invoice(inv: RetailInvoice, currency: str = "INR") -> str:
        """Renders an aligned, itemized retail tax invoice."""
        width = 104
        border = "=" * width
        divider = "-" * width

        lines = [
            border,
            f"{'ITEMIZED RETAIL TAX INVOICE':^{width}}",
            border,
            f"Invoice ID : {inv.invoice_id:<28} Customer : {inv.customer_name}",
            f"Loyalty    : {'Yes' if inv.is_loyalty_member else 'No':<28} Promo    : {inv.applied_promo_code or 'None'}",
            divider,
            f"{'SKU':<8} {'ITEM':<22} {'CAT':<12} {'QTY':>4} {'PRICE':>9} {'GROSS':>10} {'DISC':>9} {'TAX%':>6} {'TAX':>8} {'TOTAL':>10}",
            divider,
        ]

        for li in inv.line_items:
            lines.append(
                f"{li.sku:<8} {li.name[:22]:<22} {li.category:<12} {li.quantity:>4} "
                f"{li.unit_price:>9,.2f} {li.gross_amount:>10,.2f} -{li.total_discount:>8,.2f} "
                f"{li.tax_rate_pct:>5.0f}% {li.tax_amount:>8,.2f} {li.line_total:>10,.2f}"
            )

        cap_note = " [MAX CONCESSION CAP APPLIED]" if inv.concession_cap_triggered else ""
        concessions_desc = ", ".join(inv.cart_level_discounts_applied) or "None"

        lines.extend(
            [
                divider,
                f"Cart Discounts Triggered : {concessions_desc}{cap_note}",
                f"Category Tax Breakdown   : {inv.category_tax_summary}",
                divider,
                f"{'Gross Subtotal:':>85} {inv.gross_subtotal:>14,.2f}",
                f"{'Total Concessions Granted:':>85} -{inv.total_concession_granted:>13,.2f}",
                f"{'Net Taxable Value:':>85} {inv.total_taxable_amount:>14,.2f}",
                f"{'Total Category Tax:':>85} +{inv.total_tax:>13,.2f}",
                border,
                f"{f'GRAND TOTAL PAYABLE ({currency}):':>85} {inv.grand_total:>14,.2f}",
                border,
            ]
        )
        return "\n".join(lines)


# --- Example Execution ---
if __name__ == "__main__":
    engine = RetailBillingEngine()

    shopping_cart = [
        CartItem(Product("ELE-01", "Noise-Cancel Headphones", ProductCategory.ELECTRONICS, 6000.0, item_discount_pct=5.0), quantity=1),
        CartItem(Product("APP-04", "Cotton Linen Jacket", ProductCategory.APPAREL, 2400.0, item_discount_pct=10.0), quantity=2),
        CartItem(Product("GRO-09", "Organic Olive Oil 1L", ProductCategory.GROCERY, 850.0), quantity=2),
        CartItem(Product("LUX-02", "Designer Chronograph", ProductCategory.LUXURY, 9500.0), quantity=1),
        CartItem(Product("PHM-01", "Prescription Insulin", ProductCategory.PHARMACY, 1200.0, eligible_for_cart_discounts=False), quantity=1),
    ]

    invoice = engine.generate_invoice(
        invoice_id="INV-2026-9042",
        customer_name="Meenakshi Sundaram",
        items=shopping_cart,
        is_loyalty_member=True,
        promo_code="SAVE10",
    )

    print(engine.format_invoice(invoice))
