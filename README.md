# Telemetry schema

The wire contract between the FailoverBeacon SDK and a beacon: what an upload contains and how a beacon answers. Producers and receivers take these files; neither changes the contract on its own side.

## Layout

| Path | Contents |
| --- | --- |
| `schemas/v1/` | `upload-metadata` for the first line of the body, `event` for every further line |
| `samples/v1/<schema>/valid/` | Lines every producer must be able to write and every receiver must accept |
| `samples/v1/<schema>/invalid/` | Lines every receiver must reject: rules that tie fields together, and undeclared fields |
| `VERSION`, `SHA256SUMS` | The version, equal to the tag without `v`; hashes of `VERSION`, the schemas and the samples |

JSON Schema 2020-12. Objects allow no undeclared properties. The meaning of each field is in its `description`.

## HTTP

- `POST` to the beacon URL from the SDK's configuration. The reference backend uses `https://<host>/v1/beacon/<project key>`.
- `Content-Type: application/x-ndjson`, `Content-Encoding: gzip`.
- Body: UTF-8, one JSON object per line. Line 1 is the upload metadata; every further line is an event. At most 1 MiB before compression.
- An event's age is `sent_at − start_time`; both come from the same device clock.
- Delivery is at least once. A receiver drops duplicates by `call_id` + `resend_count` + `start_time`.
- The SDK waits up to 30 s for the response, then treats the request as unanswered.

| Response | SDK action |
| --- | --- |
| 2xx | Deletes the sent events |
| No response, 408, 429, 5xx | Keeps them and backs off; `Retry-After` is not read |
| 410 | Deletes every event queued for this beacon and stops sending to it until the next `initialize` |
| Anything else, 3xx included | Deletes them and counts them as `send_error` |

A receiver that rejects some lines of a request still answers 2xx for the rest; a 4xx would make the SDK delete the valid lines too.

## Versions

Tags are `vMAJOR.MINOR.PATCH`. The SDK sends the tag it was built against as `schema_version` (without `v`); `format_version` in an event equals MAJOR.

| Change | Step |
| --- | --- |
| New optional field, new enum value | MINOR |
| Description, sample | PATCH |
| Removed or renamed field, changed type or meaning, new required field | MAJOR: new `schemas/vN/`, kept next to the old ones |

Release order: schema tag, then receivers, then producers. A receiver must know every value a device can send.

## Checks

```bash
uv run scripts/check.py
```

- The schemas are valid; valid samples pass and invalid ones fail; every property and enum value appears in a valid sample.
- Every valid sample of every earlier tag passes the current schemas.
- `VERSION` matches the commit's tag, and `SHA256SUMS` matches the files. After a change, run `uv run scripts/check.py --write-sums`.

## Using it

A consuming repository keeps `VERSION`, `schemas/`, `samples/` and `SHA256SUMS` of one tag, copied by a command that takes the tag, and checks the copy against `SHA256SUMS`. A producer proves that its serializer writes the valid samples, compared as parsed JSON; a receiver proves that it accepts them.

## License

[MIT](LICENSE)
