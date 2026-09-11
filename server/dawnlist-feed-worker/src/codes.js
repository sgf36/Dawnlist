/**
 * Override codes, and the admin console they gate.
 *
 * Carried over from the Wren comp-codes Worker, including the decision that
 * matters most:
 *
 *   THE TABLE DECIDES, NOT THE LICENCE.
 *
 * A licence key says what the app should show. The `licence_roles` table says
 * what this server will do. Every admin request re-reads the role rather than
 * trusting anything the caller presents, which is what makes withdrawing an
 * administrator take effect IMMEDIATELY — on a machine that is already holding
 * a perfectly valid licence it has been using all week. A role baked into a
 * credential can never be taken back before that credential expires.
 *
 * The second decision, also carried over: usage is counted from `redemptions`,
 * and there is no `uses` counter on `codes`. A counter is a second source of
 * truth that drifts the first time an increment succeeds and the matching
 * insert does not.
 */
import { PLANS, capsFor, defaultCodeExpiry, hasExpired, licenceExpiry } from './plans.js';
import { licenceEmail, localeFor, sendEmail } from './email.js';
import { parseJsonObject } from './body.js';

/** Generated codes are 19 characters; this admits hand-made ones with room. */
const MAX_CODE_CHARS = 64;

const ROLES = ['byo', 'managed', 'admin'];
const MAX_FAILED_ATTEMPTS_PER_DAY = 20;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json; charset=utf-8' },
  });

function today() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * A code a human has to read aloud down a phone.
 *
 * No 0/O/1/I/5/S: every one of those is a support ticket when someone reads a
 * code out. Grouped in fours for the same reason.
 */
const ALPHABET = 'ABCDEFGHJKLMNPQRTUVWXY2346789';

export function newCode(prefix = 'DL') {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const body = [...bytes].map((b) => ALPHABET[b % ALPHABET.length]).join('');
  return `${prefix}-${body.match(/.{1,4}/g).slice(0, 4).join('-')}`;
}

