# Updating Microsoft Store Listings Programmatically

## The Short Version

The `msstore` CLI is already installed, authenticated, and can read/write
Submission 14's listing metadata for all 47 locales. No CSV, no Partner
Center UI, no manual upload needed.

```powershell
# Get the current submission as JSON
msstore submission get 9PF25H395BB8 > submission.json

# Edit the JSON (update Listings.{locale}.BaseListing fields)
# ...

# Push the updated metadata
msstore submission updateMetadata 9PF25H395BB8 submission.json

# Commit for certification
msstore submission publish 9PF25H395BB8

# Poll until certified
msstore submission poll 9PF25H395BB8
```

---

## Three Programmatic Options (Best to Worst)

### 1. `msstore` CLI (recommended — already installed)

| Detail | Value |
|--------|-------|
| Install | `winget install "Microsoft Store Developer CLI"` |
| Version on this machine | 0.4.2.2 |
| Auth | Already configured — `msstore apps list` works |
| Maintains | Microsoft, actively (Feb 2026 update announced) |
| GitHub | [microsoft/msstore-cli](https://github.com/microsoft/msstore-cli) |
| Docs | [learn.microsoft.com/.../msstore-dev-cli/overview](https://learn.microsoft.com/en-us/windows/apps/publish/msstore-dev-cli/overview) |

**Key commands:**

- `msstore submission get <productId>` — full submission JSON
- `msstore submission updateMetadata <productId> <json-file>` — push listing changes
- `msstore submission update <productId> <json-file>` — push full submission changes
- `msstore submission publish <productId>` — commit for certification
- `msstore submission poll <productId>` — wait for certification result
- `msstore submission delete <productId>` — delete a pending draft
- `msstore submission status <productId>` — current status

**Listing JSON structure** (inside the submission):

```json
{
  "Listings": {
    "en-us": {
      "BaseListing": {
        "Description": "...",
        "Features": ["Feature 1", ..., "Feature 20"],
        "ReleaseNotes": "What's new...",
        "Keywords": ["job search", ...],
        "Title": "Dawnlist Job Search",
        "Images": [...]
      }
    },
    "de-de": { "BaseListing": { ... } },
    "fr-fr": { "BaseListing": { ... } }
  }
}
```

### 2. Partner Center REST API (direct)

The same API the CLI wraps. Use when you need finer control or CI
integration without the CLI binary.

| Detail | Value |
|--------|-------|
| Base URL | `https://manage.devcenter.microsoft.com/v1.0/my/` |
| Auth | Azure AD client credentials → bearer token |
| Token endpoint | `https://login.microsoftonline.com/{tenantId}/oauth2/token` |
| Resource/scope | `https://manage.devcenter.microsoft.com` |

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/applications` | List apps (get internal appId) |
| POST | `/applications/{appId}/submissions` | Create draft (clones last published) |
| GET | `/applications/{appId}/submissions/{subId}` | Read submission |
| PUT | `/applications/{appId}/submissions/{subId}` | Update submission (full JSON) |
| POST | `/applications/{appId}/submissions/{subId}/commit` | Commit for certification |
| GET | `/applications/{appId}/submissions/{subId}/status` | Poll status |
| DELETE | `/applications/{appId}/submissions/{subId}` | Delete draft |

**Important:** The API requires a full PUT — no PATCH for MSIX apps. GET the
submission, modify the `listings` object, PUT the whole thing back. Packages
don't need re-uploading if unchanged.

### 3. StoreBroker (PowerShell, legacy)

| Detail | Value |
|--------|-------|
| Install | `Install-Module StoreBroker` |
| Latest version | 1.21.2 (December 2020) |
| Status | Functionally dormant, not archived, no updates |
| GitHub | [microsoft/StoreBroker](https://github.com/microsoft/StoreBroker) |

Uses XML "PDP" files (one per locale) for listings. Was used internally by
Microsoft across 64 languages. Still works mechanically — wraps the same REST
API — but receives no maintenance. Do not adopt for new work.

---

## Gotchas

1. **Don't mix API and UI.** Once you create/modify a submission via the API,
   do not touch it in Partner Center. It can get stuck in an unrecoverable
   state.

2. **`msstore submission publish` deletes any pending draft** and creates a
   new one from the last published submission. Run `get` first, make changes,
   then `updateMetadata` + `publish`.

3. **Paid apps caveat.** The CLI docs note that app update operations are
   "currently supported for free products only. Paid products will be
   supported in a future release." If this blocks you, fall back to the
   direct REST API. (Dawnlist's pricing is "Free" with IAP, so this may
   not apply.)

4. **409 errors.** The API returns 409 if the app uses mandatory app updates,
   Store-managed consumables, or Pricing Version 2.

5. **PowerShell JSON depth.** If using the REST API directly from PowerShell,
   `ConvertTo-Json -Depth 100` is required — the default depth 2 truncates
   the nested listings structure silently.

---

## Workflow for Updating v1.2.3 Listings

The script `tools/update_store_listings.py` (to be written) should:

1. Run `msstore submission get 9PF25H395BB8` to get the current draft JSON
2. For each of the 47 locales, update:
   - `Features` (20 items, from the translated locale catalogue)
   - `ReleaseNotes` (from the translated release notes)
3. Write the modified JSON to a temp file
4. Run `msstore submission updateMetadata 9PF25H395BB8 <temp-file>`
5. Run `msstore submission publish 9PF25H395BB8`
6. Run `msstore submission poll 9PF25H395BB8`

This replaces the entire CSV generation + manual Partner Center upload flow.

---

## CI/CD Integration

For GitHub Actions, use the official action:

```yaml
- uses: microsoft/microsoft-store-apppublisher@v1.2
  with:
    command: submission updateMetadata 9PF25H395BB8 metadata.json
    tenant-id: ${{ secrets.PARTNER_CENTER_TENANT_ID }}
    client-id: ${{ secrets.PARTNER_CENTER_CLIENT_ID }}
    client-secret: ${{ secrets.PARTNER_CENTER_CLIENT_SECRET }}
```

---

## References

- [MSStore CLI overview](https://learn.microsoft.com/en-us/windows/apps/publish/msstore-dev-cli/overview)
- [MSStore CLI commands](https://learn.microsoft.com/en-us/windows/apps/publish/msstore-dev-cli/commands)
- [Partner Center Submission API](https://learn.microsoft.com/en-us/windows/uwp/monetize/manage-app-submissions)
- [Update an app submission (JSON schema)](https://learn.microsoft.com/en-us/windows/uwp/monetize/update-an-app-submission)
- [GitHub Action](https://github.com/microsoft/microsoft-store-apppublisher)
- [StoreBroker (legacy)](https://github.com/microsoft/StoreBroker)
