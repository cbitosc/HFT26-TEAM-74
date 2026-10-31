# ProofStack technical notes

See [README](README.md) for setup, credits and the judge walkthrough. Historical upstream documents describe SignalLoop; they are not verified feature claims for this fork.

## Assessment

Three fixed tiers share one server-enforced 60-minute deadline. Each has a project question plus a DSA question restricted to the declared supported topics. Featherless personalizes tasks within server-owned contracts. Reference/starter checks gate release; invalid or unavailable AI output falls back to labeled validated templates. Generation has bounded concurrency, persistent reuse and limited restart recovery.

## Score and evidence

Correctness contributes up to 80 points; deterministic AI-use efficiency contributes up to 20. Novice/Adept/Master correctness weights are 20/35/45 percent. Only checks failed by the starter can earn credit, and implementation changes are required separately for each question. Comments and no-op edits cannot earn starter credit. The accepted page-hidden rule subtracts 40 once, with a floor of zero. Reports show earned points and deductions separately. Browser visibility does not prove cheating.

Claim evidence identifies tested skills and untested claims. Replay shows saved checkpoints, not every keystroke. Final tests and reference solutions are excluded from assistant context. No LLM independently grades correctness.

## Execution and hosting

Local: web 3001, API 8001, worker 9001, PostgreSQL 55432. Docker isolates candidate Python 3.11 execution. Hosted: Next, private API and WASI runner share one container. Python WASI has no network or host environment, read-only workspace and bounded memory/fuel/time. Hidden expected values stay outside candidate execution.

Hosted source uses deploy/render/Dockerfile. scripts/package_render.py creates the compatible archive-based deployment bundle. Do not apply the upstream render.yaml: it is unrelated to this free deployment.

The public demo uses Render Free and an expiring free PostgreSQL database (November 30, 2026). Availability and credits are finite. No concurrent-user reliability guarantee. A fresh generation was previously measured at 56 seconds; warm cache reuse at 2.3 seconds. Cold starts add time.

## AI use

Generation, PDF extraction and guarded hints use Featherless. Polling, editing, saving, test execution and scoring do not. Persistent daily request/allowance caps reduce usage; they do not read your provider balance or guarantee a monetary budget. Set keys only in private environment settings.

## Verification scope

Focused portfolio/provider/PDF/hardening tests and production frontend build have passed. Prior real WASI checks verified reference solutions, zero credit for no-op changes, and 100 earned points reduced to 60 after the visibility event. These checks are not an independent security audit. See docs/enhancements/proofstack.md for the implementation record.
