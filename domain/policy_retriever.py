from collections import Counter
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

POLICIES_DIR = Path(__file__).resolve().parent.parent / "docs" / "policies"


class PolicyRetriever:
    """
    Lightweight, dependency-free TF-IDF and keyword policy retriever.
    Indexes business policy markdown documents and retrieves relevant clauses for citations.
    """

    def __init__(self, docs_dir: Optional[Path] = None):
        self.docs_dir = docs_dir or POLICIES_DIR
        self.documents: List[Dict[str, Any]] = []  # List of chunks
        self.doc_count = 0
        self.df: Counter = Counter()  # Document frequency of terms
        self._load_and_index()

    def _tokenize(self, text: str) -> List[str]:
        words = re.findall(r'\b[a-z0-9_-]{3,}\b', text.lower())
        stopwords = {
            "the", "and", "for", "with", "this", "that", "from", "are", "have", "not",
            "all", "any", "must", "can", "will", "been", "has", "more", "than", "when"
        }
        return [w for w in words if w not in stopwords]

    def _load_and_index(self):
        if not self.docs_dir.exists():
            return

        chunks = []
        for file_path in sorted(self.docs_dir.glob("*.md")):
            try:
                content = file_path.read_text(encoding="utf-8")
                doc_name = file_path.stem
                
                # Split document by sections
                sections = re.split(r'\n(?=##?\s+)', content)
                for sec in sections:
                    sec_clean = sec.strip()
                    if not sec_clean:
                        continue
                    header_match = re.match(r'##?\s+(.*)', sec_clean)
                    section_title = header_match.group(1).strip() if header_match else "General"
                    
                    tokens = self._tokenize(sec_clean)
                    if tokens:
                        chunks.append({
                            "doc_name": doc_name,
                            "filename": file_path.name,
                            "section": section_title,
                            "content": sec_clean,
                            "tokens": tokens,
                            "term_counts": Counter(tokens),
                        })
            except Exception:
                pass

        self.documents = chunks
        self.doc_count = len(chunks)

        # Compute document frequency for each term
        self.df = Counter()
        for doc in self.documents:
            unique_terms = set(doc["tokens"])
            for t in unique_terms:
                self.df[t] += 1

    def search(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        query_tokens = self._tokenize(query)
        if not query_tokens or not self.documents:
            return []

        scored_docs = []
        for doc in self.documents:
            score = 0.0
            doc_len = len(doc["tokens"]) or 1
            tc = doc["term_counts"]

            for q_term in query_tokens:
                if q_term in tc:
                    # Term frequency with length normalization
                    tf = tc[q_term] / doc_len
                    # IDF with smoothing
                    idf = math.log((self.doc_count + 1) / (self.df.get(q_term, 0) + 1)) + 1.0
                    score += tf * idf

            if score > 0.0:
                scored_docs.append({
                    "doc_name": doc["doc_name"],
                    "filename": doc["filename"],
                    "section": doc["section"],
                    "text": doc["content"],
                    "score": round(score, 4),
                })

        scored_docs.sort(key=lambda x: x["score"], reverse=True)
        return scored_docs[:top_k]

