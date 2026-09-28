"""Submodular Budgeted Capability Selector and Canonical Radix Prefix Aligner.

Solves the Budgeted Maximum Coverage / Knapsack problem for MCP capability
schemas under strict context token ceilings, while enforcing deterministic
lexicographic prefix order to maximize prompt KV-cache reuse.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Set, Tuple


def estimate_schema_tokens(schema_obj: Any) -> int:
    """Deterministic token count estimation (approx 3.8 chars per token)."""
    serialized = json.dumps(schema_obj, separators=(",", ":"), ensure_ascii=False)
    # Baseline + JSON structural overhead
    return max(15, math.ceil(len(serialized) / 3.8))


class SubmodularKnapsackSelector:
    """Selects an optimal subset of capabilities within a token budget."""

    def __init__(self, token_budget: int = 2048):
        if token_budget < 64:
            raise ValueError("token_budget must be at least 64 tokens")
        self.token_budget = token_budget

    @staticmethod
    def _extract_terms(text: str) -> Set[str]:
        """Extract canonical lowercase word tokens."""
        clean = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
        return {term for term in clean.split() if len(term) >= 2}

    def _compute_item_utility(
        self, capability: Dict[str, Any], query_terms: Set[str]
    ) -> Tuple[float, Set[str]]:
        """Compute base utility and covered query terms."""
        text = " ".join([
            capability.get("id", ""),
            capability.get("name", ""),
            capability.get("description", ""),
            json.dumps(capability.get("inputSchema", {})),
        ])
        cap_terms = self._extract_terms(text)
        covered = query_terms.intersection(cap_terms)
        
        # Base coverage score
        base_score = float(len(covered))
        # Exact name match booster
        name_terms = self._extract_terms(capability.get("name", ""))
        if query_terms.intersection(name_terms):
            base_score += 2.0
        # Exploration baseline for universal discovery tools
        if base_score == 0.0 and any(t in name_terms for t in ("overview", "search", "symbols")):
            base_score = 0.5
            
        return base_score, covered

    def select(
        self,
        capabilities: List[Dict[str, Any]],
        query: str,
        *,
        max_tokens: int | None = None,
        mode: str = "submodular",
    ) -> Dict[str, Any]:
        """Select top capabilities maximizing submodular gain under token budget.

        Modes:
        - 'submodular': Greedy (1 - 1/e) submodular Knapsack selection
        - 'exact': Branch-and-bound exact 0/1 Knapsack (for N <= 20)
        - 'lexical': Baseline score without submodular diversity
        """
        budget = max_tokens if max_tokens is not None else self.token_budget
        query_terms = self._extract_terms(query)
        if not query_terms:
            query_terms = {"repo", "symbol", "overview"}

        # Precompute costs and utilities
        items = []
        for cap in capabilities:
            cost = estimate_schema_tokens(cap)
            utility, covered = self._compute_item_utility(cap, query_terms)
            items.append({
                "cap": cap,
                "cost": cost,
                "utility": utility,
                "covered": covered,
                "id": cap.get("id", ""),
            })

        if mode == "exact" and len(items) <= 20:
            selected_items = self._solve_exact(items, budget)
        elif mode == "lexical":
            selected_items = self._solve_lexical(items, budget)
        else:
            selected_items = self._solve_submodular_greedy(items, budget, query_terms)

        # Enforce Canonical Radix Prefix Ordering
        # Order selected tools by deterministic repository and name hashes
        ordered_capabilities = self.canonical_prefix_sort(
            [it["cap"] for it in selected_items]
        )

        total_cost = sum(estimate_schema_tokens(c) for c in ordered_capabilities)
        total_utility = sum(
            self._compute_item_utility(c, query_terms)[0] for c in ordered_capabilities
        )

        return {
            "query": query,
            "token_budget": budget,
            "tokens_used": total_cost,
            "token_reduction_pct": round(
                max(0.0, 100.0 * (1.0 - (total_cost / max(1, sum(it["cost"] for it in items))))),
                2,
            ) if items else 0.0,
            "total_candidates": len(capabilities),
            "selected_count": len(ordered_capabilities),
            "total_utility": round(total_utility, 3),
            "mode": mode,
            "capabilities": ordered_capabilities,
        }

    def _solve_submodular_greedy(
        self, items: List[Dict[str, Any]], budget: int, query_terms: Set[str]
    ) -> List[Dict[str, Any]]:
        """Greedy (1 - 1/e) submodular Knapsack solver."""
        selected: List[Dict[str, Any]] = []
        current_cost = 0
        covered_global: Set[str] = set()
        remaining = list(items)

        while remaining:
            best_idx = -1
            best_ratio = -1.0

            for i, it in enumerate(remaining):
                if current_cost + it["cost"] > budget:
                    continue
                # Marginal submodular gain: newly covered query terms + diminishing return
                new_covered = it["covered"].difference(covered_global)
                marginal_gain = float(len(new_covered)) + (0.2 * it["utility"])
                ratio = marginal_gain / max(1.0, float(it["cost"]))

                if ratio > best_ratio:
                    best_ratio = ratio
                    best_idx = i

            if best_idx == -1 or best_ratio <= 0.0:
                break

            chosen = remaining.pop(best_idx)
            selected.append(chosen)
            current_cost += chosen["cost"]
            covered_global.update(chosen["covered"])

        return selected

    def _solve_lexical(
        self, items: List[Dict[str, Any]], budget: int
    ) -> List[Dict[str, Any]]:
        """Naive lexical ranker filling budget."""
        sorted_items = sorted(items, key=lambda x: -x["utility"])
        selected = []
        cost = 0
        for it in sorted_items:
            if cost + it["cost"] <= budget and it["utility"] > 0:
                selected.append(it)
                cost += it["cost"]
        return selected

    def _solve_exact(
        self, items: List[Dict[str, Any]], budget: int
    ) -> List[Dict[str, Any]]:
        """Branch-and-bound exact 0/1 knapsack for small instance spaces."""
        n = len(items)
        best_val = [0.0]
        best_set = [[]]

        def search(idx: int, curr_cost: int, curr_val: float, chosen: List[Dict[str, Any]]):
            if curr_cost > budget:
                return
            if curr_val > best_val[0]:
                best_val[0] = curr_val
                best_set[0] = list(chosen)
            if idx >= n:
                return

            # Branch include
            it = items[idx]
            if curr_cost + it["cost"] <= budget:
                chosen.append(it)
                search(idx + 1, curr_cost + it["cost"], curr_val + it["utility"], chosen)
                chosen.pop()

            # Branch exclude
            search(idx + 1, curr_cost, curr_val, chosen)

        search(0, 0, 0.0, [])
        return best_set[0]

    @staticmethod
    def canonical_prefix_sort(capabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deterministically sorts tools into canonical lexicographic order.

        Ensures identical prefix token sequences across turns, preventing
        KV-cache invalidation in vLLM / SGLang / Anthropic engines.
        """
        def sort_key(c: Dict[str, Any]) -> Tuple[str, str, str]:
            rid = str(c.get("repository_id", ""))
            name = str(c.get("name", ""))
            # Global deterministic tie-breaker hash
            h = hashlib.sha256(f"{rid}:{name}".encode()).hexdigest()[:8]
            return (rid, name, h)

        return sorted(capabilities, key=sort_key)
