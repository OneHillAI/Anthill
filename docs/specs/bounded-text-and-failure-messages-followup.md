# Spec: Bounded text and fixed failure messages, the remaining places

Status: implemented. Lane: `pillar:platform`.

## What it does

A follow-up to `bounded-text-patterns.md`, `no-raw-error-text.md` and `local-only-redirects.md`. It applies
the same rules to the places those changes left out.

- **Model size in a model name** (`hosting/source.py`, `_SIZE_RE` and `params_from_name`): the pattern now has
  fixed limits. At most six digits before the point, at most three after it, at most three whitespace
  characters before the `b`, and only the first 256 characters of a name are read. A number with seven or
  more digits and no leading zero was already above the 2000B ceiling and was dropped. A number written with
  leading zeros and seven or more digits in all reads differently: `model-0000007b` read as 7 before and
  reads as nothing now. For the 87 distinct names, Ollama tags and Hugging Face ids in the bundled catalog
  (29 models) the old and the new pattern give the same result. The old pattern was already fast in practice
  (100 000 digits in a few milliseconds); the limits make the cost fixed whatever the input.
- **Admin connection checks, the org model endpoint check and the catalog refresh** answer with a fixed
  message per kind of failure. The response keys stay the same (`ok`, `detail`, `status` for the checks;
  `ok`, `error` for discovery and the catalog), and where the result is also stored in a `*_status_detail`
  or `org_backend_detail` column, the fixed message is stored.
- **What goes to the server log** (warning level): for a failed network or login call only the class of the
  error, plus the HTTP status where there is one, or AWS's error code. Never the text of the error and never
  a URL: that text can quote the endpoint URL (with a key in its query), the whole `Authorization` header
  (httpx does this when a key holds a character a header cannot carry, for example a trailing newline from
  an environment variable) or a password character (imaplib does this for a character it cannot send). The
  catalog signature reason (this server's own check) and a failed catalog save (a local path) keep their text
  in the log.
- The places:
  - `POST /settings/aws/test` (`cloud/aws.py validate`): when AWS answers and refuses (botocore
    `ClientError`), "AWS rejected the credentials (<code>). Check the access key ID, the secret and the
    region.", where `<code>` is AWS's error code, such as `InvalidClientTokenId`, shown only when it is a
    plain token. When there is no usable answer (botocore `BotoCoreError`: the endpoint cannot be reached, a
    timeout, no region), "Could not reach AWS. Check the region and the connection." Anything else: "Could
    not check the AWS credentials. Check the region and the connection and try again."
  - `POST /settings/aws/provision-test` (`cloud/aws.py provision_test`): "AWS could not launch the test
    machine (<code>). Common causes are the account's GPU quota, its permissions, the machine image (AMI), the
    subnet or security group, or no capacity for this instance type in the region.", or the same "Could not
    reach AWS" sentence as above. The guardrail reasons, which this server writes, are unchanged.
  - `POST /settings/training/test`: a check that raises answers "Could not check the training backend.
    Check its settings and try again."
  - `POST /settings/cloud/test` (`hybrid/providers.py validate_provider`): a network error answers "Network
    error reaching <provider>. Check the connection and try again." A key with a non-ASCII character, which
    used to escape the function as an unhandled error, answers "The <provider> key has a character that
    cannot be sent. Paste it again."
  - `POST /settings/events/imap/test` (`web/imap_idle.py test_connection`) and the background mailbox worker
    (`web/imap_idle.py _loop`), which writes `imap_status_detail` each time it fails to connect: both use one
    helper, `_failure_message`, so the worker stores the same sentence as the route. The sentence follows
    the phase as well as the kind of error. Before the login is through: "The mail server refused the
    login. Check the user and the password." for an IMAP error. After it: "The mail server refused a
    command. Check the folder name." for an IMAP error from SELECT, SEARCH, FETCH or STORE (for example a
    folder name the server does not accept). In either phase a network error or a dropped connection
    answers "Could not reach the mail server. Check the host and the port." A failed save of a new message
    into `inbox/` (a full disk, a read-only or missing folder, any file error) answers "Could not save the
    new mail on this device. Check the free space and the workspace folder." Anything else answers "Could
    not connect to the mailbox. Check the settings and try again." The log line names the class and the
    phase (`login`, `command` or `inbox`).
  - The org model endpoint check (`hosting/endpoint.py validate`), used by `POST
    /settings/organization/connect` and `POST /personalize/cloud` and stored in `org_backend_detail`: a
    failed check is stored as "Could not reach the server. Check the address and the connection.", "The
    server did not accept the key. Check the key and try again." (HTTP 401 or 403), "The server answered
    with HTTP <status>.", "Check the address (it should start with http:// or https://)." or, for the test
    chat, "The test chat did not get an answer. Check the model and the server." The URL is no longer part
    of the stored detail.
  - `POST /settings/organization/discover-models`: the same sentences as the member-facing provider
    discovery (`_discovery_failure_message`), naming "the model server". An address that httpx refuses
    before any network call (no `http://` or `https://`, or a malformed URL) answers "Couldn't reach the
    model server. Check the address (it should start with http:// or https://)." This sentence is added to
    the shared helper.
  - `POST /models/refresh-catalog`: "Could not reach the catalog service. Check the connection and try
    again.", "Refused: this catalog is not from the expected publisher. Your current models are
    unchanged." for a signature that is unreadable or does not verify, "This server cannot check the
    catalog signature. The catalog was not used." when the refusal comes from this server's own setup (no
    expected signer is configured, or the signature verifier is unavailable; `catalog_trust.cannot_check`),
    "The catalog is unreadable. Your current models are unchanged." and "Couldn't save the
    refreshed catalog. Check that the Anthill folder is writable; the server log has the detail."
  - `POST /settings/cloud/test` and `POST /models/refresh-catalog` keep their response keys, but no page in
    this repository calls either route at the moment, and no page shows the stored `cloud_status_detail`.
- **Session order cookie name** (`web/app.py _session_order_cookies`): the name of the newest
  `session_order_<n>` cookie is set again when a session is renewed. Only the form this server writes is
  read: 1 to 12 ASCII digits with no leading zero. Spellings that `int()` also accepts (`+5`, `05`, `9_0`,
  a space, non-ASCII digits) are ignored, so a renewal never sets a cookie under a name the browser chose.
  A request that carries a session only under such a spelling is not signed in by it.
- **Browser-side HTML** in `chat.html` (the queued-prompt list), `_inference_provider.html` and
  `_compute_chooser.html` (the provider key link): checked and left as they are. The queued prompt is
  passed through the page's `escHtml`, the chat answer through `renderMd` (sanitised, or escaped when the
  sanitiser is missing), and the provider names and key links come only from the built-in provider lists
  in the templates.

