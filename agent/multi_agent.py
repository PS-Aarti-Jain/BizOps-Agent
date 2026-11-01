"""
Multi-Agent Orchestration Module (Course-Aligned Architecture).

Maps directly to standard multi-agent patterns:
1. Planner Agent: Analyzes user queries, determines task decomposition, and generates sub-task plans.
2. Specialist / Worker Agent: Executes individual sub-tasks via the ReAct BizOpsAgent loop.
3. Aggregator Agent: Synthesizes individual worker findings into a unified executive report.
"""
import logging
import re
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class PlannerAgent:
    """
    Decomposes compound or multi-intent business queries into discrete sub-tasks.
    """

    @staticmethod
    def plan_subtasks(query: str) -> List[str]:
        # Identify compound query markers
        q_clean = query.strip()
        split_patterns = [
            r'\s+and\s+also\s+',
            r'\s+as\s+well\s+as\s+',
            r'\s*;\s*',
            r'\.\s+(?=[A-Z])',
        ]
        
        for pat in split_patterns:
            parts = [p.strip() for p in re.split(pat, q_clean, flags=re.IGNORECASE) if len(p.strip()) > 10]
            if len(parts) > 1:
                logger.info(f"Planner decomposed query into {len(parts)} subtasks using pattern '{pat}'")
                return parts

        return [q_clean]


class AggregatorAgent:
    """
    Merges outputs from multiple specialist sub-task runs into a single coherent business response.
    """

    @staticmethod
    def aggregate_results(original_query: str, subtasks: List[str], results: List[str]) -> str:
        if len(results) == 1:
            return results[0]

        answers = []
        evidence_blocks = []
        statuses = []
        next_steps = []

        for idx, (subtask, res) in enumerate(zip(subtasks, results), start=1):
            answers.append(f"• **Part {idx} ({subtask})**: {extract_section(res, 'Answer', res[:150])}")
            ev = extract_section(res, 'Evidence')
            if ev:
                evidence_blocks.append(f"Sub-task {idx} Evidence:\n{ev}")
            st = extract_section(res, 'Status')
            if st:
                statuses.append(st)
            ns = extract_section(res, 'Next Step')
            if ns:
                next_steps.append(f"{idx}. {ns}")

        overall_status = "PARTIAL_SUCCESS" if any(s in ("FAILED", "CANNOT_FULFILL") for s in statuses) else "SUCCESS"
        if not statuses:
            overall_status = "PROCESSED"

        combined_answer = "\n".join(answers)
        combined_evidence = "\n\n".join(evidence_blocks) if evidence_blocks else "• Operational findings consolidated across specialist tasks."
        combined_next_step = " ".join(next_steps) if next_steps else "Review operational sub-task summaries."

        return f"""**Answer:**
{combined_answer}

**Evidence:**
{combined_evidence}

**Status:** {overall_status}
**Next Step:** {combined_next_step}"""


def extract_section(text: str, section_name: str, default: str = "") -> str:
    pattern = rf'\*\*{section_name}:\*\*\s*(.*?)(?=\n\*\*[A-Za-z ]+:\*\*|$)'
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return default

