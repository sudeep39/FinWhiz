# Evidence-validation repair fix

Trigger: Tesla energy generation/storage gross margin question for Q2 2026. Local logs showed successful retrieval, generation and repair followed by a validation AppError and HTTP 502. Original generated text was not logged, so the exact failed model value cannot be reconstructed from those logs.

## Changes

- Repair receives the previous answer plus specific unsupported numeric values and citation IDs, rather than only a generic error.
- Narrative validation accepts exact million/billion conversions when evidence explicitly states the scale; it still rejects unsupported rounding and derived percentage-point differences. This is a numeric check, not full metric/period/unit entailment.
- Chart values and ratio/percent operation mistakes enter the bounded repair path. The repair schema does not regenerate charts, so repaired answers omit charts instead of retaining invalid originals.
- A second failed validation, or invalid repair citations, returns `insufficient_evidence` with no unsupported calculations, rather than HTTP 502 asking the user to retry.
- Telemetry records validation issue counts and repair/fallback outcomes without recording prompts or generated answers.
- Prompts emphasize disclosed causal explanations, original units and literal reported margins.

## Verification

Offline tests cover the supplied Tesla question using representative disclosed passages, exact scale conversion versus rounding, invalid charts/citations, and a single bounded repair followed by a safe evidence limitation. No paid provider call was made and no deployment was performed.
