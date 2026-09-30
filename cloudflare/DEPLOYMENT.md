# Cloudflare migration

The Worker replaces the Render API. The frontend and tender extraction remain
on GitHub Pages and GitHub Actions. D1 stores users and login history. The
current frontend API address stays unchanged until all migration checks pass.

From `cloudflare/`:

The GitHub `Cloudflare API Checks and Deployment` workflow can create/reuse D1 and
deploy the API after the Cloudflare account and API secrets are connected in the
`cloudflare` GitHub environment. It does not change the frontend URL.

1. `npm ci`, `npx wrangler login`, then `npx wrangler whoami` to verify the account.
2. `npx wrangler d1 create mp-tenders-users`; insert the returned database ID in
   `wrangler.jsonc`. Keep the Free plan.
3. `npx wrangler d1 migrations apply mp-tenders-users --remote`.
4. Use `wrangler secret put` for `TELEGRAM_BOT_TOKEN`, `GOOGLE_CLIENT_ID`,
   `GITHUB_ACTIONS_TOKEN`, `TELEGRAM_SESSION_SECRET`, `ADMIN_SESSION_SECRET`,
   and `ADMIN_TELEGRAM_IDS`. Keep the existing session secrets unchanged so
   previous logins continue to work. Missing secrets are not printed or stored
   in committed files. The GitHub token needs workflow dispatch permission.
5. `npm test`, `npm run check`, then `npm run deploy`. Check `/health` and admin
   Google login before switching any frontend address.
6. Obtain the authenticated Render `/api/admin/users-migration` snapshot using
   the existing Google admin session. Save it outside the repo with restricted
   file permissions. Use `python export_to_d1.py /private/snapshot.json
   /private/mp-users.private.sql` and then `npx wrangler d1 execute
   mp-tenders-users --remote --file /private/mp-users.private.sql`.
7. Pause registration only during the final snapshot/import/cutover window.
   Compare exact user IDs, profiles, login-event IDs, login counts and totals.
   Test registration, old session restoration, admin controls, Excel download,
   profile photos and user-initiated PDF delivery. Do not send a test Telegram
   message without an explicit recipient and request.
8. Only after verification, use `python cloudflare/switch_api.py https://<verified-worker>.workers.dev`
   to update the frontend API base URL to the deployed Worker URL. Keep the old database and API for rollback. Before a rollback,
   copy post-cutover registrations and events back so no new user data is lost.

No private migration payload is committed to GitHub. Cloudflare Free has
request and D1 usage quotas; no unlimited-availability guarantee is made.
Render free-service filesystem storage is not used as a fallback user database.
