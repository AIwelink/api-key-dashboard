# SogouEdu Auto-Replenishment Settings Design

## Goal

Add an operator-facing automatic replenishment settings page for purchasing short-lived normal Team accounts from SogouEdu. This first phase stores and validates configuration only. It must not create, poll, or take a real purchase order.

## Scope

- Add a dedicated `自动补号` page and route.
- Store the SogouEdu customer username and encrypted password on the backend.
- Configure the minimum schedulable account count and minimum predicted runway.
- Bind the configuration to the `us06-5001` Sub2API site and `plus账号池01` group.
- Fix the initial product mapping to `oauth_7d` and the local account type to normal `team`.
- Test login, balance, and inventory access without creating an order.
- Record configuration changes and connection tests in the existing audit log.
- Do not add an automatic purchase worker or any endpoint capable of creating an order in this phase.

## Chosen Approach

Use one dedicated provider configuration document for the initial SogouEdu integration. The document includes a provider discriminator so a later phase can add suppliers without redesigning the API, while avoiding a generic provider-plugin system before it is needed.

Environment-only configuration is rejected because operators need to inspect, update, and test the supplier from the application. Embedding these fields in the general Sub2API site configuration is rejected because supplier credentials and replenishment policy are a separate operational concern.

## Page And Navigation

Add a standalone `/auto-replenishment` route named `自动补号`. It belongs to the account-pool operations area rather than system or client-site configuration.

The page contains one SogouEdu configuration section:

- automatic replenishment enabled switch, default off;
- provider name `SogouEdu`;
- read-only base URL `https://sogouedu.cc`;
- customer username;
- customer password input;
- password configured indicator;
- minimum account count, default `2`;
- minimum runway, default `5` minutes;
- read-only product `oauth_7d`;
- read-only local account type `team`;
- target Sub2API site, initially `us06-5001`;
- target group, initially `plus账号池01`;
- save action;
- connection test action;
- last update and last test status.

Leaving the password input blank while updating an existing configuration preserves the stored password. The API never returns the password or an encrypted password value to the frontend.

## Trigger Contract For The Later Purchase Phase

This phase stores the policy but does not execute it. The later purchase worker will treat either condition as a replenishment trigger:

1. schedulable healthy account count is below `minimum_account_count`; or
2. the minimum of the following three runway values is below `minimum_runway_minutes`:
   - actual runway;
   - P50 dynamic forecast runway;
   - P90 conservative forecast runway.

Safety concurrency coverage is not part of this policy. Missing runway data alone must not trigger a purchase; the account-count condition can still trigger independently.

The intended strategy is just-in-time replenishment because these purchased accounts are expected to remain useful for about 30 minutes. Future order quantity calculation must fill only the immediate deficit and count pending, delivered, and push-pending accounts before creating another order.

## Configuration Storage

Store one document in `auto_replenishment_settings` using a stable identity derived from provider, target site, and target group. The document contains:

- provider and fixed base URL;
- customer username;
- encrypted customer password and encryption version;
- enabled flag;
- minimum account count;
- minimum runway minutes;
- product code and local account type;
- target site ID and target group ID/name;
- password-configured state derived for public responses;
- last connection-test status, timestamp, balance summary, inventory summary, and sanitized error;
- created/updated actor IDs and names;
- created/updated timestamps.

Encrypt the password at the application layer with authenticated encryption using the configured application secret as key material. Ciphertext is stored in MongoDB, decrypted only for server-side provider calls, never logged, and never included in audit snapshots or API responses.

## Backend API

Add authenticated, permission-controlled endpoints under `/auto-replenishment/settings`:

- `GET /auto-replenishment/settings` returns the public configuration and defaults when no document exists.
- `PUT /auto-replenishment/settings` validates and upserts the configuration. A blank password preserves an existing secret but is rejected on first save.
- `POST /auto-replenishment/settings/test` logs in and queries balance and inventory using the stored configuration. It never calls the order-creation or take endpoints.

The test uses `quantity=1` and product `oauth_7d` for inventory validation. A successful response exposes only operational summaries such as available inventory, price estimate, remaining-time range, and balance amounts in fen. The customer token returned by login remains in request-local memory and is discarded after the test.

## Validation And Error Handling

- Username is required.
- Password is required for initial configuration and optional on later updates.
- Minimum account count is an integer from 1 through 10,000.
- Minimum runway is an integer from 1 through 1,440 minutes.
- Target site must exist and be an active Sub2API site.
- Target group must exist in that site's group cache.
- The selected group name is stored as display metadata; group ID is authoritative.
- Provider calls use bounded connect/read timeouts and do not automatically retry login failures.
- HTTP 401 is reported as invalid supplier credentials.
- HTTP 402 is not expected during testing because no order is created.
- Remote response bodies are parsed as JSON and sanitized before storage or display.
- Passwords, customer tokens, and raw response bodies are excluded from logs and audit data.

## Audit And Permissions

Reuse the existing authenticated router and audit-log patterns. View access follows the account-pool configuration permission boundary. Saving configuration requires the corresponding management permission.

Audit actions are:

- `auto_replenishment.settings_update`;
- `auto_replenishment.connection_test`.

Audit snapshots contain public configuration fields and sanitized test results only.

## Testing

Backend coverage includes:

- defaults are returned before the first save;
- initial save requires username and password;
- password is encrypted at rest and absent from public responses and audit snapshots;
- blank password preserves the existing ciphertext;
- numeric bounds are enforced;
- invalid target site or group is rejected;
- connection test calls login, balance, and inventory only;
- connection test never calls order creation or take delivery;
- successful tests persist sanitized status and summaries;
- credential and transport failures persist sanitized errors;
- customer tokens and passwords do not appear in stored test results.

Frontend coverage includes:

- defaults render as account count `2` and runway `5` minutes;
- the password-configured state is shown without revealing the secret;
- blank password updates preserve the configured state;
- save and connection-test success/error feedback is visible;
- the page remains usable on desktop and mobile widths.

## Deployment And Follow-Up

No migration is required. The first GET supplies defaults and the first save creates the document. The automatic replenishment switch defaults to off, and this phase has no worker that reads it, so deployment cannot cause billing.

The next phase will add durable order records and a replay-safe purchasing orchestrator. It must persist an order identity before polling, count in-flight supply when calculating a deficit, import delivered JSON separately from pushing it to Sub2API, and never repurchase merely because import or push failed.
