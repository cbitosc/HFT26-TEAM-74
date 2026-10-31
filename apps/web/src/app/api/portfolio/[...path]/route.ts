import {NextRequest, NextResponse} from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
const root = process.env.PROOFSTACK_API_INTERNAL_URL || 'http://127.0.0.1:8001';

// Expose only the candidate API. Worker, database and legacy admin routes stay private.
async function forward(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  const route = path.join('/');
  const token = '[A-Za-z0-9_-]{20,100}';
  const allowed = request.method === 'GET'
    ? new RegExp(`^sessions/${token}$`).test(route)
    : request.method === 'PUT'
      ? new RegExp(`^sessions/${token}/tasks/[0-2]/code$`).test(route)
      : request.method === 'POST' && (route === 'sessions' || route === 'import-pdf' || new RegExp(`^sessions/${token}/(start|finish|retry|page-hidden|focus/[0-2]|tasks/[0-2]/(run|hint))$`).test(route));
  if (!allowed) return NextResponse.json({detail: 'Not found'}, {status: 404});
  if (request.method !== 'GET') {
    const origin = request.headers.get('origin');
    const host = request.headers.get('x-forwarded-host') || request.headers.get('host');
    if (origin && new URL(origin).host !== host) return NextResponse.json({detail:'Origin not allowed'}, {status:403});
  }
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (request.body) {
    const reader = request.body.getReader();
    while (true) {
      const {done,value} = await reader.read();
      if (done) break;
      size += value.length;
      if (size > (route === 'import-pdf' ? 5_000_000 : 250_000)) { await reader.cancel(); return NextResponse.json({detail:'Request too large'}, {status:413}); }
      chunks.push(value);
    }
  }
  try {
    const response = await fetch(`${root}/portfolio/${route}`, {
      method:request.method, headers:{'X-Proofstack-Client':(request.headers.get('x-forwarded-for')||'local').split(',').at(-1)!.trim(),'Content-Type':route === 'import-pdf' ? 'application/pdf' : 'application/json'},
      ...(size ? {body:Buffer.concat(chunks)} : {}), cache:'no-store', signal:AbortSignal.timeout(120_000),
    });
    return new NextResponse(await response.text(), {status:response.status, headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  } catch {
    return NextResponse.json({detail:'The assessment service is temporarily unavailable. Please retry.'}, {status:503});
  }
}
export const GET=forward;
export const POST=forward;
export const PUT=forward;
