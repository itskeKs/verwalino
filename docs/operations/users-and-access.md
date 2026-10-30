# Users and access

Password changes, user administration, and session invalidation.

## Changing your own password

After sign-in, „Passwort ändern“ (change password) in the top user bar opens the
dialog for current password, new password, and confirmation. A wrong old password
and an invalid new password are shown in the dialog. After a successful change,
the user is signed out; then sign in again with the new password. The local browser
flow is repeatable with
`scripts/acceptance/t14_password_browser.cjs` against `scripts/dev-local.sh`
(existing Playwright via `HV_PLAYWRIGHT_PACKAGE`, Chrome).

## User management

T57 introduced this workflow.

Under „Benutzer“ (users), admins edit role and active status or set a new
password (12–128 characters). Deactivation requires a confirmation; deactivating or
downgrading oneself is blocked. A reset sends no email and
never displays existing passwords. Roles take effect on the next request;
read-only hides business write actions, while managing one's own password remains available.

Reset, changing one's own password, and deactivation revoke existing cookies
via a persistent session generation. Reactivation does not revive old cookies.
The live check is performed again under the ACL lock; role/active changes
and non-sensitive audit data are stored atomically. The actual local
desktop/smartphone flow is `scripts/acceptance/t57_users_browser.cjs`.
The authenticated generation is also retained in later knowledge/plan transactions;
the isolated review runtime may only verify sessions, not look up
passwords. Unknown legacy roles are displayed as invalid and rejected at login,
not silently converted into a valid role.
