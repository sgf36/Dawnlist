/**
 * Delivering the licence to the person who just paid for it, in their language.
 *
 * WHY THIS FILE HAD TO EXIST
 * --------------------------
 * The Paddle webhook issued a licence, wrote it to D1, and stopped. The comment
 * beside it said "delivery is a separate, deliberate step" — and that step did
 * not exist. A customer could pay $79, have a perfectly valid licence created,
 * and never receive it. That is the worst class of failure in the product,
 * because it happens AFTER taking money.
 *
 * WHY IT IS TRANSLATED, AND INTO THE SAME FIFTY
 * ---------------------------------------------
 * The application ships in fifty languages. A buyer who chose Dawnlist in
 * Japanese, read a Japanese site and set up a Japanese application, and then
 * receives the one email that actually unlocks it in English, has been told
 * that the translation was decoration. The list is the app's own
 * `LOCALE_CODES`, not a shorter one chosen for convenience — a subset would
 * mean deciding whose language mattered.
 *
 * WHY THE MARKUP IS TABLES AND INLINE STYLES
 * ------------------------------------------
 * Ported deliberately from the support Worker that already serves Easy-Post
 * and Wren, so a Dawnlist customer's licence email and their support reply
 * look like the same company wrote them. Table-based and image-free on
 * purpose: remote images are blocked by default in most inboxes and hurt
 * deliverability, while a wordmark in an accent bar reads as "designed" with
 * none of that risk. Every message ships HTML *and* plain text, because a
 * meaningful share of clients never render the HTML.
 *
 * The colours are Dawnlist's own, from brand-dawnlist/README.md. No colour was
 * invented for email — that file says no new colours and an email is not an
 * exception.
 *
 * WHAT THE EMAIL DELIBERATELY DOES NOT DO
 * ---------------------------------------
 * It does not link to a download. The Store listing and the direct download
 * live on the website, which changes far more often than this Worker does, and
 * a stale download link in a licence email is worse than none.
 */
import { EMAIL_STRINGS, RTL, strings } from './email-strings.js';

const SERIF = "Georgia,'Iowan Old Style',serif";
const SANS =
  "ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif";
const MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace";

// Dawnlist's palette, matching the site and the support emails.
const BRAND = {
  green: '#1E4B45',
  wordmarkColor: '#DDA758',
  ink: '#191a1c',
  body: '#404040',
  muted: '#6b6b6b',
  cream: '#f0ece4',
  rule: '#e4ddcd',
  white: '#ffffff',
  wordmark: 'Dawnlist',
  site: 'https://dawnlist.spencerfields.com',
  support: 'Apps@spencerfields.com',
};

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * The locale to write in, from whatever Paddle knows about the customer.
 *
 * Falls back to English rather than guessing from the country: a buyer in
 * Belgium may want French, Dutch or English, and picking one from the address
 * is a coin toss dressed as personalisation. Paddle's own `locale` is what the
 * customer chose.
 */
export function localeFor(raw) {
  if (!raw) return 'en';
  const lower = String(raw).toLowerCase().replace('_', '-');
  if (EMAIL_STRINGS[lower]) return lower;
  const base = lower.split('-')[0];
  return EMAIL_STRINGS[base] ? base : 'en';
}

/**
 * The branded outer shell. `preheader` is the hidden inbox-preview snippet —
 * without one, clients show the first words of the body, which here is the
 * heading and tells the reader nothing they cannot already see.
 */
