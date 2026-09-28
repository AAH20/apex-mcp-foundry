# Evaluation plan

Foundry is a prototype. No benchmark advantage, production readiness, or performance target is asserted until a reproducible experiment supports it.

## Baselines

1. Static exposure: every approved tool schema is sent to the client on every request.
2. Lexical progressive discovery: current capability search selects among approved capability descriptions.
3. Random top-k: a sanity baseline for discovery quality.

When a retrieval model is added, report it separately and pin model/provider/version, prompts, data, and cost.

## Measures

| Area | Measure | Required reporting |
|---|---|---|
| Discovery | Recall@k, MRR, wrong-tool selection rate | Query set, capability labels, repo mix, confidence interval |
| Context | Input schema token count and total prompt tokens | Exact tokenizer/model and full serialized request |
| Cache | Prefix-cache hit tokens and TTFT | Provider/runtime, exact prompt bytes, warm/cold runs |
| Correctness | Task success and contract-valid argument rate | Ground-truth expected behavior, adjudication rules |
| Safety | Unauthorized call acceptance, data exposure, unapproved handler reachability | Negative test inventory; target zero accepted unauthorized calls |
| Performance | p50/p95 discovery and call latency, memory, CPU | Hardware, file/symbol limits, cache state, repetitions |
| Economics | Fully allocated cost per successful task | Model tokens, compute, database, egress, observability, support assumptions |

## NP-hard scope

Potential formulations include budgeted capability selection (tool value under context/cost budgets), generalized assignment (tasks to heterogeneous execution slots), and precedence-constrained scheduling (dependent tasks on limited workers). Actual objective functions and constraints must be written down per workload. Static capability search is not itself proof of solving these formulations. Use exact enumeration/optimization on small instances as a reference, then compare heuristics on larger instances for feasibility, objective gap, runtime, and memory.

Cycle detection on a fixed directed dependency graph is polynomial-time. Do not label it NP-hard. Authentication and permission checks are hard gates and are not optimization objectives.

## Benchmark release requirements

- Publish datasets/fixtures or a lawful synthetic generator, with versions and licenses.
- Pin repository snapshots and capability manifests by digest.
- Include all failures, invalid outputs, and negative authorization tests.
- Do not claim percentage wins from token counts alone; report task success at matched quality.
- Separate local stdio measurements from remote transport and provider/model results.
