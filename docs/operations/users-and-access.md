# Users and access

Sign-in, registration, password changes, user administration and session invalidation.

## Signing in and registering

The public pages share Verwalino branding, labelled form fields and the Klar design.
Sign in with your email address and password. During the request the fields are
disabled and the button reads “Anmeldung läuft …”. A failed request keeps the email
and password for correction; the error receives focus and scrolls into view.
Your personal design loads after you sign in.

Hosted login offers “Konto erstellen”. Enter the organization, your name and email,
then choose and repeat a password with 12–128 characters. Mismatching passwords
are explained before submitting. While registration runs, the form stays visible
and disabled. Its generic receipt asks you to check your email and spam folder;
it preserves the existing ten-minute resend rule and offers “Erneut senden”.
A resend failure keeps the receipt available.

The confirmation page asks for the password chosen during registration. A mistyped
password keeps the link available for another attempt. Invalid links require a
new registration request. Self-hosted login has no “Konto erstellen” action. “Passwort vergessen?” appears
when mail is available.

## Choosing your personal design

Open “Einstellungen” from the top bar or the navigation's “Persönlich” group.
Every signed-in role, including read-only, can choose **Klar** or **Warm**.
Klar is the default for new and existing accounts: pale side navigation and blue
accents. Warm uses petrol horizontal desktop navigation, cream surfaces and
property cards. On phones, the navigation stays at the top; the menu contains all
other areas available to your role.

Choose a preview and press “Design speichern”. Until the server confirms, the
choice is marked “Noch nicht gespeichert.” and the previous design stays active.
Failed saves retain the choice for another attempt. A failed initial load offers
“Erneut laden” and does not overwrite an existing saved preference. A successful
save applies immediately without clearing work on the current page.

The choice belongs to your user account, survives reloads and future logins, and
does not change a colleague's design. Both designs provide the same functions and
permissions. No additional appearance options or browser-local preferences exist.

## Recovering a forgotten password

When mail delivery is configured, select „Passwort vergessen?“ on the login page
and enter your email address. The same receipt appears whether an address has an
account. An active account with a matching membership receives a link valid for
one hour; ambiguous addresses receive no link. Open it and enter the new password
twice, then sign in with that password. A successful reset ends all existing
sessions and sends a confirmation email. Requesting another link invalidates the
previous one. An expired or used link requires a new request; a temporary server
failure keeps the link available for retry.

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

When mail delivery is configured, the „Benutzer“ page additionally offers
„Benutzer einladen“. Enter an e-mail address and a role and send the invitation;
the colleague receives an e-mail with a link that is valid for 72 hours and can be
used once. The link opens a public page where the colleague sets a password and is
then signed in-capable with the invited role; no admin-chosen password travels by
chat or phone. „Offene Einladungen“ lists pending invitations with validity and
offers „Erneut senden“ (issues a new link and invalidates the old one) and
„Widerrufen“. Issuing, resending and revoking are audit-logged. Without a
configured mail server no invitation card appears and user creation works only
with an admin-chosen password.

Admins create users in the „Neuen Benutzer anlegen“ card. Its persistent labels
are „E-Mail“, „Name“, „Startpasswort“ and „Rolle“; email and a 12–128-character
initial password are required. Fields stack on phones and remain inside the card.
Tab through the fields, choose the role and submit „Benutzer anlegen“ with Enter.
While creation is pending the fields and submit action are disabled. A failed
request keeps all entries for correction or retry; success clears the form and
refreshes the user list with the chosen role.

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