## Acceptance criteria

- `params_from_name` answers in under a second on 100 000 characters of digits, digits with a point,
  spaces or a long name with a size at the end, and still reads `qwen2.5:32b` as 32, `Llama-3-70B` as 70,
  `Qwen2.5-1.5B` as 1.5 and returns nothing for `mymodel-q4_K_M`.
- For each place above, an error whose text carries a marker string is absent from the response body, from
  the stored status detail and from the log (the catalog signature reason and a failed save are logged on
  purpose), the log names the class of the error, and the response keeps its keys.
- A provider key with a trailing newline, a CR LF pair or a NUL character never reaches the log.
- The mailbox worker stores the same fixed sentence as the mailbox test route. An IMAP error after a good
  login is reported as a refused command, not a refused login, in both; a failed save into `inbox/` in the
  worker is reported as a save problem on this device, not as an unreachable server.
- A refused catalog signature caused by this server's own setup is not reported as a catalog from the wrong
  publisher.
- The org endpoint check and server model discovery never write the URL (or a key in its query) to the
  stored detail or the log.
- `_session_order_cookies` reads `session_order_12` and `session_order_3` and ignores every other spelling;
  a renewal of a request that carries both `session_order_5` and `session_order_05` sets only
  `session_order_5`.

## Known limits

- A name longer than 256 characters is read only up to that point, a size with more than three digits after
  the point is not read, and a size written with leading zeros to seven or more digits is not read. No model
  name in the catalog comes near any of these.
- `validate_provider` still shows the provider's own HTTP status and the start of its response body when it
  answers with an error; that is the provider's answer, not an error raised on this server (the same rule
  as the remote MCP message in `no-raw-error-text.md`).
- When a provider has no `/models` route, `validate_provider` falls back to a short chat call; a read timeout
  or a body that is not JSON on that path still ends in a server error, as before.
- Existing audit records that carry the address as `url=` (the org endpoint connect, the personal cloud
  connect and server model discovery) or `src=` (a refused catalog signature) are unchanged and out of
  scope here. The audit record of a refused catalog signature also keeps the first 120 characters of the
  reason, as before.
- Provisioning (`web/provision_run.py`) still adds the text of a provisioner or wiki-host error to
  `org_backend_detail`, as before.
- The places listed under Known limits in `no-raw-error-text.md` are unchanged, as are the MCP connector
  `last_error` field, the backup restore audit record, the benchmark status and the research failure audit
  record, which keep the error text for the operator.

## Tests

`tests/test_bounded_patterns.py` (model size), `tests/test_no_raw_errors.py` (AWS check and provision test,
each kind of failure; server model discovery, including an address without a web scheme; the org endpoint
check), `tests/test_training_settings.py`, `tests/test_cloud_fallback_connect.py` (including keys with a
newline, a CR LF pair, a NUL or a non-ASCII character, against a local listener), `tests/test_inbound_push.py`
(the mailbox helper, the route and the background worker, including a refused command after the login and a
failed save into `inbox/`), `tests/test_local_model_settings.py` (catalog refresh, including the two reasons
this server cannot check a signature), `tests/test_jwt_live_revocation.py` (order cookie names and renewal), and the updated assertions in
`tests/test_hosting_connect.py` and `tests/test_aws_cloud.py`.
