import json
import logging
import time
from typing import Any, Dict, Optional
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from agent.security import SecuritySanitizer

logger = logging.getLogger("BizOpsObservability")
console = Console()


class SecretRedactionFilter(logging.Filter):
    """
    Logging filter that sanitizes API keys, secrets, and Bearer tokens
    from all log records before output.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = SecuritySanitizer.mask_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (SecuritySanitizer.mask_secrets(v) if isinstance(v, str)
                        else (SecuritySanitizer.mask_secrets(str(v)) if isinstance(v, Exception) else v))
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    SecuritySanitizer.mask_secrets(a) if isinstance(a, str)
                    else (SecuritySanitizer.mask_secrets(str(a)) if isinstance(a, Exception) else a)
                    for a in record.args
                )
        return True


logger.addFilter(SecretRedactionFilter())
logging.getLogger().addFilter(SecretRedactionFilter())


def install_secret_redaction_filter(target_logger: Optional[logging.Logger] = None):
    """
    Attaches the SecretRedactionFilter to a specific logger or the root logger.
    """
    l = target_logger or logging.getLogger()
    l.addFilter(SecretRedactionFilter())


class AgentTraceLogger:
    def __init__(self, session_id: str, verbose: bool = True):
        self.session_id = session_id
        self.verbose = verbose
        self.traces = []

    def log_query(self, user_query: str, user_id: str, user_role: str):
        event = {
            "session_id": self.session_id,
            "type": "USER_QUERY",
            "query": user_query,
            "user_id": user_id,
            "user_role": user_role,
            "timestamp": time.time(),
        }
        self.traces.append(event)
        if self.verbose:
            console.print(
                Panel(
                    f"[bold cyan]User Query:[/bold cyan] {user_query}\n"
                    f"[dim]User: {user_id} | Role: {user_role} | Session: {self.session_id}[/dim]",
                    title="[bold green]BizOps Agent Initialized[/bold green]",
                    border_style="green",
                )
            )

    @staticmethod
    def _redact_pii(text: str) -> str:
        import re
        # Redact emails e.g. orders@abcretail.com -> o***@abcretail.com
        text = re.sub(r"\b([a-zA-Z0-9_.+-])[a-zA-Z0-9_.+-]*@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b", r"\1***@\2", text)
        # Redact street addresses e.g. "100 Market St, San Francisco, CA"
        text = re.sub(r"\b\d+\s+[A-Za-z0-9\s]+(?:St|Street|Way|Blvd|Avenue|Ave|Rd|Road),\s*[A-Za-z\s]+,\s*[A-Z]{2}\b", "[REDACTED ADDRESS]", text)
        return text

    def log_iteration(
        self,
        iteration: int,
        action: str,
        owning_server: Optional[str],
        args: Dict[str, Any],
        latency_ms: float,
        result: Any,
    ):
        event = {
            "session_id": self.session_id,
            "type": "TOOL_EXECUTION",
            "iteration": iteration,
            "tool": action,
            "server": owning_server,
            "args": args,
            "latency_ms": latency_ms,
            "result_status": (
                result.get("status") if isinstance(result, dict) else "SUCCESS"
            ),
            "timestamp": time.time(),
        }
        self.traces.append(event)
        if self.verbose:
            res_str = (
                json.dumps(result, indent=2)
                if isinstance(result, (dict, list))
                else str(result)
            )
            safe_res_str = self._redact_pii(res_str)
            console.print(
                Panel(
                    f"[bold yellow]Step {iteration}:[/bold yellow] Tool [bold magenta]{action}[/bold magenta] on [bold blue]{owning_server}[/bold blue]\n"
                    f"[bold white]Args:[/bold white] {json.dumps(args)}\n"
                    f"[bold white]Latency:[/bold white] {latency_ms:.2f} ms\n\n"
                    f"[bold white]Result:[/bold white]\n{safe_res_str[:300]}{'...' if len(safe_res_str) > 300 else ''}",
                    title=f"[bold yellow]Iteration {iteration}[/bold yellow]",
                    border_style="yellow",
                )
            )

    def log_pending_action(self, pa_card_str: str):
        if self.verbose:
            console.print(
                Panel(
                    pa_card_str,
                    title="[bold red]CONFIRMATION REQUIRED (SAFETY GATEWAY)[/bold red]",
                    border_style="red",
                )
            )

    def log_reflection(self, status: str, reason: str, missing_tool: Optional[str]):
        event = {
            "session_id": self.session_id,
            "type": "REFLECTION",
            "status": status,
            "reason": reason,
            "missing_tool": missing_tool,
            "timestamp": time.time(),
        }
        self.traces.append(event)
        if self.verbose:
            color = "green" if status == "ready" else "red"
            console.print(
                Panel(
                    f"[bold {color}]Status:[/bold {color}] {status}\n"
                    f"[bold white]Reason:[/bold white] {reason}\n"
                    + (
                        f"[bold red]Missing Tool:[/bold red] {missing_tool}"
                        if missing_tool
                        else ""
                    ),
                    title="[bold blue]Reflection Quality Audit[/bold blue]",
                    border_style="blue",
                )
            )

    def log_final_answer(self, final_answer: str):
        event = {
            "session_id": self.session_id,
            "type": "FINAL_ANSWER",
            "answer": final_answer,
            "timestamp": time.time(),
        }
        self.traces.append(event)
        if self.verbose:
            console.print(
                Panel(
                    final_answer,
                    title="[bold green]Final Answer[/bold green]",
                    border_style="green",
                )
            )