export function emailShell({ title, preheader, bodyHtml, lang = 'en' }) {
  const br = BRAND;
  // Right-to-left scripts need the direction on the document, not on a div:
  // set it lower down and the table layout still lays out left-to-right and
  // the email reads as though it were never translated at all.
  const dir = RTL.has(lang) ? 'rtl' : 'ltr';
  const align = dir === 'rtl' ? 'right' : 'left';
  return `<!DOCTYPE html>
<html lang="${escapeHtml(lang)}" dir="${dir}"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>${escapeHtml(title)}</title>
</head>
<body style="margin:0;padding:0;background:${br.cream};" dir="${dir}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:${br.cream};">${escapeHtml(preheader || '')}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${br.cream};">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" dir="${dir}" style="width:100%;max-width:600px;background:${br.white};border:1px solid ${br.rule};border-radius:10px;overflow:hidden;">
    <tr><td align="${align}" style="background:${br.green};padding:20px 28px;">
      <span style="font-family:${SERIF};font-size:20px;font-weight:700;color:${br.wordmarkColor};letter-spacing:.3px;">${br.wordmark}</span>
    </td></tr>
    <tr><td align="${align}" style="padding:28px;font-family:${SANS};font-size:15px;line-height:1.6;color:${br.body};">
      ${bodyHtml}
    </td></tr>
    <tr><td align="${align}" style="padding:18px 28px;border-top:1px solid ${br.rule};font-family:${SANS};font-size:12px;line-height:1.7;color:${br.muted};">
      ${escapeHtml(strings(lang).footer)}<br>
      <a href="${br.site}" style="color:${br.green};text-decoration:none;">dawnlist.spencerfields.com</a>
      &nbsp;·&nbsp;
      <a href="mailto:${br.support}" style="color:${br.green};text-decoration:none;">${br.support}</a>
    </td></tr>
  </table>
</td></tr></table>
</body></html>`;
}

/**
 * The licence email.
 *
 * The key is presented in a monospaced block on its own, because it is the one
 * thing in the message that must be copied exactly, and a key wrapped inside a
 * paragraph invites a mis-selection that takes a trailing space with it. It is
 * also forced left-to-right even in a right-to-left email: the key is not
 * language, and an RTL client will otherwise reorder its hyphenated groups on
 * screen while copying them correctly, so the reader sees a key that does not
 * match what they paste.
 */
export function licenceEmail({ licenceKey, postingsPerDay, lang = 'en' }) {
  const t = strings(lang);
  const key = escapeHtml(licenceKey);
  const allowance = postingsPerDay
    ? t.allowance.replace('{count}', Number(postingsPerDay).toLocaleString(lang === 'en' ? 'en-GB' : lang))
    : '';

  const bodyHtml = `
<p style="margin:0 0 14px;font-family:${SERIF};font-size:22px;line-height:1.25;color:${BRAND.ink};">${escapeHtml(t.heading)}</p>
<p style="margin:0 0 18px;">${escapeHtml(t.thanks)}</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;">
  <tr><td dir="ltr" align="left" style="background:${BRAND.cream};border:1px solid ${BRAND.rule};border-radius:8px;padding:16px 18px;font-family:${MONO};font-size:16px;letter-spacing:.5px;color:${BRAND.ink};word-break:break-all;unicode-bidi:isolate;">${key}</td></tr>
</table>
<p style="margin:0 0 8px;"><strong style="color:${BRAND.ink};">${escapeHtml(t.to_use_heading)}</strong></p>
<p style="margin:0 0 18px;">${escapeHtml(t.to_use_body)}</p>
${allowance ? `<p style="margin:0 0 18px;color:${BRAND.muted};">${escapeHtml(allowance)}</p>` : ''}
<p style="margin:0 0 18px;">${escapeHtml(t.own_key)}</p>
<p style="margin:0 0 6px;">${escapeHtml(t.not_installed)} <a href="${BRAND.site}" style="color:${BRAND.green};">dawnlist.spencerfields.com</a></p>
<p style="margin:18px 0 0;color:${BRAND.body};">${escapeHtml(t.reply_note)}</p>`;

  const text = `${t.heading}

${t.thanks}

    ${licenceKey}

${t.to_use_heading.toUpperCase()}
${t.to_use_body}
${allowance ? `\n${allowance}\n` : ''}
${t.own_key}

${t.not_installed} ${BRAND.site}

${t.reply_note}

—
${t.footer}
${BRAND.site}  ·  ${BRAND.support}`;

  return {
    subject: t.subject,
    preheader: t.preheader,
    lang,
    html: emailShell({ title: t.subject, preheader: t.preheader, bodyHtml, lang }),
    text,
  };
}

