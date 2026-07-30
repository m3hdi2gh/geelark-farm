# secrets/

Put the Google service-account JSON key here (default path:
`secrets/service-account.json`, overridable with
`GOOGLE_SERVICE_ACCOUNT_JSON`).

Everything in this directory except this file is gitignored. Nothing here
should ever be committed, pasted into a chat, or attached to a ticket.

To create the key:

1. In Google Cloud, create a project and enable the **Google Sheets API**.
2. Create a service account and download a JSON key.
3. Save it as `secrets/service-account.json`.
4. Share the spreadsheet with the service account's `client_email` as an
   **Editor** — the tool writes status back.
