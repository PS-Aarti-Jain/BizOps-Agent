from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from database.models import SupplierOption
from database.repositories.repository import BizOpsRepository


class SupplierDomainService:
    def __init__(self, repo: Optional[BizOpsRepository] = None):
        self.repo = repo or BizOpsRepository()

    def find_suppliers_for_product(self, product_id_or_name: str) -> Dict[str, Any]:
        product = self.repo.get_product(product_id_or_name)
        if not product:
            return {
                "status": "NO_DATA",
                "message": f"Product '{product_id_or_name}' not found.",
            }

        product_id = product["product_id"]
        suppliers = self.repo.get_supplier_products(product_id)

        if not suppliers:
            return {
                "status": "EMPTY_RESULT",
                "message": f"No suppliers found offering product '{product['name']}'.",
                "product_name": product["name"],
                "suppliers": [],
            }

        return {
            "status": "SUCCESS",
            "product_id": product_id,
            "product_name": product["name"],
            "count": len(suppliers),
            "suppliers": suppliers,
        }

    def compare_and_rank_suppliers(
        self, product_id_or_name: str, required_quantity: int = 1
    ) -> Dict[str, Any]:
        """
        Deterministic Procurement Ranking Evaluator.
        Uses exact Decimal arithmetic for monetary totals and trade-off disclosures.
        Ranks suppliers based on:
        1. Ability to satisfy required quantity (can_fulfill_qty)
        2. Total cost (unit_price * required_quantity)
        3. Lead time (days)
        4. Supplier reliability score
        """
        if required_quantity <= 0:
            return {
                "status": "INVALID_REQUEST",
                "message": "Required quantity must be a positive integer greater than zero.",
            }

        product = self.repo.get_product(product_id_or_name)

        if not product:
            return {
                "status": "NO_DATA",
                "message": f"Product '{product_id_or_name}' not found.",
            }

        product_id = product["product_id"]
        suppliers = self.repo.get_supplier_products(product_id)

        if not suppliers:
            return {
                "status": "EMPTY_RESULT",
                "message": f"No suppliers available for product '{product['name']}'.",
                "product_name": product["name"],
                "ranked_suppliers": [],
            }

        options: List[SupplierOption] = []
        dec_qty = Decimal(str(required_quantity))

        for s in suppliers:
            unit_price = s["unit_price"]
            avail_qty = s["available_qty"]
            lead_time = s["lead_time_days"]
            dec_unit = Decimal(str(unit_price))
            dec_total = (dec_unit * dec_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_cost = float(dec_total)
            can_fulfill = avail_qty >= required_quantity

            options.append(
                SupplierOption(
                    supplier_id=s["supplier_id"],
                    supplier_name=s["supplier_name"],
                    contact_email=s["contact_email"],
                    product_id=product_id,
                    product_name=product["name"],
                    unit_price=unit_price,
                    available_qty=avail_qty,
                    lead_time_days=lead_time,
                    min_order_qty=s["min_order_qty"],
                    total_cost_for_req=total_cost,
                    can_fulfill_qty=can_fulfill,
                    rating=s["rating"],
                    reliability_score=s["reliability_score"],
                    rank=0,
                )
            )

        # Deterministic ranking:
        # 1. can_fulfill_qty (True first)
        # 2. total_cost_for_req (Lowest first)
        # 3. lead_time_days (Shortest first)
        # 4. reliability_score (Highest first)
        sorted_options = sorted(
            options,
            key=lambda x: (
                not x.can_fulfill_qty,
                x.total_cost_for_req,
                x.lead_time_days,
                -x.reliability_score,
            ),
        )

        top_rec = sorted_options[0] if sorted_options else None

        for idx, opt in enumerate(sorted_options, start=1):
            opt.rank = idx
            if idx == 1:
                opt.trade_off_vs_recommendation = "Rank #1 Recommendation: Lowest total procurement cost."
            elif top_rec:
                cost_diff = opt.total_cost_for_req - top_rec.total_cost_for_req
                lead_diff = opt.lead_time_days - top_rec.lead_time_days
                notes = []
                if cost_diff > 0:
                    notes.append(f"+₹{cost_diff:,.2f} higher cost")
                elif cost_diff < 0:
                    notes.append(f"-₹{abs(cost_diff):,.2f} lower cost")

                if lead_diff < 0:
                    notes.append(f"{abs(lead_diff)} business days faster delivery")
                elif lead_diff > 0:
                    notes.append(f"{lead_diff} business days slower delivery")

                if opt.reliability_score > top_rec.reliability_score:
                    notes.append(f"higher reliability score ({opt.reliability_score:.2f} vs {top_rec.reliability_score:.2f})")

                opt.trade_off_vs_recommendation = f"Trade-off vs Rank #1 ({top_rec.supplier_name}): {', '.join(notes)}."

        return {
            "status": "SUCCESS",
            "product_id": product_id,
            "product_name": product["name"],
            "requested_quantity": required_quantity,
            "recommendation": (
                sorted_options[0].model_dump() if sorted_options else None
            ),
            "ranked_suppliers": [opt.model_dump() for opt in sorted_options],
        }

    def get_supplier_performance(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculates supplier performance analytics and rankings based on ACTUAL purchase order history.
        Does not infer supplier rankings from catalogue prices or subjective ratings.
        Computes:
        - Total spend per supplier (Decimal)
        - Total units purchased
        - Number of purchase orders
        - Rankings by spend and order frequency
        """
        from services.date_resolver import RelativeDateResolver

        s_date = start_date
        e_date = end_date
        period_label = "All Time"

        if period and not (start_date and end_date):
            dr = RelativeDateResolver.resolve_date_expression(period)
            if dr:
                s_date = dr.start_date
                e_date = dr.end_date
                period_label = dr.label

        records = self.repo.get_supplier_purchase_history(start_date=s_date, end_date=e_date)

        if not records:
            return {
                "status": "SUCCESS",
                "message": f"No purchase order history found for reporting period: {period_label}.",
                "period": period_label,
                "total_spend": 0.0,
                "total_orders": 0,
                "suppliers": [],
                "biggest_supplier_by_spend": None,
                "biggest_supplier_by_frequency": None,
                "is_complete": True,
            }

        # Aggregate metrics per supplier
        supplier_stats: Dict[str, Dict[str, Any]] = {}
        total_system_spend = Decimal("0.00")
        total_system_orders = len(records)

        for r in records:
            s_id = r["supplier_id"]
            amt = Decimal(str(r["total_amount"]))
            qty = int(r["total_qty"] or 0)
            total_system_spend += amt

            if s_id not in supplier_stats:
                supplier_stats[s_id] = {
                    "supplier_id": s_id,
                    "supplier_name": r["supplier_name"],
                    "city": r["city"],
                    "state": r["state"],
                    "rating": r["rating"],
                    "reliability_score": r["reliability_score"],
                    "total_spend": Decimal("0.00"),
                    "total_quantity": 0,
                    "po_count": 0,
                    "fulfilled_pos": 0,
                    "open_pos": 0,
                }

            supplier_stats[s_id]["total_spend"] += amt
            supplier_stats[s_id]["total_quantity"] += qty
            supplier_stats[s_id]["po_count"] += 1
            if r["po_status"] == "Fulfilled":
                supplier_stats[s_id]["fulfilled_pos"] += 1
            else:
                supplier_stats[s_id]["open_pos"] += 1

        # Calculate rankings
        suppliers_list = list(supplier_stats.values())
        # 1. Rank by spend
        ranked_by_spend = sorted(suppliers_list, key=lambda x: x["total_spend"], reverse=True)
        # 2. Rank by frequency
        ranked_by_freq = sorted(suppliers_list, key=lambda x: x["po_count"], reverse=True)

        for rank, s in enumerate(ranked_by_spend, start=1):
            s["rank_by_spend"] = rank
        for rank, s in enumerate(ranked_by_freq, start=1):
            s["rank_by_frequency"] = rank

        # Convert Decimals to float for serialization
        formatted_suppliers = []
        for s in ranked_by_spend:
            formatted_suppliers.append({
                "supplier_id": s["supplier_id"],
                "supplier_name": s["supplier_name"],
                "city": s["city"],
                "state": s["state"],
                "rating": s["rating"],
                "reliability_score": s["reliability_score"],
                "total_spend": float(s["total_spend"]),
                "total_quantity": s["total_quantity"],
                "po_count": s["po_count"],
                "fulfilled_pos": s["fulfilled_pos"],
                "open_pos": s["open_pos"],
                "rank_by_spend": s["rank_by_spend"],
                "rank_by_frequency": s["rank_by_frequency"],
            })

        top_spend = formatted_suppliers[0] if formatted_suppliers else None
        top_freq = sorted(formatted_suppliers, key=lambda x: x["rank_by_frequency"])[0] if formatted_suppliers else None

        return {
            "status": "SUCCESS",
            "period": period_label,
            "total_system_spend": float(total_system_spend),
            "total_system_orders": total_system_orders,
            "total_suppliers": len(formatted_suppliers),
            "suppliers": formatted_suppliers,
            "biggest_supplier_by_spend": top_spend,
            "biggest_supplier_by_frequency": top_freq,
            "is_complete": True,
        }

