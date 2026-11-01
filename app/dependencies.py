import os
from typing import Dict, Optional
from fastapi import HTTPException, Request
from app.config import DEFAULT_TOKEN_USER_MAP, DEFAULT_USER_ROLE, DEMO_MODE, ROLE_HIERARCHY


def get_current_user(request: Request, body_role: Optional[str] = None) -> Dict[str, str]:
    """
    Derives user_id and role server-side.
    """
    auth_header = request.headers.get("Authorization")
    token_map = dict(DEFAULT_TOKEN_USER_MAP)

    demo_mode_active = os.getenv("DEMO_MODE", str(DEMO_MODE)).lower() in ("true", "1", "yes")

    if auth_header:
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Expected 'Bearer <token>'.")
        token = auth_header[len("Bearer "):].strip()
        if token in token_map:
            user_info = token_map[token]
            return {
                "user_id": user_info["user_id"],
                "role": user_info["role"],
                "name": user_info.get("name", user_info["user_id"]),
            }
        raise HTTPException(status_code=401, detail="Invalid authentication token.")

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

