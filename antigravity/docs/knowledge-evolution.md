# Offline knowledge evolution evaluation

Run `python scripts/evaluate_knowledge_evolution.py` or pass an explicit JSON fixture path. The evaluator uses only the standard library and constructs a temporary synthetic vault. It invokes the actual inventory capture/check and selected-note export APIs, checks independently authored expected transitions, and verifies exported hashes and a selected-link invariant. The destination and fixture contents cannot select arbitrary replay paths.

Claims and source fact maps are manually structured. A current matching fact is supported; a current contradiction with historical support is stale; a current contradiction without historical support is invented; an absent current key is unverified. A changed hash or missing source alone is not a truth judgment. Each checkpoint and the pooled total count every authored claim; supported rates are null when the denominator is zero. Exit codes are 0 when all expectations match, 1 for a mismatch, and 2 for malformed or unavailable input.

This small fixture is inspired by longitudinal evaluation such as LEDGER; it does not reproduce that benchmark, infer facts from natural language, establish evaluator completeness, or measure coverage beyond its authored topics and claims. Results should not be generalized to a production vault or treated as authorization to share its contents.
