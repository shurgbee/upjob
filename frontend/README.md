# UpJob frontend

UpJob is a Next.js 16 App Router project with WorkOS AuthKit authentication.

## Local setup

Install dependencies:

```bash
bun install
```

Copy the environment template and add the credentials from your WorkOS staging environment:

```bash
cp .env.example .env.local
```

Generate a cookie password with `openssl rand -base64 24`. Keep `WORKOS_API_KEY` and `WORKOS_COOKIE_PASSWORD` server-side and out of source control.

In **WorkOS Dashboard -> Redirects**, configure:

- Redirect URI: `http://localhost:3000/callback`
- Sign-in URL: `http://localhost:3000/sign-in`
- Logout redirect: `http://localhost:3000/login`

Enable the OAuth providers you want to offer under the AuthKit authentication settings. The hosted AuthKit screen automatically displays enabled email, password, OAuth, and SSO methods; no provider-specific frontend code is required.

Start the app:

```bash
bun run dev
```

Open `http://localhost:3000`. Signed-out users land on `/login`; successful authentication returns them to `/home`. `/home` and `/shop` require an authenticated WorkOS session.

## Authentication structure

- `proxy.ts` refreshes sessions and protects authenticated routes.
- `app/sign-in/route.ts` and `app/sign-up/route.ts` initiate hosted AuthKit flows.
- `app/callback/route.ts` exchanges the OAuth code and creates the encrypted session cookie.
- `AuthenticatedShell` verifies the session again during server rendering and seeds `AuthKitProvider` without exposing the access token.

For production, create production WorkOS credentials and register the deployment's HTTPS callback, sign-in, and logout URLs before updating its environment variables.
