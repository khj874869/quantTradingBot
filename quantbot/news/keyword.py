from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class KeywordScorer:
    positive: List[str]
    negative: List[str]

    def score(self, text: str) -> Tuple[float, List[str]]:
        t = (text or "").strip()
        hits = []
        score = 0.0
        for k in self.positive:
            if k and k in t:
                hits.append(f"+{k}")
                score += 1.0
        for k in self.negative:
            if k and k in t:
                hits.append(f"-{k}")
                score -= 2.0
        return score, hits

