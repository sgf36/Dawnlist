/**
 * Signing and posting Paddle events the way Paddle does, for tests.
 *
 * `sign` computes the real HMAC over `ts:rawBody`, so a test that passes has
 * gone through the Worker's actual verification rather than around it.
 */
import { handlePaddleWebhook } from '../src/paddle.js';

export const SECRET = 'pdl_ntfset_01test_secretvalue_for_local_checks_only';

export async function hmac(body, secret = SECRET, ts = Math.floor(Date.now() / 1000)) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`${ts}:${body}`));
  return [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function sign(body, secret = SECRET, ts = Math.floor(Date.now() / 1000)) {
  return `ts=${ts};h1=${await hmac(body, secret, ts)}`;
}

/** Posts an event, signed, and returns `{ status, out }`. */
export async function deliver(env, event, { ctx } = {}) {
  const body = JSON.stringify(event);
  const res = await handlePaddleWebhook(new Request('https://w/paddle/webhook', {
    method: 'POST', body, headers: { 'Paddle-Signature': await sign(body) },
  }), { PADDLE_WEBHOOK_SECRET: SECRET, ...env }, ctx);
  return { status: res.status, out: await res.json() };
}
