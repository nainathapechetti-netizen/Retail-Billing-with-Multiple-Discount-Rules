# Retail-Billing-with-Multiple-Discount-Rules
# Retail Billing with Multiple Discount Rules

A deterministic retail point-of-sale (POS) billing engine built in Python. It processes multi-item shopping carts, applies product-level and cart-level discount hierarchies, enforces **maximum concession caps**, calculates **category-specific taxes**, and generates an aligned **Itemized Retail Tax Invoice**.

---

## Key Features

1. **Multi-Layer Discount Engine:**
   - **Item-Level Markdowns:** Per-product promotional discounts capped at `max_item_discount_pct` (`25%` default).
   - **Tiered Cart Threshold Discounts:** Automatically unlocks higher discount tiers (`5%` at `≥ 2,000`, `10%` at `≥ 5,000`, `15%` at `≥ 10,000`) based on the cart's eligible pool.
   - **Loyalty & Promo Coupons:** Applies member privileges (`3%`) and validated coupon codes (`SAVE10`, `FESTIVE15`, `WELCOME5`), supporting both stacked (`allow_discount_stacking=True`) and best-single-discount modes.
   - **Exclusion Controls:** Price-controlled items (e.g., `Pharmacy` essentials with `eligible_for_cart_discounts=False`) are excluded from cart-wide promotional pools.
2. **Maximum Concession Guardrails:**
   - Prevents margin erosion by capping combined item + cart discounts at **both** a maximum percentage of gross cart value (`max_total_concession_pct = 20%`) and an absolute currency ceiling (`max_total_concession_amount = 2,500`).
   - Approved cart discounts are proportionally distributed across eligible line items so line-level tax calculations remain exact.
3. **Category-Specific Taxation:**
   - Applies distinct tax slabs per `ProductCategory` (`Grocery`: `5%`, `Pharmacy`: `5%`, `Apparel`: `12%`, `Electronics`: `18%`, `Luxury`: `28%`) on post-discount taxable amounts and summarizes tax liabilities by slab.

---

## Project Structure

```text
├── retail_billing.py   # Product/cart models, discount & cap engine, tax calculator & invoice formatter
└── README.md           # System documentation