export function normalise(code) {
  // People type them with spaces, lower case, or without hyphens.
  return (code || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

/**
 * A licence key as an admin listing shows it: the last four characters.
 *
 * A licence key is the whole credential — the feed and the admin console
 * authorise on nothing else — so a listing that printed keys in full made
 * every screenshot, pasted log and shared terminal of the console a leak.
 * Four characters are enough to tell rows apart when talking to a customer.
 */
export function maskKey(key) {
  const text = String(key || '');
  return text.length <= 4 ? '…' : `…${text.slice(-4)}`;
}

/** Compare normalised, so formatting never decides whether a code works. */
function sameCode(a, b) {
  return normalise(a) === normalise(b) && normalise(a).length > 0;
}

// ---------------------------------------------------------------------------
// Rate limiting
// ---------------------------------------------------------------------------

/**
 * The salt for the unkeyed fallback below. It stops a hash table computed for
 * some other system from matching these rows, and nothing more: anybody with
 * this source can still hash candidate addresses. CLIENT_HASH_SECRET closes
 * that, and wrangler.jsonc says so.
 */
const CLIENT_HASH_SALT = 'dawnlist:code-attempts:v1:';

const hex = (buffer) =>
  [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, '0')).join('');

/**
 * What failed attempts are counted against: a hash of the connecting address,
 * never the address. The table only has to tell one connection from another
 * within a day; storing the address kept a list of who had tried codes, and
 * when, for as long as the rows lived.
 */
export async function clientHash(env, request) {
  const address = request.headers.get('cf-connecting-ip') || 'unknown';
  const encoder = new TextEncoder();
  if (env.CLIENT_HASH_SECRET) {
    const key = await crypto.subtle.importKey(
      'raw', encoder.encode(env.CLIENT_HASH_SECRET),
      { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    return hex(await crypto.subtle.sign('HMAC', key, encoder.encode(address)));
  }
  return hex(await crypto.subtle.digest('SHA-256', encoder.encode(CLIENT_HASH_SALT + address)));
}

async function tooManyFailures(env, client) {
  const row = await env.DB.prepare(
    'SELECT failures FROM code_attempts WHERE client_hash = ?1 AND day = ?2'
  ).bind(client, today()).first();
  return (row?.failures || 0) >= MAX_FAILED_ATTEMPTS_PER_DAY;
}

async function recordFailure(env, client) {
  await env.DB.prepare(
    `INSERT INTO code_attempts (client_hash, day, failures) VALUES (?1, ?2, 1)
     ON CONFLICT(client_hash, day) DO UPDATE SET failures = failures + 1`
  ).bind(client, today()).run();
}

// ---------------------------------------------------------------------------
// Redemption
// ---------------------------------------------------------------------------

export async function handleRedeem(request, env, newLicenceKey) {
  const client = await clientHash(env, request);
  if (await tooManyFailures(env, client)) {
    return json({ error: 'too_many_attempts' }, 429);
  }

  const parsed = await parseJsonObject(request);
  if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
  const body = parsed.body;

  if (body.code !== undefined && body.code !== null
      && (typeof body.code !== 'string' || body.code.length > MAX_CODE_CHARS)) {
    return json({ error: 'invalid_field',
                  message: `code must be a string of at most ${MAX_CODE_CHARS} characters` }, 400);
  }
  const given = normalise(body.code);
  if (!given) return json({ error: 'no_code' }, 400);

  // One row, through the index on the normalised form, so a code typed without
  // hyphens still works and a guess costs the same however many codes exist.
  const row = await env.DB.prepare(
    `SELECT code, role, plan, max_uses, revoked, expires_at
       FROM codes WHERE code_normalised = ?1`
  ).bind(given).first();

  // Unknown, revoked, expired and spent all answer the SAME, deliberately.
  // Each distinct answer tells somebody probing a fact about a code they do
  // not hold: that it once existed, that it was real until a date, that
  // somebody else has already used it.
  const refuse = async () => {
    await recordFailure(env, client);
    return json({ error: 'invalid_code' }, 403);
  };
  if (!row || row.revoked || hasExpired(row.expires_at)) return refuse();

  const licenceKey = newLicenceKey();
  // The caps the CODE carries. Without this a redeemed code took the Worker's
  // 700/day default, so a code meant as a short trial was indistinguishable
  // from a paid subscription in the only place that decides what a licence can
  // actually do — and a card-free trial could not be offered at all.
  const caps = capsFor(row.plan || 'standard');

  // The use limit is checked and spent in ONE transaction. It used to be a
  // count, then three inserts, so parallel redemptions of a single-use code
  // each counted zero and each got a licence. The redemption row is inserted
  // only while the count is under max_uses and the code is still unrevoked,
  // and the licence and role are inserted only if that row now exists — so a
  // refused redemption leaves nothing behind. Usage is still counted from
  // redemptions alone; there is no counter to drift from it.
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO redemptions (code, licence_key, redeemed_at)
       SELECT ?1, ?2, datetime('now')
        WHERE (SELECT COUNT(*) FROM redemptions WHERE code = ?1)
              < (SELECT max_uses FROM codes WHERE code = ?1 AND revoked = 0)`
    ).bind(row.code, licenceKey),
    env.DB.prepare(
      `INSERT INTO licences (licence_key, tier, status, plan,
                             max_postings_per_day, max_refreshes_per_day,
                             max_saved_queries, expires_at)
       SELECT ?1, ?2, 'active', ?3, ?4, ?5, ?6, ?7
        WHERE EXISTS (SELECT 1 FROM redemptions WHERE licence_key = ?1)`
    ).bind(licenceKey, row.role === 'admin' ? 'managed' : row.role,
           caps.plan, caps.max_postings_per_day, caps.max_refreshes_per_day,
           caps.max_saved_queries, licenceExpiry(caps.plan, row.role)),
    env.DB.prepare(
      `INSERT INTO licence_roles (licence_key, role, from_code)
       SELECT ?1, ?2, ?3
        WHERE EXISTS (SELECT 1 FROM redemptions WHERE licence_key = ?1)`
    ).bind(licenceKey, row.role, row.code),
  ]);
  if (results.some((r) => r.meta?.changes !== 1)) return refuse();

  return json({ ok: true, licence_key: licenceKey, role: row.role });
}

// ---------------------------------------------------------------------------
// Admin console
// ---------------------------------------------------------------------------

/**
 * Authorise an admin request by RE-READING the role from the table.
 *
 * The bearer is the licence the machine already holds — not a second
 * credential, and nothing extra to keep safe. What it proves is identity; the
 * table decides authority.
 */
async function requireAdmin(request, env) {
  const key = (request.headers.get('authorization') || '')
    .replace(/^Bearer\s+/i, '').trim();
  if (!key) return { ok: false, response: json({ error: 'no_licence' }, 401) };

  const licence = await env.DB.prepare(
    'SELECT status, expires_at FROM licences WHERE licence_key = ?1'
  ).bind(key).first();
  const role = await env.DB.prepare(
    'SELECT role FROM licence_roles WHERE licence_key = ?1'
  ).bind(key).first();

  // Withdrawn administrator and never-was-one answer identically. Telling them
  // apart tells a former administrator exactly what changed.
  if (!licence || licence.status !== 'active' || hasExpired(licence.expires_at)
      || role?.role !== 'admin') {
    return { ok: false, response: json({ error: 'not_permitted' }, 403) };
  }
  return { ok: true, licenceKey: key };
}

export async function handleAdmin(request, env) {
  const auth = await requireAdmin(request, env);
  if (!auth.ok) return auth.response;

  const url = new URL(request.url);
  const path = url.pathname;

  // --- list codes --------------------------------------------------------
  if (path === '/admin/codes' && request.method === 'GET') {
    const { results } = await env.DB.prepare(
      `SELECT c.code, c.note, c.role, c.plan, c.max_uses, c.revoked, c.created_at,
              c.expires_at,
              (SELECT COUNT(*) FROM redemptions r WHERE r.code = c.code) AS uses
         FROM codes c ORDER BY c.created_at DESC`
    ).all();
    return json({ codes: results || [] });
  }

  // --- issue a code ------------------------------------------------------
  if (path === '/admin/codes' && request.method === 'POST') {
    let body;
    try { body = await request.json(); } catch { return json({ error: 'bad_json' }, 400); }

    const role = ROLES.includes(body.role) ? body.role : 'byo';
    // Which plan the code grants. Defaults to `trial` DELIBERATELY: the usual
    // reason to mint a code is to let somebody try the product, and the safe
    // default for a credential handed to a stranger is the smallest allowance,
    // not the largest. A reviewer or comp code names its plan explicitly.
    const plan = PLANS[body.plan] ? body.plan : 'trial';
    const maxUses = Number.isInteger(body.max_uses) && body.max_uses > 0
      ? body.max_uses : 1;
    const note = (body.note || '').slice(0, 200);
    if (!note.trim()) {
      // A code with no note is a code nobody can account for later.
      return json({ error: 'note_required',
                    message: 'Say who this is for; an unlabelled code cannot be audited' }, 400);
    }

    // Stored as ISO 8601 UTC, whatever form it was given in, because the
    // redemption check compares it with the current time: a date typed as
    // "2026-12-01" or with an offset is otherwise compared as text and can
    // expire a day early or never.
    let expiresAt;
    if (body.expires_at === undefined || body.expires_at === null || body.expires_at === '') {
      expiresAt = defaultCodeExpiry(role, plan);
    } else {
      const ms = typeof body.expires_at === 'string' ? Date.parse(body.expires_at) : NaN;
      if (Number.isNaN(ms)) {
        return json({ error: 'invalid_expires_at',
                      message: 'expires_at must be a date, such as 2026-12-01 or 2026-12-01T09:00:00Z' }, 400);
      }
      expiresAt = new Date(ms).toISOString();
    }

    const code = newCode();
    await env.DB.prepare(
      `INSERT INTO codes (code, note, max_uses, revoked, created_at, expires_at, role, plan,
                          code_normalised)
       VALUES (?1, ?2, ?3, 0, datetime('now'), ?4, ?5, ?6, ?7)`
    ).bind(code, note, maxUses, expiresAt, role, plan, normalise(code)).run();

    // The plan and expiry are returned so whoever mints a code can see what
    // they just handed out. A trial code and a comp code are otherwise
    // identical to look at, and the difference between them is the entire
    // allowance.
    return json({ ok: true, code, role, plan, max_uses: maxUses, note, expires_at: expiresAt });
  }

  // --- withdraw a code ---------------------------------------------------
  if (path === '/admin/revoke' && request.method === 'POST') {
    let body;
    try { body = await request.json(); } catch { return json({ error: 'bad_json' }, 400); }
    const target = normalise(body.code);
    if (!target) return json({ error: 'no_code' }, 400);

    const row = await env.DB.prepare(
      'SELECT code FROM codes WHERE code_normalised = ?1'
    ).bind(target).first();
    if (!row) return json({ error: 'unknown_code' }, 404);

    // Revoking the code this caller is using would sign them out of the
    // console with nothing on screen to explain why.
    const self = await env.DB.prepare(
      'SELECT from_code FROM licence_roles WHERE licence_key = ?1'
    ).bind(auth.licenceKey).first();
    if (self?.from_code && sameCode(self.from_code, row.code)) {
      return json({ error: 'would_revoke_self',
                    message: 'That is the code this session is using' }, 409);
    }

    await env.DB.prepare('UPDATE codes SET revoked = 1 WHERE code = ?1')
      .bind(row.code).run();
    // Licences already redeemed from it are expired too — a withdrawn code
    // that leaves working licences behind has not been withdrawn.
    await env.DB.prepare(
      `UPDATE licences SET status = 'expired' WHERE licence_key IN
         (SELECT licence_key FROM redemptions WHERE code = ?1)`
    ).bind(row.code).run();

    return json({ ok: true, revoked: row.code });
  }

  // --- licences and usage ------------------------------------------------
  if (path === '/admin/licences' && request.method === 'GET') {
    const { results } = await env.DB.prepare(
      `SELECT l.licence_key, l.tier, l.status, l.created_at,
              r.role, r.from_code,
              (SELECT COALESCE(SUM(postings), 0) FROM usage_daily u
                WHERE u.licence_key = l.licence_key) AS postings_total
         FROM licences l
         LEFT JOIN licence_roles r ON r.licence_key = l.licence_key
        ORDER BY l.created_at DESC LIMIT 200`
    ).all();
    return json({ licences: results || [] });
  }

  // --- licences a customer paid for and may not have received -------------
  //
  // 'failed' is a delivery that ended in an error code. 'pending' is listed as
  // well because a delivery runs after the webhook has answered, and a Worker
  // stopped part-way leaves the row pending for ever with no error at all.
  // A row pending for more than a few minutes after `created_at` is that case.
  if (path === '/admin/undelivered' && request.method === 'GET') {
    const { results } = await env.DB.prepare(
      `SELECT licence_key, plan, status, created_at, paddle_subscription_id,
              delivery_status, delivery_error_code
         FROM licences
        WHERE delivery_status IN ('pending', 'failed')
        ORDER BY created_at DESC LIMIT 200`
    ).all();
    return json({
      licences: (results || []).map(({ licence_key: key, ...row }) =>
        ({ licence: maskKey(key), ...row })),
    });
  }

  // --- do the credentials actually WORK ----------------------------------
  //
  // `wrangler secret list` proves a value was stored under a name. It proves
  // nothing about whether the value is right, and the sibling project lost
  // four separate afternoons to exactly that gap — a secret whose NAME was the
  // credential, a secret from the wrong destination, a secret that was simply
  // stale. Every one of them listed perfectly.
  //
  // So this asks each upstream a cheap read-only question and reports what came
  // back. It sends nothing and spends nothing.
  if (path === '/admin/selftest' && request.method === 'GET') {
    const out = {};

    if (!env.RESEND_API_KEY) {
      out.resend = { ok: false, reason: 'not set' };
    } else {
      const r = await fetch('https://api.resend.com/domains', {
        headers: { authorization: `Bearer ${env.RESEND_API_KEY}` },
      });
      // 401 is a wrong key; 200 is a working one. Anything else is reported
      // as itself rather than collapsed into "failed".
      out.resend = { ok: r.ok, status: r.status };
    }

    if (!env.PADDLE_API_KEY) {
      out.paddle = { ok: false, reason: 'not set' };
    } else {
      const base = env.PADDLE_API_BASE || 'https://api.paddle.com';
      const r = await fetch(`${base}/customers?per_page=1`, {
        headers: { authorization: `Bearer ${env.PADDLE_API_KEY}` },
      });
      out.paddle = { ok: r.ok, status: r.status };
    }

    out.theirstack = { ok: Boolean(env.THEIRSTACK_API_KEY), checked: false,
                       note: 'not called — a feed request costs a credit' };
    out.paddle_webhook_secret = { ok: Boolean(env.PADDLE_WEBHOOK_SECRET),
                                  checked: false,
                                  note: 'only a signed delivery can prove it' };
    out.price_id = env.PADDLE_PRICE_STANDARD || null;

    const ready = out.resend.ok && out.paddle.ok;
    return json({ ok: ready, checks: out });
  }

  // --- send somebody their licence key again -----------------------------
  //
  // A support tool first and a test second. The commonest thing a paying
  // customer will ever ask for is the key they lost, and without this the only
  // answer is to read it out of the database by hand and paste it into a mail
  // client — which is slower, less consistent, and puts the key through a
  // second system.
  if (path === '/admin/resend' && request.method === 'POST') {
    let body;
    try { body = await request.json(); } catch { return json({ error: 'bad_json' }, 400); }

    const target = (body.licence_key || '').trim();
    const to = (body.to || '').trim();
    if (!target || !to) {
      return json({ error: 'need_licence_and_address',
                    message: 'Both licence_key and to are required' }, 400);
    }

    const row = await env.DB.prepare(
      `SELECT licence_key, status, plan, max_postings_per_day
         FROM licences WHERE licence_key = ?1`
    ).bind(target).first();
    if (!row) return json({ error: 'unknown_licence' }, 404);
    if (row.status !== 'active') {
      // Re-sending a revoked key would have somebody paste it in and be
      // refused, with no way to tell that from the key being wrong.
      return json({ error: 'licence_inactive',
                    message: `That licence is ${row.status}` }, 409);
    }

    const mail = licenceEmail({
      licenceKey: row.licence_key,
      postingsPerDay: row.max_postings_per_day,
      lang: localeFor(body.lang),
    });
    const sent = await sendEmail(env, { to, ...mail });
    if (!sent.ok) {
      // Resend's own words go back to the administrator who asked, because
      // they are what says how to fix it. They are not logged.
      return json({ error: 'send_failed', code: sent.error,
                    detail: sent.detail ?? sent.error, status: sent.status }, 502);
    }
    // Recorded as delivered, so a licence resent by hand leaves
    // /admin/undelivered instead of being chased a second time.
    await env.DB.prepare(
      `UPDATE licences SET delivery_status = 'sent', delivery_error_code = NULL
        WHERE licence_key = ?1`
    ).bind(row.licence_key).run();

    // The address is not logged or echoed. The id is enough to find it in
    // Resend if somebody says it never arrived.
    return json({ ok: true, id: sent.id, lang: mail.lang });
  }

  return json({ error: 'not_found' }, 404);
}
