/**
 * Reading a JSON request body that must be an object.
 *
 * Shared because both public POST routes had the same hole: `request.json()`
 * happily returns `null`, an array or a bare number, and the handlers then
 * read properties off it and threw. A throw is a 500, which the app cannot
 * tell from the Worker being down.
 */
export async function parseJsonObject(request) {
  let body;
  try {
    body = JSON.parse(await request.text());
  } catch {
    return { ok: false, error: 'bad_json', message: 'The request body is not valid JSON' };
  }
  if (body === null || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_body', message: 'The request body must be a JSON object' };
  }
  return { ok: true, body };
}
