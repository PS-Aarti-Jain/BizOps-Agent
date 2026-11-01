"""BizOps Agent - Web UI Backend (FastAPI)"""
import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    ALLOWED_ORIGINS,
    DB_PATH,
    DEFAULT_TOKEN_USER_MAP,
    DEFAULT_USER_ID,
    DEFAULT_USER_ROLE,
    DEMO_MODE,
    HOST,
    PA_EXPIRATION_SECONDS,
    PORT,
    ROLE_HIERARCHY,
)
from database.seed import seed_database
from agent.agent import BizOpsAgent
from services.order_service import OrderDomainService
from services.finance_service import FinanceDomainService
from services.inventory_service import InventoryDomainService, classify_inventory_level

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bizops.ui")

_agent: Optional[BizOpsAgent] = None
_agent_ready: bool = False
_agent_lock = asyncio.Lock()
_mcp_status: Dict[str, str] = {
    "inventory": "connecting",
    "orders": "connecting",
    "finance": "connecting",
    "suppliers": "connecting",
    "policies": "connecting",
    "google_sheets": "connected",
}
# Server-side pending action store: action_id -> dict
_pending_actions: Dict[str, Dict[str, Any]] = {}
_session_progress: Dict[str, List[str]] = {}
_session_jobs: Dict[str, Dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent, _agent_ready, _mcp_status
    current_db = os.getenv("DB_PATH", str(DB_PATH))
    seed_database(Path(current_db))
    try:
        _agent = BizOpsAgent(
            user_id=DEFAULT_USER_ID,
            user_role=DEFAULT_USER_ROLE,
            auto_confirm_writes=False,
            interactive_confirm_fn=None,
            verbose=True,
        )
        if os.getenv("ENVIRONMENT") == "test":
            from unittest.mock import AsyncMock
            _agent.ollama.is_available = AsyncMock(return_value=False)
        await _agent.initialize()
        _agent_ready = True
        for k in _mcp_status:
            _mcp_status[k] = "connected"
        logger.info("BizOps Agent ready.")
    except Exception as e:
        logger.error(f"Agent init failed: {e}")
        for k in _mcp_status:
            _mcp_status[k] = "error"
    yield
    if _agent:
        await _agent.close()


app = FastAPI(title="BizOps Agent UI", lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        # Explicitly reject disallowed origin for security on API endpoints
        if origin and origin not in ALLOWED_ORIGINS and "*" not in ALLOWED_ORIGINS:
            if request.url.path.startswith("/api/"):
                return Response(
                    content=json.dumps({"detail": f"Origin '{origin}' not allowed by CORS policy."}),
                    status_code=403,
                    media_type="application/json",
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

ui_dir = Path(__file__).parent
static_dir = ui_dir / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class QueryRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    role: Optional[str] = None
    wait_timeout: Optional[float] = 2.0
    mode: Optional[str] = "auto"


class ConfirmRequest(BaseModel):
    approve: Optional[bool] = None
    approved: Optional[bool] = None
    role: Optional[str] = None

    def get_approval(self) -> bool:
        if self.approve is not None:
            return self.approve
        if self.approved is not None:
            return self.approved
        return False


def get_current_user(request: Request, body_role: Optional[str] = None) -> Dict[str, str]:
    """
    Derives user_id and role server-side.
    Priority:
    1. Valid 'Authorization: Bearer <token>' header mapped from config/env.
       When a token is provided, client-supplied role parameters are strictly IGNORED.
    2. If DEMO_MODE is true and no token is passed, allows demo role dropdown/header.
    3. If DEMO_MODE is false and no token (or invalid token) is passed, raises 401 Unauthorized.
    """
    auth_header = request.headers.get("Authorization")
    token_map = dict(DEFAULT_TOKEN_USER_MAP)
    env_token_map = os.getenv("TOKEN_USER_MAP")
    if env_token_map:
        try:
            token_map.update(json.loads(env_token_map))
        except Exception:
            pass

    demo_mode_active = os.getenv("DEMO_MODE", str(DEMO_MODE)).lower() in ("true", "1", "yes")

    if auth_header:
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid Authorization header format. Expected 'Bearer <token>'.")
        token = auth_header[len("Bearer "):].strip()
        if token in token_map:
            user_info = token_map[token]
            return {
                "user_id": user_info["user_id"],
                "role": user_info["role"],
                "name": user_info.get("name", user_info["user_id"]),
            }
        raise HTTPException(status_code=401, detail="Invalid authentication token.")

    # No token provided
    if demo_mode_active:
        req_role = request.headers.get("X-Demo-Role") or body_role or DEFAULT_USER_ROLE
        if req_role not in ROLE_HIERARCHY:
            req_role = DEFAULT_USER_ROLE
        return {
            "user_id": f"DEMO-{req_role.upper()}",
            "role": req_role,
            "name": f"Demo {req_role}",
        }

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide a valid 'Authorization: Bearer <token>' header.",
    )


def db_query(sql: str, params=()):
    target_path = os.getenv("DB_PATH", str(DB_PATH))
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTMLResponse(content=(ui_dir / "index.html").read_text(encoding="utf-8"))


@app.get("/api/status")
async def get_status():
    provider_name = "groq"
    model_name = "llama-3.3-70b-versatile"
    badge_label = "LLM: groq/llama-3.3-70b-versatile"
    if _agent and hasattr(_agent, "llm"):
        provider_name = getattr(_agent.llm, "provider_name", "groq")
        model_name = getattr(_agent.llm, "model_name", "llama-3.3-70b-versatile")
        badge_label = getattr(_agent, "_format_provenance_badge", lambda: f"LLM: {provider_name}/{model_name}")()
        if not badge_label or badge_label == "UNKNOWN":
            badge_label = f"LLM: {provider_name}/{model_name}"

    return {
        "agent_ready": _agent_ready,
        "mcp_servers": _mcp_status,
        "user_id": DEFAULT_USER_ID,
        "user_role": DEFAULT_USER_ROLE,
        "demo_mode": os.getenv("DEMO_MODE", str(DEMO_MODE)).lower() in ("true", "1", "yes"),
        "llm_provider": provider_name,
        "llm_model": model_name,
        "llm_label": badge_label,
        "cloud_llm_active": bool(os.getenv("GROQ_API_KEY")),
        "circuit_breaker_open": False,
    }


@app.get("/api/query/progress")
async def get_query_progress(session_id: str):
    """Returns live status steps and state for the active query session."""
    steps = _session_progress.get(session_id, [])
    job = _session_jobs.get(session_id)
    status = job.get("status", "PROCESSING") if job else "PROCESSING"
    return {"session_id": session_id, "status": status, "steps": steps}


@app.get("/api/query/result")
async def get_query_result(session_id: str):
    """Returns the result or current execution state of an asynchronous query job."""
    job = _session_jobs.get(session_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No query job found for session '{session_id}'.")

    status = job.get("status", "PROCESSING")
    res = {
        "status": status,
        "session_id": session_id,
        "steps": _session_progress.get(session_id, job.get("steps", [])),
    }

    if status in ("ok", "TIMED_OUT"):
        res["answer"] = job.get("answer")
        res["elapsed_seconds"] = job.get("elapsed_seconds")
        res["llm_badge"] = job.get("llm_badge")
    elif status == "PENDING_CONFIRMATION":
        res["action_id"] = job.get("action_id")
        res["card"] = job.get("card")
        res["elapsed_seconds"] = job.get("elapsed_seconds")
        res["llm_badge"] = job.get("llm_badge")
    elif status == "FAILED":
        res["error"] = job.get("error", "Unknown execution failure")

    return res


@app.get("/api/data/inventory")
async def get_inventory():
    service = InventoryDomainService()
    res = service.get_all_inventory_items()
    return {"status": "ok", "data": res.get("items", [])}


@app.get("/api/data/orders")
async def get_orders():
    service = OrderDomainService()
    pending_res = service.get_pending_orders()
    orders = db_query("SELECT * FROM orders ORDER BY order_date DESC")
    items = db_query("""
        SELECT oi.*, p.name as product_name
        FROM order_items oi JOIN products p ON oi.product_id = p.product_id
    """)
    items_map = {}
    for it in items:
        items_map.setdefault(it["order_id"], []).append(it)
    for o in orders:
        o["items"] = items_map.get(o["order_id"], [])
    return {
        "status": "ok",
        "data": orders,
        "summary": {
            "total_count": len(orders),
            "pending_count": pending_res.get("pending_count", 0),
            "pending_amount": pending_res.get("pending_amount", 0.0),
            "processing_count": pending_res.get("processing_count", 0),
            "processing_amount": pending_res.get("processing_amount", 0.0),
            "active_amount": pending_res.get("total_active_amount", 0.0),
        },
    }


@app.get("/api/data/finance")
async def get_finance():
    service = FinanceDomainService()
    oct_summary = service.get_financial_summary(month="2026-10")
    sep_summary = service.get_financial_summary(month="2026-09")
    pending_payments = service.get_pending_payments()
    invoices = db_query("""
        SELECT inv.*, c.name as customer_name
        FROM invoices inv LEFT JOIN customers c ON inv.customer_id = c.customer_id
        ORDER BY inv.due_date DESC
    """)
    for inv in invoices:
        inv["outstanding"] = round(inv["total_amount"] - inv["paid_amount"], 2)
    return {
        "status": "ok",
        "data": invoices,
        "summary": {
            "october_2026": oct_summary,
            "september_2026": sep_summary,
            "total_outstanding": pending_payments.get("total_outstanding_amount", 0.0),
            "total_overdue": pending_payments.get("total_overdue_amount", 0.0),
        },
    }


@app.get("/api/data/suppliers")
async def get_suppliers():
    suppliers = db_query("SELECT * FROM suppliers ORDER BY rating DESC")
    prods = db_query("""
        SELECT sp.*, p.name as product_name
        FROM supplier_products sp JOIN products p ON sp.product_id = p.product_id
    """)
    sp_map = {}
    for p in prods:
        sp_map.setdefault(p["supplier_id"], []).append(p)
    for s in suppliers:
        s["products"] = sp_map.get(s["supplier_id"], [])
    return {"status": "ok", "data": suppliers}


@app.get("/api/data/purchase_orders")
async def get_purchase_orders():
    pos = db_query("""
        SELECT po.*, s.name as supplier_name FROM purchase_orders po
        LEFT JOIN suppliers s ON po.supplier_id = s.supplier_id
        ORDER BY po.created_at DESC LIMIT 20
    """)
    return {"status": "ok", "data": pos}


@app.get("/api/scenarios")
async def get_scenarios():
    import yaml
    scenarios_file = Path(__file__).parent.parent / "tests" / "scenarios.yaml"
    with open(scenarios_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {"status": "ok", "scenarios": data.get("scenarios", [])}


async def _execute_query_job(session_id: str, query: str, user_id: str, role: str):
    global _agent
    job = _session_jobs.get(session_id)
    if not job:
        return

    def progress_callback(msg: str):
        _session_progress.setdefault(session_id, []).append(msg)
        if job and "steps" in job:
            job["steps"].append(msg)

    try:
        async with _agent_lock:
            _agent.user_id = user_id
            _agent.user_role = role
            _agent.auto_confirm_writes = False

            start = time.time()
            result = await asyncio.wait_for(
                _agent.run_query(query, session_id=session_id, on_progress=progress_callback),
                timeout=110.0,
            )
            elapsed = round(time.time() - start, 2)
            badge = getattr(
                _agent,
                "_format_provenance_badge",
                lambda: getattr(_agent, "last_llm_display_label", "LLM: groq/llama-3.3-70b-versatile"),
            )()
            if not badge or badge == "UNKNOWN":
                badge = "LLM: groq/llama-3.3-70b-versatile"

            job["completed_at"] = time.time()
            job["elapsed_seconds"] = elapsed
            job["llm_badge"] = badge

            if isinstance(result, dict) and result.get("status") == "PENDING_CONFIRMATION":
                action_id = result["action_id"]
                _pending_actions[action_id] = {
                    "action_id": action_id,
                    "user_id": user_id,
                    "user_role": role,
                    "session_id": session_id,
                    "created_at": time.time(),
                    "expires_at": time.time() + PA_EXPIRATION_SECONDS,
                    "card": result.get("card", ""),
                }
                job["status"] = "PENDING_CONFIRMATION"
                job["action_id"] = action_id
                job["card"] = result.get("card", "")
            else:
                if isinstance(result, str) and "**Status:** TIMED_OUT" in result:
                    job["status"] = "TIMED_OUT"
                else:
                    job["status"] = "ok"
                job["answer"] = result
    except asyncio.TimeoutError:
        logger.warning(f"Query execution timed out after 110s for session {session_id}")
        job["status"] = "TIMED_OUT"
        job["answer"] = "Execution timed out after 110s. The query required more time than the server limit."
        job["completed_at"] = time.time()
        job["elapsed_seconds"] = 110.0
    except Exception as e:
        logger.error(f"Job execution failed for {session_id}: {e}", exc_info=True)
        job["status"] = "FAILED"
        job["error"] = str(e)
        job["completed_at"] = time.time()


@app.post("/api/query")
async def run_query(req: QueryRequest, request: Request):
    global _agent
    if not _agent_ready or not _agent:
        raise HTTPException(503, "Agent not ready. MCP servers still connecting...")

    current_user = get_current_user(request, body_role=req.role)
    session_id = req.session_id or f"SESS-{uuid.uuid4().hex[:8].upper()}"

    # 1. Idempotency check: if query actively processing for this session, return PROCESSING
    existing_job = _session_jobs.get(session_id)
    if existing_job and existing_job.get("status") == "PROCESSING":
        return {
            "status": "PROCESSING",
            "session_id": session_id,
            "message": "Query is currently processing for this session.",
            "steps": _session_progress.get(session_id, []),
        }

    # 2. Cleanup old stale jobs if store exceeds 200 items
    if len(_session_jobs) > 200:
        now_ts = time.time()
        stale_keys = [k for k, v in _session_jobs.items() if (now_ts - v.get("created_at", 0)) > 1800]
        for k in stale_keys:
            _session_jobs.pop(k, None)

    _session_progress[session_id] = []
    job_record = {
        "session_id": session_id,
        "user_id": current_user["user_id"],
        "user_role": current_user["role"],
        "query": req.query,
        "status": "PROCESSING",
        "created_at": time.time(),
        "started_at": time.time(),
        "completed_at": None,
        "elapsed_seconds": None,
        "answer": None,
        "card": None,
        "action_id": None,
        "llm_badge": None,
        "error": None,
        "steps": [],
    }
    _session_jobs[session_id] = job_record

    # 3. Launch background execution task
    task = asyncio.create_task(
        _execute_query_job(session_id, req.query, current_user["user_id"], current_user["role"])
    )
    job_record["task"] = task

    # 4. Synchronous wait window for fast responses & backward compatibility
    mode = (req.mode or "auto").lower()
    wait_sec = req.wait_timeout if req.wait_timeout is not None else 2.0
    if mode == "sync":
        wait_sec = 115.0
    elif mode == "async":
        wait_sec = 0.0

    if wait_sec > 0:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=wait_sec)
        except asyncio.TimeoutError:
            pass
        except Exception:
            pass

    # 5. Check outcome after synchronous wait window
    st = job_record.get("status")
    if st == "ok":
        return {
            "status": "ok",
            "answer": job_record.get("answer"),
            "session_id": session_id,
            "elapsed_seconds": job_record.get("elapsed_seconds"),
            "llm_badge": job_record.get("llm_badge"),
            "steps": _session_progress.get(session_id, []),
        }
    elif st == "PENDING_CONFIRMATION":
        return {
            "status": "PENDING_CONFIRMATION",
            "action_id": job_record.get("action_id"),
            "card": job_record.get("card"),
            "session_id": session_id,
            "elapsed_seconds": job_record.get("elapsed_seconds"),
            "llm_badge": job_record.get("llm_badge"),
            "steps": _session_progress.get(session_id, []),
        }
    elif st == "TIMED_OUT":
        return {
            "status": "TIMED_OUT",
            "answer": job_record.get("answer"),
            "session_id": session_id,
            "elapsed_seconds": job_record.get("elapsed_seconds"),
            "llm_badge": job_record.get("llm_badge"),
            "steps": _session_progress.get(session_id, []),
        }
    elif st == "FAILED":
        raise HTTPException(500, job_record.get("error", "Query execution failed."))

    # Still processing -> return non-blocking PROCESSING state
    return {
        "status": "PROCESSING",
        "session_id": session_id,
        "message": "Query accepted and processing in background.",
        "steps": _session_progress.get(session_id, []),
    }


@app.post("/api/confirm/{action_id}")
async def confirm_action(action_id: str, req: ConfirmRequest, request: Request):
    global _agent
    if not _agent_ready or not _agent:
        raise HTTPException(503, "Agent not ready. MCP servers still connecting...")

    current_user = get_current_user(request, body_role=req.role)

    # 1. Lookup Pending Action
    record = _pending_actions.get(action_id)
    pa = _agent.safety_gateway.pending_actions.get(action_id)
    if not record and not pa:
        raise HTTPException(status_code=404, detail=f"Pending action '{action_id}' not found.")

    # 2. Verify Creator Ownership
    creator_id = record["user_id"] if record else pa.user_id
    if creator_id and current_user["user_id"] != creator_id:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: Action '{action_id}' was created by '{creator_id}'. User '{current_user['user_id']}' cannot confirm it.",
        )

    # 3. Verify Expiration
    is_expired = False
    now_ts = time.time()
    if record and now_ts > record.get("expires_at", 0):
        is_expired = True
    if pa and pa.is_expired():
        is_expired = True

    if is_expired:
        _pending_actions.pop(action_id, None)
        if pa:
            _agent.safety_gateway.pending_actions.pop(action_id, None)
        raise HTTPException(status_code=400, detail=f"Pending action '{action_id}' has expired.")

    # 4. Execute or Reject Action with Agent Mutex
    approve = req.get_approval()
    sess_id = record["session_id"] if record else f"SESS-{uuid.uuid4().hex[:8].upper()}"

    async with _agent_lock:
        res = await _agent.execute_confirmed_action(
            action_id=action_id,
            approved=approve,
            user_id=current_user["user_id"],
            user_role=current_user["role"],
            session_id=sess_id,
        )
        _pending_actions.pop(action_id, None)
        if sess_id in _session_jobs:
            _session_jobs[sess_id]["status"] = res.get("status", "SUCCESS")
            _session_jobs[sess_id]["answer"] = res.get("answer") or res.get("message")
        return res


if __name__ == "__main__":
    host = os.getenv("HOST", HOST)
    port = int(os.getenv("PORT", str(PORT)))
    uvicorn.run("app:app", host=host, port=port, reload=False, log_level="info")
