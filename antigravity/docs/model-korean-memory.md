# Fresh Korean structured-answer observations

`scripts/evaluate_model_korean_memory.py` evaluates the repository's original,
invented Korean fixture. Each of its 50 cases has separately retrieved default
and annotate-only arms and receives a separate response: 100 requests in a full
local-model run, even where the evidence is identical. Identical evidence does
not establish an annotation effect. This is neither an official benchmark nor an
Inspect SDK integration; no external data is copied and no real vault is read.

The default is an offline scripted control:

```sh
python scripts/evaluate_model_korean_memory.py
python scripts/evaluate_model_korean_memory.py --control wrong
```

To opt into a local OpenAI-compatible server, specify a literal loopback endpoint,
explicit model and `--allow-network`:

```sh
python scripts/evaluate_model_korean_memory.py --backend local-model --allow-network --endpoint http://127.0.0.1:8080/v1/chat/completions --model MODEL
```

`--api-key-env NAME` optionally reads only that process environment variable.
No server discovery, proxies, redirects or retries occur. Time, request and
response bytes are bounded. A timeout or transport failure stops later requests;
errors never count as abstentions. A sent count records an attempted bounded
dispatch; it does not prove server acceptance or inference. Provider usage is retained when present even
for invalid responses, and billing remains unknown. A Korean system instruction
requires strict scoped JSON; only question, options and retrieved evidence enter
the user message. Synthetic labels remain on the host.

Output contains a versioned receipt. Save the receipt object as JSON and pass it
with `--replay FILE` to regrade without network access. Replay binds the original
pack and labels, eligible corpus and policy, current local source closure, actual
ordered samples and evidence, model, limits, prompt, requests and all outcome
rows. Checksums detect changes but do not authenticate the origin or prove that
inference took place. Usage is caller-attested during replay. Replay rejects
claims that exceed the declared request, response or output budgets, and enforces
that no requests follow a timeout or transport failure. If the overall time limit
is shorter than one full request timeout, replay permits only unsent run-timeout
rows. It cannot verify unrecorded elapsed time. Its response-byte check
uses the smallest compact envelope for the retained structured answer; the raw
provider response is not retained, so replay cannot prove its exact byte length. Malformed responses,
incomplete evidence and missing usage are not success, certified abstention or
zero-cost inference. Temperature zero does not establish deterministic output.
The existing `evaluate_korean_memory.py --render-matched` remains evidence-only.

The fixture and approach are controlled original adaptations inspired by research
on memory evaluation, including MemGauge and NoMIRACL, and Inspect's separation
of input and scoring. They do not reproduce those benchmarks.
