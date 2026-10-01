# MP Tenders: private Google Sheet profiles

Target: https://docs.google.com/spreadsheets/d/1VHILTCBB-CR0srqOTmaxf0b17wWJCpaOuMpVp_KphKw/edit

This integration is prepared in code. It does not become active merely by sharing
the spreadsheet link or connecting Google Drive to ChatGPT.

## One-time owner setup

1. Open the target Sheet → Extensions → Apps Script. Add the contents of `Code.gs`.
2. In Apps Script → Project Settings → Script Properties, set
   `MP_TENDERS_SHARED_SECRET` to a randomly generated secret of at least 32 characters.
   Keep it private; do not put it in GitHub, the Sheet cells, frontend code or chat.
3. Run `setupMpTenders` once and authorize access to your spreadsheet. Existing
   custom sheets are preserved. If a required tab already has incompatible headers,
   setup stops instead of overwriting it.
4. Deploy → New deployment → Web app. Execute as the owner, access Anyone.
   The web endpoint is reachable, but every data operation requires a valid HMAC
   signed backend request. Keep the spreadsheet's sharing restricted.
5. Export any existing local profile data before switching storage. Configure these environment variables on the existing Render backend:
   - `GOOGLE_SHEETS_WEBAPP_URL`: the deployment's HTTPS `/exec` URL.
   - `GOOGLE_SHEETS_SHARED_SECRET`: the exact same private secret from step 2.
   - `GUNICORN_CMD_ARGS`: `--timeout 120` for multiple authenticated Sheet operations.
6. Redeploy the backend and check `/api/admin/profile-storage` using its existing
   Google admin authentication. It must report `storage: google_sheets` and the
   target spreadsheet ID. Then verify a real consented registration and affidavit
   save/restore in the Sheet. Do not count mocked tests as a live connection.

## Stored records and identity

- `Users`: one contact per normalized mobile, aggregate visits, first name and
  district, explicit `mobile_verified: No`. A unique mobile count is not a count
  of verified people. Different people may share a phone.
- `VisitorSessions`: one browser registration UUID per saved session, linked to
  the contact. Retried registration requests do not create duplicates.
- `AffidavitProfiles`: bidder/firm/status/place and relative fields per browser
  session, linked to that contact. No generated PDF or tender-specific data.
- `VisitEvents`: idempotent registration/visit requests for daily analysis.

Affidavit read/write requires the backend-signed session token. A new browser
entering the same mobile can be grouped under the same contact without receiving
the other browser's affidavit data. Cross-device recovery requires a separately
verified identity (OTP or Google login); it is not enabled by mobile alone.

When both Google environment variables are absent, existing SQLite/Postgres
profile storage remains in use. When either variable is set, missing settings,
remote errors or failed saves return an error; there is no silent SQLite fallback.
Existing local records need a deliberate migration before the storage switch.

Apps Script has execution and request quotas; this integration uses a script lock
to serialize deduplication and updates. A throttled/failed operation asks the user
to retry and never claims an unconfirmed Google Sheet save.

For a script code update, publish a new deployment version using the existing web
app deployment so the configured URL remains valid.

## Password accounts and manual WhatsApp reset

The dashboard now uses Sign in / Sign up / Forgot password. Signup collects name,
mobile, district and a 15–128-character password plus confirmation. The backend
stores a salted scrypt hash (N=32768, r=8, p=3); it never stores/export plaintext
passwords. `Accounts` holds private account JSON and revision numbers;
`AccountRateLimits` holds hashed phone/IP keys and 15-minute attempt windows.
The account tabs are private and must not be published or publicly shared.

Sessions expire after 30 days; at most 10 devices remain signed in. Session tokens
and reset tokens are stored as SHA-256 digests of cryptographically random
values. Logout revokes the selected device; password reset revokes every device.
Compare-and-swap revisions prevent an old concurrent sign-in or reset from
overwriting a newer password/account change.

Forgot password opens a WhatsApp draft to SAR Digital Services. It does not
automatically send a message or create a reset token. After independently
verifying the registered WhatsApp number and account ownership, a Google admin
uses `/admin/` → User Password Reset to generate a one-use 15-minute reset link.
The user enters their own new password; the admin cannot view the old password.

Affidavit profiles are keyed to the stable password-account ID, allowing restore
on another device after successful password sign-in. Anonymous visitor endpoints
are disabled by default so they cannot bypass password-account access.

Production password-account operations require a configured Google Sheet or
PostgreSQL database. They fail with an explicit error on the existing ephemeral
SQLite backend. `ALLOW_EPHEMERAL_ACCOUNTS=1` is for isolated development/tests;
do not enable it on the free Render service. Existing anonymous profiles are not
silently claimed merely by entering the same unverified mobile number.
