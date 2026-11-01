# Warehouse Inventory Reorder Policy

## Policy Objective
Standard operating procedures for calculating safety stock, determining reorder thresholds, and initiating supplier purchase orders.

## Reorder Triggers & Thresholds
1. **Safety Stock Evaluation:** Every SKU maintains a minimum safety stock threshold based on 14-day historical consumption velocity.
2. **Reorder Point:** Restock must be initiated whenever `Available Stock` (On-hand minus Reserved and Allocated) falls at or below the defined `reorder_threshold`.
3. **Pending PO Deduplication:** If an open purchase order with status 'Created' or 'Approved' already exists for the SKU, incoming quantities are tracked, and redundant purchase orders must not be created unless the existing PO is insufficient to cover deficit.

## Order Quantities & Minimums
- **Economic Order Quantity:** Unless otherwise specified by supplier agreements, purchase orders should target restocking up to `reorder_threshold + 30-day projected usage`.
- **Supplier Minimum Order Quantity (MOQ):** Orders must satisfy supplier minimum lot sizes.

