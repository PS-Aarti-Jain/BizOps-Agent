import argparse
import asyncio
import logging
import os
import sys
import yaml
from pathlib import Path

# Ensure UTF-8 output encoding on Windows console
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.config import DEFAULT_USER_ID, DEFAULT_USER_ROLE
from database.seed import seed_database
from agent.agent import BizOpsAgent


def cli_interactive_confirm(card_str: str) -> bool:
    print(card_str, end="")
    try:
        choice = input().strip().lower()
        return choice in ("y", "yes")
    except EOFError:
        return False


async def main():
    parser = argparse.ArgumentParser(description="BizOps Agent — Small Business Operations Assistant")
    parser.add_argument("--query", type=str, help="Single natural language query to execute")
    parser.add_argument("--demo", action="store_true", help="Run all 5 standard capstone demo scenarios")
    parser.add_argument("--web", action="store_true", help="Launch FastAPI Web UI dashboard")
    parser.add_argument("--user-id", type=str, default=DEFAULT_USER_ID, help="User ID context")
    parser.add_argument("--user-role", type=str, default=DEFAULT_USER_ROLE, choices=["Operator", "Manager", "Admin"], help="User RBAC Role")
    parser.add_argument("--auto-confirm", action="store_true", help="Automatically confirm purchase orders (for testing/ci)")

    args = parser.parse_args()

    # 1. Ensure database is initialized and seeded
    seed_database()

    # Handle Web UI Mode
    if args.web:
        import uvicorn
        from app.config import HOST, PORT
        print(f"Starting BizOps Agent Web UI on http://{HOST}:{PORT}...")
        config = uvicorn.Config("ui.app:app", host=HOST, port=PORT, reload=False)
        server = uvicorn.Server(config)
        await server.serve()
        return

    # 2. Safety check for --auto-confirm
    auto_confirm = args.auto_confirm
    env = os.getenv("ENVIRONMENT", "development").lower()
    if auto_confirm and env not in ("test", "ci", "development"):
        print("[ERROR] Safety Warning: --auto-confirm is forbidden in production environment!")
        sys.exit(1)

    # 3. Initialize Agent Client
    agent = BizOpsAgent(
        user_id=args.user_id,
        user_role=args.user_role,
        auto_confirm_writes=auto_confirm,
        interactive_confirm_fn=cli_interactive_confirm,
        verbose=True,
    )

    try:
        print("Connecting to independent MCP servers (Inventory, Order, Finance, Supplier)...")
        await agent.initialize()
        print("MCP Tool Discovery complete! Discovered Tools Registered.")

        # 4. Handle Demo Mode
        if args.demo:
            scenarios_file = Path(__file__).parent / "tests" / "scenarios.yaml"
            with open(scenarios_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            print("\n=======================================================")
            print("          RUNNING 5 CAPSTONE DEMO SCENARIOS            ")
            print("=======================================================\n")

            for sc in data.get("scenarios", []):
                print(f"\n--- [DEMO SCENARIO: {sc['name']}] ---")
                print(f"Query: \"{sc['query']}\"")
                answer = await agent.run_query(sc["query"])
                print(f"\nResult:\n{answer}\n")
                print("-" * 65)

            return

        # 5. Handle Single Query Mode
        if args.query:
            answer = await agent.run_query(args.query)
            print(f"\nResult:\n{answer}\n")
            return

        # 6. Interactive Shell Mode
        print("\n--- BizOps Agent Interactive Mode ---")
        print(f"Logged in as: User '{args.user_id}' | Role: '{args.user_role}'")
        print("Type your question below (or 'exit' / 'quit' to end):\n")

        while True:
            try:
                user_input = input("BizOps> ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    print("Exiting BizOps Agent. Goodbye!")
                    break

                await agent.run_query(user_input)
                print("\n")
            except (KeyboardInterrupt, EOFError):
                print("\nExiting. Goodbye!")
                break
    finally:
        await agent.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(main())
