from typing import List


class PlannerAgent:
    @staticmethod
    def plan_subtasks(user_query: str) -> List[str]:
        """
        Decomposes composite natural language queries into logical sub-tasks.
        """
        q = user_query.strip()
        # Check for composite coordinating conjunctions with distinct operational topics
        if " and " in q.lower() and any(w in q.lower() for w in ["check", "compare", "update", "verify"]):
            parts = [p.strip() for p in q.split(" and ") if len(p.strip()) > 5]
            if len(parts) > 1:
                return parts
        return [q]


class AggregatorAgent:
    @staticmethod
    def aggregate_results(user_query: str, subtasks: List[str], results: List[str]) -> str:
        """
        Synthesizes multiple sub-task results into an integrated business answer.
        """
        if len(results) == 1:
            return results[0]

        sections = []
        for i, (sub, res) in enumerate(zip(subtasks, results), start=1):
            sections.append(f"### Subtask {i}: {sub}\n{res}")

        return "\n\n---\n\n".join(sections)

