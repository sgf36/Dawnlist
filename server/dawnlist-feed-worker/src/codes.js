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
import { compEligible } from './apple.js';

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

/** A JSON error body's `name`, or null when there is no readable one. */
async function errorNameOf(response) {
  try {
    const name = JSON.parse(await response.text())?.name;
    return typeof name === 'string' ? name : null;
  } catch {
    return null;
  }
}

/**
 * What each admin route is recorded as. A request to anything not listed is
 * refused before it is recorded or counted.
 */
const ADMIN_ACTIONS = {
  'GET /admin/codes': 'codes.list',
  'POST /admin/codes': 'codes.mint',
  'POST /admin/revoke': 'codes.revoke',
  'GET /admin/licences': 'licences.list',
  'GET /admin/undelivered': 'undelivered.list',
  'GET /admin/selftest': 'selftest',
  'POST /admin/resend': 'licence.resend',
  'POST /admin/apple-codes': 'apple.upload',
  'GET /admin/apple-codes': 'apple.list',
  'POST /admin/apple-codes/assign': 'apple.assign',
  'POST /admin/apple-codes/void': 'apple.void',
  'POST /admin/apple-comp': 'apple.comp',
};

/**
 * Apple allows twenty-five thousand one-time codes per offer, but a single
 * request body is not the place to move them: a Worker has a memory ceiling
 * and D1 a statement ceiling, and an upload of that size failing half way
 * through leaves a batch nobody can account for. The minting tool sends
 * chunks, and re-sending a chunk is harmless by design.
 */
const MAX_OFFER_CODES_PER_UPLOAD = 500;

/**
 * Apple's one-time offer codes are alphanumeric, and nothing else is one.
 *
 * Checked rather than trusted because these arrive from a CSV Apple generates:
 * a header row, a stray quote or a trailing blank line all look like strings
 * and none of them is a code. Stored unchecked, they become codes somebody is
 * handed that cannot work, with nothing to distinguish that from a bug.
 */
function isOfferCode(value) {
  if (typeof value !== 'string') return false;
  const code = value.trim().toUpperCase();
  if (!code || code.length > 40) return false;
  for (const ch of code) {
    const isLetter = ch >= 'A' && ch <= 'Z';
    const isDigit = ch >= '0' && ch <= '9';
    if (!isLetter && !isDigit) return false;
  }
  return true;
}

/**
 * Requests one administrator may make in ten minutes. The console makes a few
 * per screen, so this sits far above real use; what it bounds is a leaked
 * administrator licence minting codes in a loop or paging through every
 * licence before anybody notices.
 */
const ADMIN_REQUESTS_PER_WINDOW = 60;
const ADMIN_WINDOW = '-10 minutes';

/** Who made an admin request, as the audit stores it: a hash, never the key. */
async function actorHash(licenceKey) {
  return hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(licenceKey)));
}

