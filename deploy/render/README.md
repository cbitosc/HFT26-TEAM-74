# No-card free deployment (not deployed yet)

Use a **Free Web Service** on Render, Dockerfile `deploy/render/Dockerfile`, repository root context.
This runs Next, FastAPI and a Wasmtime sandbox in one service; only Next's port is public.
Keep the original `render.yaml` out of this flow: its upstream plans are paid.

Required runtime settings, entered privately in Render:
- `DATABASE_URL`: a Neon **Free** PostgreSQL connection string with SSL.
- `FEATHERLESS_API_KEY`: the existing hackathon key, never committed.

Python is 3.15 in WASI for this deployment, 3.11 in the local Docker runner.
Expected answers are never mounted into the guest. Only its own code/arguments and the
Python distribution are readable. No host environment or network capabilities are granted.
Memory, fuel, output size and wall-clock execution are bounded; executions are serialized.

Limits: Render sleeps after 15 minutes idle, wakes on requests, and provides 750 free hours/month.
No payment method means service suspension rather than overage charges. Neon Free has usage
limits. Featherless credits can expire/run out independently. Generation is an in-process job;
restarts during generation can interrupt it. This remains a small hackathon demo.

Status: native sandbox tests passed across all nine topic/tier pairs plus isolation probes.
Linux image/resource validation and cloud publication still required. This is not a deployment URL.


## Public deployment verified — 2026-10-31

URL: https://proofstack-zuyt.onrender.com
Private deployment repository commit db03cd3c519d7624046b1d871903732e840639a4 is live on Render Free.
Verified in the public browser: homepage, portfolio sample submission, Featherless-personalized
three-tier generation and isolated reference checks (10/10, 10/10, 13/13), start, Monaco, public
execution (untouched starter 0/2), review/submit and final report (untouched code 0/100 including
AI efficiency 0/20). This verification uses hosted services, not laptop services.
Initial CPU feature mismatch fixed with explicit baseline Linux target for WASI artifacts.
One transient failed-fetch banner occurred during generation; subsequent polling completed and
start/run/finish succeeded. Concurrent-user load and long-term reliability are not verified.
Free database expires November 30, 2026; migrate before then. Free web service sleeps when idle.
Featherless remains subject to existing hackathon credits. No paid plan or card was used.
Future source bundles can be rebuilt using scripts/package_render.py and uploaded to the private
repo; Render auto-deploys main. API keys remain in Render environment only.
