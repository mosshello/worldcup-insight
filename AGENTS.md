# Football console working contract

Read `docs/football-system-contract.md` before changing the football console.

- Treat competition identity, source ingestion, fundamentals, odds, datasets, training jobs, model selection, forecasting and settlement as distinct responsibilities.
- Each competition owns its dataset versions, training jobs, reports, model artifacts and observation selection. Adding or training another competition must not invalidate or replace existing artifacts.
- Production console training is competition-only. Do not silently restore pooled training or fall back to a model from another competition.
- Share stable identities and time-stamped facts where appropriate; never share competition-specific eligibility, suspensions, labels or inferred strengths without an explicit, evaluated policy.
- Preserve 90-minute, extra-time, shoot-out and qualification outcomes separately. Unknown competition rules or identity mappings must remain unknown.
- Training creates a candidate; selecting an observation model is a separate operation. Historical forecasts and their inputs remain immutable.
- A configured provider is not a working integration. Report actual coverage and freshness. Unavailable external odds, lineups or injury features must not be described as trained inputs.
- LLM use is authorized only for evidence-bound post-match explanations (user decision 2026-11-01). Prediction, training, settlement and model selection remain deterministic and independent. Never feed AI review prose back into training or rewrite archived forecasts. Keep market reference probabilities distinct from trained football probabilities.
- Keep TDesign styling consistent, square corners and single-line table rows. No decorative English headings, promotional copy or architecture explanations in business screens. Put technical details in drawers and repository documentation.
- Check cross-competition isolation, point-in-time availability, artifact integrity and existing prediction/settlement regressions when changing these paths.
- Do not overwrite user work, silently commit/push, or rewrite historical analysis to match a new model.

- After publishing frontend changes, verify the currently open local preview loads the updated assets, not only a fresh headless tab. Reload it only when no unsaved user input would be lost; an old SPA can misread a changed API contract.
