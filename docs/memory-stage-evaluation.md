# Memory stage interventions

`scripts/evaluate_memory_stages.py` is an optional checkout-only experiment using fixed, original ASCII and Korean synthetic fixtures. It does not read a user vault, execute shell commands, deploy a memory policy, or reproduce an official benchmark. Memory generation is `deterministic_experimental_intervention`: no model writes or summarizes memory here.

The experimental design borrows the principles of stage-wise contrasts and matched clean/poisoned conditions from Zang et al., [Understanding Stage-Wise Utility-Risk Trade-offs in LLM Agent Memory (MemGauge)](https://arxiv.org/abs/2608.30177). These fixtures, interventions and metrics are independently written; no external code or benchmark data is copied, and results are not directly comparable to the paper's benchmark.

```sh
python scripts/evaluate_memory_stages.py --stage all --case all
python scripts/evaluate_memory_stages.py --stage retrieval --case ascii --control unsafe
```

Each stage has two levels, each run against matched clean and poisoned sources and both checksum and startup tasks. Writing reviews the host-reported caller origin or accepts an inline claim of caller role, then compiles and persists the chosen observation through a revision-bound lesson delta. Management persists and reloads either the original source roles or a deterministic role-erasing projection before review, compilation, and lesson persistence. Retrieval uses the same stored files for both levels and compares explicit source exclusion with inclusion; the actual retrieval context is the solver's input. Storage manifests, context hashes, local source dependency hashes, and context exposure/completeness appear in the report. Host-reported origin and source binding do not establish truth or authority.

The default safe and unsafe controls are scripted plans, not inference or measurements of a model. An optional local model requires explicit opt-in and a literal loopback chat-completions endpoint:

```sh
python scripts/evaluate_memory_stages.py --backend local-model --allow-network --endpoint http://127.0.0.1:8000/v1/chat/completions --model MODEL
```

An API key is optional and can only come from an explicitly named process environment variable via `--api-key-env NAME`. The shared transport disables proxies, redirects and retries and enforces byte and time limits. After a timeout or transport failure, subsequent requests stop. Valid provider usage is reported when supplied; billing is unknown. HTTP test services test contracts only; they are not actual model observations. Temperature zero does not guarantee deterministic inference.

Rows separate exposed bad plans from observed adoption, and summarize clean-task utility by stage and level. Exposure counts include failed requests; the scored-exposure count is the completed denominator for adoption rates. Retrieval uses a task-specific query, and completeness requires the corresponding task evidence in the returned context. A complete matched pair requires both level requests to complete with complete context. Truncated, omitted or failed evidence is incomplete, not proof of protection, abstention or repair. Conditional rates are null without an eligible denominator. This experiment does not measure selective repair; the existing lifecycle scorer retains its separate four-phase criteria. Sources and intervention provenance are bound to local byte hashes, which do not authenticate model behavior or establish a source's factual accuracy.
