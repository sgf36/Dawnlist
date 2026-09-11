/**
 * The licence email — the message that decides whether a paying customer can
 * use what they bought.
 *
 * The webhook issued a licence and stopped; the comment beside it said
 * delivery was "a separate, deliberate step" and that step did not exist. So
 * these tests are mostly about the failures that strand somebody who has
 * already been charged: a key they cannot read, a language they cannot read,
 * or an email nobody notices did not send.
 */
import assert from 'node:assert';
import { EMAIL_STRINGS, RTL, strings } from '../src/email-strings.js';
import { customerDetails, escapeHtml, licenceEmail, localeFor, sendEmail } from '../src/email.js';

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); passed++; }
  catch (e) { console.log(`  FAIL ${name}\n       ${e.message}`); failed++; }
}

// Obviously fake on purpose. A real licence key sat here, and a test file is
// copied, pasted and published far more readily than a database is.
const KEY = 'DAWN-TEST0000-TEST0000-TEST0000-TEST0000';

console.log('the key itself');

await test('the key appears in the HTML and in the plain text', () => {
  const mail = licenceEmail({ licenceKey: KEY });
  assert.ok(mail.html.includes(KEY), 'HTML must carry the key');
  assert.ok(mail.text.includes(KEY), 'a client that renders no HTML must still get it');
});

await test('the key block is forced left-to-right even in an RTL email', () => {
  // Arabic reorders hyphenated groups on screen while copying them correctly,
  // so the reader sees a key that does not match what they paste.
  const mail = licenceEmail({ licenceKey: KEY, lang: 'ar' });
  assert.ok(mail.html.includes('dir="ltr"'), 'the key cell must be isolated');
  assert.ok(mail.html.includes('unicode-bidi:isolate'));
});

await test('a key containing HTML characters cannot break the markup', () => {
  const mail = licenceEmail({ licenceKey: 'DAWN-<script>&"' });
  assert.ok(!mail.html.includes('<script>'));
  assert.ok(mail.html.includes('&lt;script&gt;'));
});

console.log('language');

await test('every locale the app ships is present or falls back cleanly', () => {
  for (const code of Object.keys(EMAIL_STRINGS)) {
    const t = strings(code);
    for (const key of Object.keys(EMAIL_STRINGS.en)) {
      assert.ok(t[key], `${code} is missing ${key}`);
    }
  }
});

await test('an unknown language falls back to English rather than to blanks', () => {
  const t = strings('kl');
  assert.equal(t.subject, EMAIL_STRINGS.en.subject);
});

await test('a regional tag resolves to its base language', () => {
  assert.equal(localeFor('fr-CA'), 'fr');
  assert.equal(localeFor('pt_BR'), 'pt');
});

await test('an absent locale is English, never a guess from the country', () => {
  assert.equal(localeFor(null), 'en');
  assert.equal(localeFor(''), 'en');
});

await test('a translated email is actually in that language', () => {
  const en = licenceEmail({ licenceKey: KEY, lang: 'en' });
  const fr = licenceEmail({ licenceKey: KEY, lang: 'fr' });
  assert.notEqual(en.subject, fr.subject, 'the subject must be translated');
  assert.ok(fr.html.includes('lang="fr"'));
});

await test('right-to-left languages set direction on the document', () => {
  for (const code of RTL) {
    if (!EMAIL_STRINGS[code]) continue;
    const mail = licenceEmail({ licenceKey: KEY, lang: code });
    assert.ok(mail.html.includes('dir="rtl"'), `${code} must be rtl`);
  }
});

console.log('the allowance');

await test('the posting allowance is filled in, not left as a placeholder', () => {
  const mail = licenceEmail({ licenceKey: KEY, postingsPerDay: 700 });
  assert.ok(!mail.html.includes('{count}'), 'a literal brace reaches a paying customer');
  assert.ok(mail.text.includes('700'));
});

await test('no allowance means the sentence is omitted, not left empty', () => {
  const mail = licenceEmail({ licenceKey: KEY });
  assert.ok(!mail.html.includes('{count}'));
});

console.log('sending');

await test('sending without a Resend key is reported, never thrown', () => {
  // A thrown error here becomes a non-2xx, and Paddle retries a non-2xx —
  // which could issue a second subscription for one payment. A failed EMAIL
  // must never cost a customer a duplicate charge.
  return sendEmail({}, { to: 'a@b.test', subject: 's', html: 'h', text: 't' })
    .then((r) => {
      assert.equal(r.ok, false);
      assert.equal(r.error, 'no_resend_key');
    });
});

await test('replies go to a person, not to a no-reply address', async () => {
  let body = null;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    body = JSON.parse(init.body);
    return { ok: true, status: 200, text: async () => '{"id":"re_1"}' };
  };
  try {
    const r = await sendEmail({ RESEND_API_KEY: 'x' },
      { to: 'a@b.test', subject: 's', html: 'h', text: 't' });
    assert.equal(r.ok, true);
    assert.ok(body.reply_to && !/no-?reply/i.test(body.reply_to),
      'the one email a customer is most likely to answer must be answerable');
    assert.ok(body.text && body.html, 'both parts, always');
  } finally {
    globalThis.fetch = realFetch;
  }
});

console.log('finding the customer');

await test('an address on the event costs no round trip', async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('must not look up'); };
  try {
    // A locale that is actually generated, chosen from the catalogue rather
    // than hard-coded: this file must not fail merely because the translation
    // run has not reached German yet.
    const some = Object.keys(EMAIL_STRINGS).find((c) => c !== 'en') || 'en';
    const who = await customerDetails({}, { data: { customer: { email: 'a@b.test', locale: some } } });
    assert.equal(who.email, 'a@b.test');
    assert.equal(who.lang, some);
    assert.equal(who.source, 'event');
  } finally {
    globalThis.fetch = realFetch;
  }
});

await test('no address and no API key is reported by cause', async () => {
  const who = await customerDetails({}, { data: { customer_id: 'ctm_1' } });
  assert.equal(who.email, null);
  assert.equal(who.source, 'no_api_key');
});

await test('no customer at all is distinguishable from a failed lookup', async () => {
  const who = await customerDetails({}, { data: {} });
  assert.equal(who.source, 'absent');
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