async function adminBusy(env, actor) {
  const row = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM admin_audit
      WHERE actor_hash = ?1 AND at >= datetime('now', ?2)`
  ).bind(actor, ADMIN_WINDOW).first();
  return (row?.n || 0) >= ADMIN_REQUESTS_PER_WINDOW;
}

/**
 * Written AFTER the request is handled, because the target of a mint does not
 * exist until then. A failed write is logged and not turned into an error: the
 * action has already been committed, and a failed response would have the
 * administrator retry it — minting a second code.
 */
async function recordAudit(env, action, actor, target) {
  try {
    await env.DB.prepare(
      'INSERT INTO admin_audit (action, actor_hash, target) VALUES (?1, ?2, ?3)'
    ).bind(action, actor, target ?? null).run();
  } catch (err) {
    console.error('admin: audit write failed', { action, error: err?.name });
  }
}

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
  // Guessing at the console's credential is capped like guessing at a code, on
  // the same counter: both are somebody trying keys they were not given.
  const client = await clientHash(env, request);
  if (await tooManyFailures(env, client)) {
    return json({ error: 'too_many_attempts' }, 429);
  }
  const auth = await requireAdmin(request, env);
  if (!auth.ok) {
    await recordFailure(env, client);
    return auth.response;
  }

  const path = new URL(request.url).pathname;
  const action = ADMIN_ACTIONS[`${request.method} ${path}`];
  if (!action) return json({ error: 'not_found' }, 404);

  const actor = await actorHash(auth.licenceKey);
  if (await adminBusy(env, actor)) {
    return json({ error: 'too_many_requests',
                  message: `More than ${ADMIN_REQUESTS_PER_WINDOW} admin requests in ten minutes; `
                    + 'wait a few minutes and try again' }, 429);
  }

  const audit = { target: null };
  try {
    return await routeAdmin(request, env, auth, path, audit);
  } finally {
    await recordAudit(env, action, actor, audit.target);
  }
}

async function routeAdmin(request, env, auth, path, audit) {

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
    const parsed = await parseJsonObject(request);
    if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
    const body = parsed.body;

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
    audit.target = maskKey(code);
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
    const parsed = await parseJsonObject(request);
    if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
    const body = parsed.body;
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

    audit.target = maskKey(row.code);
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
    return json({
      licences: (results || []).map(({ licence_key: key, ...row }) =>
        ({ licence: maskKey(key), ...row })),
    });
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

  // --- Mac offer codes: upload a minted batch -----------------------------
  //
  // The codes arrive ALREADY MINTED, from `tools/asc_offer_codes.py` on a
  // machine holding the App Store Connect key. Nothing in this Worker talks to
  // Apple, and migration 011 says why that is not a stylistic choice: an
  // individual App Store Connect key carries App Manager rights over the whole
  // app, and Apple has no finer grain to give it.
  if (path === '/admin/apple-codes' && request.method === 'POST') {
    const parsed = await parseJsonObject(request);
    if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
    const body = parsed.body;

    const batch = String(body.batch || '').trim().slice(0, 60);
    if (!batch) {
      return json({ error: 'batch_required',
                    message: 'Name the batch; codes nobody can group are codes nobody can account for' }, 400);
    }
    const codes = Array.isArray(body.codes) ? body.codes : null;
    if (!codes || codes.length === 0) return json({ error: 'no_codes' }, 400);
    if (codes.length > MAX_OFFER_CODES_PER_UPLOAD) {
      return json({ error: 'too_many_codes',
                    message: `Send at most ${MAX_OFFER_CODES_PER_UPLOAD} codes per request` }, 400);
    }

    // Validated BEFORE anything is written. A stray CSV header or a quoting
    // accident stored as a code is a code somebody is later handed that cannot
    // possibly work, and they have no way to tell that from a bug in the app.
    const clean = [];
    for (const raw of codes) {
      if (!isOfferCode(raw)) {
        return json({ error: 'bad_code',
                      message: `Not an offer code: ${String(raw).slice(0, 40)}` }, 400);
      }
      clean.push(String(raw).trim().toUpperCase());
    }

    await env.DB.prepare(
      `INSERT INTO apple_offer_batches (batch, offer_id) VALUES (?1, ?2)
       ON CONFLICT(batch) DO NOTHING`
    ).bind(batch, body.offer_id ? String(body.offer_id).slice(0, 120) : null).run();

    // DO NOTHING on conflict, so re-sending a chunk after a half-finished
    // upload adds what is missing instead of failing on the first duplicate
    // and leaving the operator to work out how far it got.
    const insert = env.DB.prepare(
      `INSERT INTO apple_offer_codes (code, batch) VALUES (?1, ?2)
       ON CONFLICT(code) DO NOTHING`);
    await env.DB.batch(clean.map((code) => insert.bind(code, batch)));

    const row = await env.DB.prepare(
      'SELECT COUNT(*) AS n FROM apple_offer_codes WHERE batch = ?1').bind(batch).first();
    audit.target = batch;
    return json({ ok: true, batch, sent: clean.length, in_batch: row?.n || 0 });
  }

  // --- Mac offer codes: what is there, and where it went ------------------
  if (path === '/admin/apple-codes' && request.method === 'GET') {
    const state = new URL(request.url).searchParams.get('state') || 'all';
    const where = state === 'free' ? 'WHERE void = 0 AND assigned_at IS NULL'
      : state === 'assigned' ? 'WHERE void = 0 AND assigned_at IS NOT NULL'
      : state === 'void' ? 'WHERE void = 1'
      : '';
    const { results } = await env.DB.prepare(
      `SELECT code, batch, added_at, assigned_to, assigned_at, note, void
         FROM apple_offer_codes ${where}
        ORDER BY added_at DESC, code LIMIT 500`).all();
    const { results: batches } = await env.DB.prepare(
      `SELECT b.batch, b.offer_id, b.created_at, b.redemptions,
              COUNT(c.code) AS total,
              SUM(CASE WHEN c.assigned_at IS NOT NULL THEN 1 ELSE 0 END) AS assigned,
              SUM(CASE WHEN c.void = 1 THEN 1 ELSE 0 END) AS voided
         FROM apple_offer_batches b
         LEFT JOIN apple_offer_codes c ON c.batch = b.batch
        GROUP BY b.batch
        ORDER BY b.created_at DESC`).all();
    return json({ codes: results || [], batches: batches || [] });
  }

  // --- Mac offer codes: hand one out --------------------------------------
  //
  // ONE STATEMENT, and that is the whole point. Reading a free code and then
  // marking it in a second statement is the shape that hands the same string
  // to two people when two console windows are open: both read the same row
  // before either writes. The UPDATE's sub-select runs under SQLite's write
  // lock, so exactly one of them changes a row and the other is told none_free.
  if (path === '/admin/apple-codes/assign' && request.method === 'POST') {
    const parsed = await parseJsonObject(request);
    if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
    const body = parsed.body;

    const who = String(body.assigned_to || '').trim().slice(0, 120);
    if (!who) {
      return json({ error: 'assigned_to_required',
                    message: 'Say who this is going to; an unlabelled code cannot be audited' }, 400);
    }
    const note = String(body.note || '').slice(0, 200);
    const batch = String(body.batch || '').trim();

    const { results } = await env.DB.prepare(
      `UPDATE apple_offer_codes
          SET assigned_to = ?1, assigned_at = datetime('now'), note = ?2
        WHERE code = (SELECT code FROM apple_offer_codes
                       WHERE void = 0 AND assigned_at IS NULL
                         AND (?3 = '' OR batch = ?3)
                       ORDER BY added_at, code LIMIT 1)
          AND assigned_at IS NULL
        RETURNING code, batch, assigned_to, assigned_at, note`
    ).bind(who, note, batch).all();

    const row = (results || [])[0];
    if (!row) {
      return json({ error: 'none_free',
                    message: 'No unassigned code left; mint another batch and upload it' }, 409);
    }
    audit.target = row.code;
    return json({ ok: true, ...row });
  }

  // --- Mac offer codes: take one out of circulation -----------------------
  //
  // THIS DOES NOT REVOKE THE CODE AT APPLE, and cannot: Apple has no API to
  // withdraw a one-time code once it is minted. It marks the code as not to be
  // handed out from here. A code already given to somebody still works, and the
  // only real control over that is the expiry set when the offer was created.
  // The response says so, rather than letting the console imply otherwise.
  if (path === '/admin/apple-codes/void' && request.method === 'POST') {
    const parsed = await parseJsonObject(request);
    if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
    const code = String(parsed.body.code || '').trim().toUpperCase();
    if (!code) return json({ error: 'no_code' }, 400);

    const { results } = await env.DB.prepare(
      `UPDATE apple_offer_codes SET void = 1 WHERE code = ?1 RETURNING code, batch`
    ).bind(code).all();
    const row = (results || [])[0];
    if (!row) return json({ error: 'unknown_code' }, 404);
    audit.target = row.code;
    return json({ ok: true, code: row.code, batch: row.batch,
                  still_redeemable_at_apple: true });
  }

  // --- comp a Mac user who never paid, and only such a user ---------------
  //
  // TWO GATES, AND THIS ROUTE IS ONLY THE SECOND ONE. `compEligible` reads
  // `offer_identifier` off the row, which was written from the transaction
  // APPLE SIGNED — so a subscription somebody bought carries no comp offer and
  // cannot be comped here however this request is phrased. That is what makes
  // it impossible to hand free access to a lapsed paying customer by mistake:
  // the evidence is absent, not merely the intention.
  if (path === '/admin/apple-comp' && request.method === 'POST') {
    const parsed = await parseJsonObject(request);
    if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
    const body = parsed.body;

    const id = String(body.original_transaction_id || '').trim();
    if (!id) return json({ error: 'no_transaction' }, 400);
    const comp = body.comp === true || body.comp === 1;

    const row = await env.DB.prepare(
      `SELECT original_transaction_id, licence_key, offer_identifier, comp
         FROM apple_transactions WHERE original_transaction_id = ?1`
    ).bind(id).first();
    if (!row) return json({ error: 'unknown_transaction' }, 404);

    if (comp && !compEligible(env, row)) {
      // Named, so the refusal is actionable. "Not eligible" sends somebody
      // looking for a bug; naming the offer the subscription actually began
      // with tells them immediately that this is a customer, not a guest.
      return json({
        error: 'not_comp_eligible',
        message: 'This subscription did not begin with a comp offer, so it '
          + 'cannot be comped. Comp offers: '
          + (String(env.APPLE_COMP_OFFERS || '') || '(none configured)'),
        began_with: row.offer_identifier || null,
      }, 409);
    }

    await env.DB.prepare(
      'UPDATE apple_transactions SET comp = ?1 WHERE original_transaction_id = ?2'
    ).bind(comp ? 1 : 0, id).run();

    // Switching comp OFF does not itself end access: Apple may still be saying
    // the subscription is live, and a period they were genuinely granted is
    // not ours to cancel. What it ends is the EXTENSION past that period. To
    // cut somebody off now, revoke the licence — which an Apple check can no
    // longer undo.
    audit.target = maskKey(row.licence_key);
    return json({ ok: true, original_transaction_id: id, comp,
                  began_with: row.offer_identifier || null,
                  licence: maskKey(row.licence_key) });
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
    // Each check is isolated. An upstream that threw — a DNS failure, a
    // timeout — used to escape and turn the whole selftest into a 500, hiding
    // the answer for every other credential at exactly the moment somebody
    // was trying to find out which one was broken.
    const check = async (name, run) => {
      try {
        out[name] = await run();
      } catch (err) {
        out[name] = { ok: false, reason: 'unreachable', error: err?.name || 'Error' };
      }
    };

    await check('resend', async () => {
      if (!env.RESEND_API_KEY) return { ok: false, reason: 'not set' };
      const r = await fetch('https://api.resend.com/domains', {
        headers: { authorization: `Bearer ${env.RESEND_API_KEY}` },
      });
      if (r.ok) return { ok: true, status: r.status };
      // A key restricted to SENDING may not list domains and is refused with
      // restricted_api_key. That is the least-privileged key this Worker
      // should hold — it only ever sends — so it is healthy, and flagged as
      // restricted so nobody "fixes" it by issuing a full-access key.
      if (await errorNameOf(r) === 'restricted_api_key') {
        return { ok: true, status: r.status, restricted: true };
      }
      // 401 is a wrong key. Anything else is reported as itself rather than
      // collapsed into "failed".
      return { ok: false, status: r.status };
    });

    await check('paddle', async () => {
      if (!env.PADDLE_API_KEY) return { ok: false, reason: 'not set' };
      const base = env.PADDLE_API_BASE || 'https://api.paddle.com';
      const r = await fetch(`${base}/customers?per_page=1`, {
        headers: { authorization: `Bearer ${env.PADDLE_API_KEY}` },
      });
      return { ok: r.ok, status: r.status };
    });

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
    const parsed = await parseJsonObject(request);
    if (!parsed.ok) return json({ error: parsed.error, message: parsed.message }, 400);
    const body = parsed.body;

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
    audit.target = maskKey(row.licence_key);
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
