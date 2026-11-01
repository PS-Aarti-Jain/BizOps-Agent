import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9222

async def run_browser_verification():
    print("[1/5] Launching headless Edge with remote debugging...")
    proc = subprocess.Popen([
        EDGE_PATH,
        "--headless=new",
        f"--remote-debugging-port={PORT}",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "http://127.0.0.1:8080/"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        # Wait for debugging endpoint
        ws_url = None
        for _ in range(15):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
                    targets = json.loads(r.read().decode())
                    for t in targets:
                        if t.get("type") == "page":
                            ws_url = t.get("webSocketDebuggerUrl")
                            break
                    if ws_url:
                        break
            except Exception:
                pass

        if not ws_url:
            raise RuntimeError("Failed to connect to Edge remote debugging endpoint")

        print(f"[2/5] Connected to browser CDP at {ws_url}")
        async with websockets.connect(ws_url) as ws:
            msg_id = 0
            console_errors = []
            console_logs = []

            async def send_cmd(method, params=None):
                nonlocal msg_id
                msg_id += 1
                payload = {"id": msg_id, "method": method, "params": params or {}}
                await ws.send(json.dumps(payload))
                while True:
                    resp = json.loads(await ws.recv())
                    if resp.get("id") == msg_id:
                        return resp.get("result", {})
                    # Catch console events
                    if resp.get("method") == "Runtime.consoleAPICalled":
                        args = resp.get("params", {}).get("args", [])
                        text = " ".join(str(a.get("value", "")) for a in args)
                        console_logs.append(text)
                        if resp.get("params", {}).get("type") == "error":
                            console_errors.append(text)
                    elif resp.get("method") == "Runtime.exceptionThrown":
                        details = resp.get("params", {}).get("exceptionDetails", {})
                        console_errors.append(str(details))

            await send_cmd("Runtime.enable")
            await send_cmd("Page.enable")

            print("[3/5] Waiting for page load and switching to Inventory panel...")
            # Navigate to page explicitly
            await send_cmd("Page.navigate", {"url": "http://127.0.0.1:8080/"})
            
            # Wait for page to finish loading
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("method") == "Page.loadEventFired":
                    break

            # Extra pause for DOM initialization
            await asyncio.sleep(2.0)

            # Switch to inventory panel and trigger loadInventory
            script = """
            (async () => {
                const navItems = document.querySelectorAll('.nav-item');
                const invNav = Array.from(navItems).find(el => el.textContent.includes('Inventory'));
                if (invNav) {
                    switchPanel('inventory', invNav);
                } else if (typeof loadInventory === 'function') {
                    loadInventory();
                }
                await new Promise(r => setTimeout(r, 2000));
                
                const statsEl = document.getElementById('inv-stats');
                const tbody = document.getElementById('inv-tbody');
                const rows = Array.from(tbody.querySelectorAll('tr')).map(tr => {
                    const cells = Array.from(tr.querySelectorAll('td'));
                    return {
                        cells: cells.map(td => td.innerText.trim()),
                        statusBadgeHtml: cells[9] ? cells[9].innerHTML.trim() : '',
                        statusText: cells[9] ? cells[9].innerText.trim() : '',
                        barWidth: tr.querySelector('.stock-bar-fill') ? tr.querySelector('.stock-bar-fill').style.width : '',
                    };
                });

                return {
                    statsHtml: statsEl ? statsEl.innerText.trim() : '',
                    rowCount: rows.length,
                    rows: rows
                };
            })()
            """
            eval_res = await send_cmd("Runtime.evaluate", {
                "expression": script,
                "awaitPromise": True,
                "returnByValue": True
            })

            data = eval_res.get("result", {}).get("value", {})
            print("[4/5] Browser execution complete. Inspecting DOM results...")

            # Output results
            print("\n=== SUMMARY CARDS (Rendered) ===")
            print(data.get("statsHtml"))

            print(f"\n=== INVENTORY TABLE (Total Rendered Rows: {data.get('rowCount')}) ===")
            target_ids = ["P1001", "P1002", "P1005"]
            verified_items = {}
            for row in data.get("rows", []):
                cells = row.get("cells", [])
                if len(cells) >= 10:
                    sku = cells[1]
                    name = cells[0]
                    # Check target products
                    for pid, pmatch in [("P1001", "ELE-MOU-WRL"), ("P1002", "ELE-KEY-TKL"), ("P1005", "ELE-EXT-4WAY")]:
                        if pmatch in sku or pid in name:
                            verified_items[pid] = {
                                "product": cells[0],
                                "sku": cells[1],
                                "category": cells[2],
                                "on_hand": cells[3],
                                "reserved": cells[4],
                                "available": cells[5],
                                "reorder_level": cells[6],
                                "available_pct": cells[7],
                                "location": cells[8],
                                "status": cells[9],
                                "status_html": row.get("statusBadgeHtml"),
                                "bar_width": row.get("barWidth")
                            }

            for pid, info in verified_items.items():
                print(f"\n--- Product {pid} ---")
                for k, v in info.items():
                    print(f"  {k}: {v}")

            print("\n=== BROWSER CONSOLE STATUS ===")
            print(f"Total Console Logs: {len(console_logs)}")
            print(f"Total Console Errors: {len(console_errors)}")
            if console_errors:
                print("Console Errors Found:")
                for err in console_errors:
                    print(f"  [ERROR] {err}")
            else:
                print("Clean browser console: 0 errors detected.")

            return {
                "stats": data.get("statsHtml"),
                "rowCount": data.get("rowCount"),
                "verified_items": verified_items,
                "console_errors": console_errors
            }

    finally:
        print("[5/5] Terminating headless Edge...")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    asyncio.run(run_browser_verification())