/**
 * Resend's error name from a failure body, or the status when there is none.
 *
 * Only a plain identifier is accepted. The name is logged and stored, and a
 * field that could carry free text would carry whatever Resend put in it.
 */
function resendErrorName(body, status) {
  try {
    const name = JSON.parse(body)?.name;
    if (typeof name === 'string' && /^[a-z0-9_]{1,64}$/i.test(name)) return name;
  } catch {
    /* not JSON: fall through to the status */
  }
  return `http_${status}`;
}

/**
 * Send one message through Resend.
 *
 * Returns `{ ok, ... }` rather than throwing, including when Resend cannot be
 * reached. The caller is a webhook: a thrown error there becomes a non-2xx, and
 * Paddle retries a non-2xx — which would re-run the whole handler. A failed
 * EMAIL must never cost a customer a duplicate subscription.
 *
 * On failure `error` is a code safe to log and store, and `detail` is Resend's
 * own message, for an administrator who asked for the send. The message is
 * never logged: Resend echoes the recipient's address and domain back in it,
 * and a log line is the one place this Worker promises holds no content.
 */
export async function sendEmail(env, { to, subject, html, text }) {
  if (!env.RESEND_API_KEY) return { ok: false, error: 'no_resend_key' };

  let res;
  try {
    res = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        authorization: `Bearer ${env.RESEND_API_KEY}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        from: env.LICENCE_FROM || 'Dawnlist <noreply@dawnlist.spencerfields.com>',
        // Replies reach a person. A no-reply address on the one email a paying
        // customer is most likely to answer is a support failure by design.
        reply_to: env.LICENCE_REPLY_TO || 'Apps@spencerfields.com',
        to: [to],
        subject,
        html,
        text,
      }),
    });
  } catch {
    return { ok: false, error: 'resend_unreachable' };
  }
  const body = await res.text();
  if (!res.ok) {
    return { ok: false, status: res.status, error: resendErrorName(body, res.status),
             detail: body.slice(0, 300) };
  }
  let id = null;
  try {
    id = JSON.parse(body).id;
  } catch {
    /* an id we cannot read is not a failure to send */
  }
  return { ok: true, status: res.status, id };
}

/**
 * The buyer's email address and chosen language, from Paddle's customer record.
 *
 * ONLY from the customer record, looked up by `customer_id`. The event's
 * `custom_data` is whatever the page that opened the checkout put there, so an
 * address in it is one the BUYER chose — anybody's — and the licence key would
 * be sent wherever they pointed it. The event's other address fields are not
 * read either: the customer record is where Paddle holds the address the
 * payment was made under, and a single source cannot disagree with itself.
 *
 * `subscription.created` carries `customer_id` and no address, so this is the
 * lookup most issues needed anyway. A missing key or a failed lookup degrades
 * to "we could not find the address", reported by cause.
 */
export async function customerDetails(env, event) {
  const id = event?.data?.customer_id;
  if (!id) return { email: null, lang: 'en', source: 'absent' };
  if (!env.PADDLE_API_KEY) return { email: null, lang: 'en', source: 'no_api_key' };

  const base = env.PADDLE_API_BASE || 'https://api.paddle.com';
  let payload;
  try {
    const res = await fetch(`${base}/customers/${encodeURIComponent(id)}`, {
      headers: { authorization: `Bearer ${env.PADDLE_API_KEY}` },
    });
    if (!res.ok) return { email: null, lang: 'en', source: `lookup_failed_${res.status}` };
    payload = await res.json();
  } catch {
    return { email: null, lang: 'en', source: 'lookup_unreachable' };
  }
  return {
    email: payload?.data?.email || null,
    lang: localeFor(payload?.data?.locale),
    source: 'api',
  };
}
