from typing import Any, Dict, List, Optional
from database.repositories.repository import BizOpsRepository


class PurchaseOrderService:
    def __init__(self, repo: Optional[BizOpsRepository] = None):
        self.repo = repo or BizOpsRepository()

    def create_purchase_order(
        self,
        supplier_id: str,
        items: List[Dict[str, Any]],
        created_by: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        return self.repo.create_purchase_order(
            supplier_id=supplier_id,
            items=items,
            created_by=created_by,
            idempotency_key=idempotency_key,
        )

    def receive_goods(self, po_id: str, received_by: str = "Warehouse Manager") -> Dict[str, Any]:
        return self.repo.receive_goods(po_id=po_id, received_by=received_by)

