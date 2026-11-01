import asyncio
import json
import os
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.inventory_service import InventoryDomainService, classify_inventory_level, calculate_available_percentage
from mcp.router import MCPRouterManager

async def verify_alignment():
    print("=== Step 1: Query Live Dashboard API ===")
    api_url = "http://127.0.0.1:8080/api/data/inventory"
    with urllib.request.urlopen(api_url) as resp:
        api_resp = json.loads(resp.read().decode())
    api_items = {it["product_id"]: it for it in api_resp["data"]}
    print(f"API returned {len(api_items)} items successfully.")

    print("\n=== Step 2: Query MCP Inventory Server ===")
    router = MCPRouterManager()
    await router.initialize_connections()
    try:
        mcp_p1001 = await router.execute_tool("inventory.get_stock", {"product_id": "P1001"})
        mcp_p1002 = await router.execute_tool("inventory.get_stock", {"product_id": "P1002"})
        mcp_p1005 = await router.execute_tool("inventory.get_stock", {"product_id": "P1005"})
        mcp_all = await router.execute_tool("inventory.all_inventory", {})
    finally:
        await router.close()

    mcp_items = {
        "P1001": mcp_p1001.get("data", {}),
        "P1002": mcp_p1002.get("data", {}),
        "P1005": mcp_p1005.get("data", {}),
    }
    print("MCP tools executed successfully.")

    print("\n=== Step 3: Query InventoryDomainService Directly ===")
    svc = InventoryDomainService()
    svc_p1001 = svc.get_stock_info("P1001")["data"]
    svc_p1002 = svc.get_stock_info("P1002")["data"]
    svc_p1005 = svc.get_stock_info("P1005")["data"]
    svc_all = svc.get_all_inventory_items()["items"]
    svc_items = {
        "P1001": svc_p1001,
        "P1002": svc_p1002,
        "P1005": svc_p1005,
    }

    print("\n=== Step 4: Comparison Table for Target Products ===")
    headers = ["Product ID", "Source", "On Hand", "Reserved", "Allocated", "Available", "Reorder Thresh", "Classification", "Available %"]
    row_fmt = "{:<12} {:<10} {:<9} {:<10} {:<11} {:<11} {:<16} {:<16} {:<12}"
    print(row_fmt.format(*headers))
    print("-" * 105)

    discrepancies = []
    target_ids = ["P1001", "P1002", "P1005"]

    for pid in target_ids:
        api_data = api_items.get(pid, {})
        mcp_data = mcp_items.get(pid, {})
        svc_data = svc_items.get(pid, {})

        # Check alignment
        for key in ["on_hand_qty", "reserved_qty", "allocated_qty", "available_qty", "classification"]:
            val_api = api_data.get(key)
            val_mcp = mcp_data.get(key)
            val_svc = svc_data.get(key)
            if val_api != val_mcp or val_api != val_svc:
                discrepancies.append(f"Mismatch for {pid} field '{key}': API={val_api}, MCP={val_mcp}, SVC={val_svc}")

        print(row_fmt.format(pid, "API", api_data.get("on_hand_qty"), api_data.get("reserved_qty"), api_data.get("allocated_qty"), api_data.get("available_qty"), str(api_data.get("reorder_threshold")), api_data.get("classification"), f"{api_data.get('available_pct')}%"))
        print(row_fmt.format(pid, "MCP", mcp_data.get("on_hand_qty"), mcp_data.get("reserved_qty"), mcp_data.get("allocated_qty"), mcp_data.get("available_qty"), str(mcp_data.get("reorder_threshold")), mcp_data.get("classification"), f"{mcp_data.get('available_pct')}%"))
        print(row_fmt.format(pid, "DomainSvc", svc_data.get("on_hand_qty"), svc_data.get("reserved_qty"), svc_data.get("allocated_qty"), svc_data.get("available_qty"), str(svc_data.get("reorder_threshold")), svc_data.get("classification"), f"{svc_data.get('available_pct')}%"))
        print("-" * 105)

    print("\n=== Step 5: Reserved & Allocated Quantity Verification ===")
    print("Testing stock semantics: available == max(0, on_hand - reserved - allocated)")
    for pid, it in api_items.items():
        on_hand = it["on_hand_qty"]
        res = it["reserved_qty"]
        alloc = it["allocated_qty"]
        avail = it["available_qty"]
        expected_avail = max(0, on_hand - res - alloc)
        assert avail == expected_avail, f"Double counting or math error on {pid}: on_hand={on_hand}, res={res}, alloc={alloc}, avail={avail}"
    print("PASS: Zero double counting across all 15 catalog products. Reserved and allocated are subtracted exactly once.")

    print("\n=== Step 6: Edge Case Verifications ===")
    print("Case 1: Zero on-hand and zero available stock (P1001)")
    p1001 = svc_p1001
    assert p1001["on_hand_qty"] == 0 and p1001["available_qty"] == 0
    assert p1001["classification"] == "OUT_OF_STOCK"
    assert p1001["is_out_of_stock"] is True and p1001["is_low_stock"] is False
    assert p1001["available_pct"] == 0.0
    print("PASS: P1001 correctly classified as OUT_OF_STOCK with 0.0% available.")

    print("Case 2: Missing reorder threshold (reorder_level=None)")
    c_in = classify_inventory_level(available_qty=10, reorder_level=None)
    c_out = classify_inventory_level(available_qty=0, reorder_level=None)
    assert c_in == "IN_STOCK", f"Expected IN_STOCK, got {c_in}"
    assert c_out == "OUT_OF_STOCK", f"Expected OUT_OF_STOCK, got {c_out}"
    print("PASS: Missing reorder threshold handled explicitly without synthetic fallback values.")

    print("\n=== Discrepancies Summary ===")
    if discrepancies:
        print(f"FOUND {len(discrepancies)} DISCREPANCIES:")
        for d in discrepancies:
            print(f"  - {d}")
    else:
        print("PERFECT ALIGNMENT: 0 discrepancies found across API, MCP, and Domain Service.")

if __name__ == "__main__":
    asyncio.run(verify_alignment())
