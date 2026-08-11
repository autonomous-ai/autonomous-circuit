# JLCPCB Open Platform integration

Quote, order and track a board from inside the app instead of downloading a
gerber zip and filling in a form on jlcpcb.com. The client lives in
`viewer/src/server/circuit/jlcpcb*.mjs`.

**Status on 2026-08-11: signing works against the live platform and every path
below is confirmed to exist, but no call has returned data yet.** The account's
application is missing API scope, so the platform answers `403 API insufficient
permissions, access denied` on all of them. That is the only remaining blocker
and it is a console setting, not code.

## The highest-value endpoint is not the order button

`pcb/audit/get` — pre-review — runs **JLC's own gerber parser** over the files
we are about to send and returns the board dimensions and a preview image.
Every other check in this repo is our opinion of our own output. This one is
the fab's, it is free, and it happens before any money moves.

`comparePreReview()` turns it into a gate: if JLC reads a different board size
than we drew, the export describes the wrong board and a two-week round trip is
about to be wasted proving it. That serves the fab-ready bar directly, which is
why it was built before the checkout flow.

## Auth

HMAC-SHA256, base64, over a five-line string in an `Authorization: JOP …`
header. Three details cause almost every signature failure:

1. **Every line ends with `\n`, including the last.** Four newlines is the
   common mistake and fails with a generic 401.
2. **The bytes signed must be the bytes sent.** The client serializes once and
   signs and sends the same string.
3. **The header must be one line.** The docs wrap it only for readability.

```
POST\n/overseas/openapi/pcb/audit/get\n1625208260\nIZHE…TO50\n{"key":"…"}\n
```

Verified two ways. Offline, the implementation reproduces JLC's published
worked example byte for byte — that is the first test in `jlcpcb.test.mjs`, and
if it ever fails nothing built on the client can be trusted:

```
AccessKey b6713a535d56412f805afadd7e818455
SecretKey z0BWlikshimuyiwBsH1i2qwnzMb3j3kA
POST /order/v1/createOrder, timestamp 1625208260, nonce IZHEJYNIHYZIE8S0LLC0VWTPJVRRTO50
body      {"goodsId":100,"quantity":52,"createdTime":"2024-03-21 10:03:20"}
signature sygwKhKBkLwHVv0c7D+a/A7JTEJjGH/kLugFKh16918=   ✓ reproduced
```

Live, the platform now rejects requests on *permission* grounds rather than
authentication grounds, which it can only do after checking the signature.

**File uploads are the one exception to rule 2.** For multipart requests the
signature covers the *meta JSON*, not the multipart body. The form carries one
`meta` part holding that exact JSON and one `file` part.

## HTTP 200 does not mean success

Business failures arrive inside a 200 as `{code, message}` — for example
`{"code":1001,"message":"Insufficient prepaid balance"}`. Success is `code`
200. A client that checks only the status code reports "ordered" on a failure,
which for an ordering integration is the worst available bug, so `request()`
treats the body as authoritative and throws on any non-success.

Every response carries a `J-Trace-ID` header. JLC support cannot find a request
without it, so it rides on every error as `err.traceId`.

## How the paths were found

**The docs never publish the URLs, and the one path that appears in the docs
prose does not exist.** `/order/v1/createOrder`, from the signature worked
example, answers `401 API not exists`. It is illustrative. Nothing guessed from
its shape resolves.

The real surface came from [`i2cjak/jlcpcb_api`](https://github.com/i2cjak/jlcpcb_api),
a Python client reverse-engineered from JLC's own Java SDK jars. The SDK is not
on Maven Central; the jars are a manual download.

Each path was then checked against the live platform, which distinguishes the
two failures cleanly once a valid appid signs the request:

| Response | Meaning |
|---|---|
| `401 API not exists` | no such path |
| `403 API insufficient permissions, access denied` | the path exists; the app lacks the scope |

A 403 is therefore a *positive* result. Every confirmed path below earned one.

## The endpoints

All POST with a JSON body unless noted. `PCB_*` and `COMPONENT_*` are the table
keys in `jlcpcb-endpoints.mjs`; nothing else in the codebase writes a path.

| Key | Path | Request | Confirmed |
|---|---|---|---|
| `PCB_UPLOAD_GERBER` | `/overseas/openapi/pcb/uploadGerber` | multipart: `meta` + `file` | live |
| `PCB_AUDIT_GET` | `/overseas/openapi/pcb/audit/get` | `{key, language}` | live |
| `PCB_CALCULATE` | `/overseas/openapi/pcb/calculate` | `{orderType, pcbParam, fileKey, country, postCode, city, shippingMethod, achieveDate}` | live |
| `PCB_CREATE` | `/overseas/openapi/pcb/create` | `{fileKey, orderType, pcbParam, shippingAddress, billingAddress, …}` | **SDK only — never probed** |
| `PCB_ORDER_DETAIL` | `/overseas/openapi/pcb/order/detail` | `{batchNum}` | live |
| `PCB_WIP_GET` | `/overseas/openapi/pcb/wip/get` | `{orderUUID}` | live |
| `PCB_IMPEDANCE_TEMPLATES` | `/overseas/openapi/pcb/getImpedanceTemplateSettingList` | `{stencilLayer, stencilPly, cuprumThickness, …}` | live |
| `PCB_STEEL_PRICE_CONFIG` | `/overseas/openapi/pcb/getSteelPriceConfig` | **GET**, no parameters | live |
| `PCB_UPLOAD_BLIND_VIA_IMG` | `/overseas/openapi/pcb/uploadBlindViaHoleImg` | multipart | live |
| `COMPONENT_DETAIL_BY_CODE` | `/overseas/openapi/component/getComponentDetailByCode` | `{componentCodes:[…]}` | live |
| `COMPONENT_LIBRARY_LIST` | `/overseas/openapi/component/getComponentLibraryList` | `{currentPage, pageSize}` | live |
| `COMPONENT_PRIVATE_LIBRARY` | `/overseas/openapi/component/getPrivateComponentLibrary` | `{currentPage, pageSize}` | live |
| `COMPONENT_INFOS` | `/overseas/openapi/component/getComponentInfos` | `{lastKey}` | live |

`PCB_CREATE` is the one path deliberately left unprobed: it is the call that
buys boards, and confirming it is not worth any chance of placing an order by
accident. Its existence rests on the SDK.

`getSteelPriceConfig` being a GET contradicts the docs' "only supported via the
POST method". The SDK and the live platform both say GET, so the docs are wrong
about that too.

A path can be corrected without a code change — the credentials file may set
`JLCPCB_PATH_<KEY>` for any key in the table.

## Confirmed / assumed / blocked

**Confirmed.** The endpoint host is `https://open.jlcpcb.com`
(`api.jlcpcb.com` is the docs site; a client pointed there gets HTML). The
signature algorithm, offline and live. The Authorization header format. The
multipart meta rule. Business errors inside a 200. All thirteen paths above
except `PCB_CREATE`. The request field names, from JLC's own SDK models.

**Assumed.** Every *response* shape. No call has returned data, so the
normalizers in `jlcpcb-pcb.mjs` read a set of plausible field names, record
which one matched in `foundKeys`, and always return the untouched `raw`.
Nothing downstream should assume a normalized field exists. When the first real
response arrives, `foundKeys` says whether the guesses landed and each fix is
one array. Also assumed: `language: 1` on pre-review (an integer in the SDK
with no documented values), and millimetres for pre-review dimensions —
`normalizePreReview` sets `unitAssumed` so a caller can tell.

**Blocked.** Everything, on API scope. The application exists and authenticates;
it has no business types enabled. In the JLCPCB API Platform console, enable the
PCB and Components business types for the application (the console calls this
"apply for open business type" and it may need JLC's approval). Then run the
smoke test:

```js
import { createJlcPcb } from "./viewer/src/server/circuit/jlcpcb-pcb.mjs";
await createJlcPcb().smokeTest();   // GET getSteelPriceConfig, no parameters
```

It spends nothing and uploads nothing, and it proves signing, routing and
permission in one call. After that, the first real test is a gerber zip through
`reviewGerberZip()` with `expected` dimensions — the pre-review path end to
end.

Also unresolved: JLC's API list names a balance endpoint that is absent from
the SDK, so its path is unknown and it is not in the table. An IP whitelist can
be configured per application; nothing suggests one is set, but a sudden block
from a new machine would point there. The SDK also carries RSA fields for
encrypting personal data in addresses (`{encrypted}` prefix, OAEP-SHA1) — not
implemented, and it may matter for the shipping address on a real order.

## Ordering rules

Placing an order spends real money and cannot be undone, so the rules are
enforced in code rather than trusted to the caller.

**Nothing retries.** A create-order call that times out after the server
accepted it would, on retry, buy the boards twice — and the timeout looks
identical either way. `jlcpcb.test.mjs` asserts one request in, one request
out, on every failure shape.

**A confirmation must name the numbers.** `assertOrderConfirmed` requires the
quantity and the total price to be repeated back and to match both each other
and the payload's own `pcbParam.qty`. A bare `{confirmed:true}` is refused, and
so is a truthy-but-not-true value. A UI cannot confirm an order whose price it
never showed. Since JLC's create payload carries no price, pass the `quote` the
order came from and the total is checked against it.

**Every attempt is journalled before it is sent.** `~/.config/autonomous-circuit/jlcpcb-orders.jsonl`,
append-only, chmod 600, outside the repo. An attempt whose answer never arrived
is recorded as `unknown`, and a later attempt to place the same order — same
payload, same fingerprint — is refused until a person checks their JLCPCB
account and calls `resolveUnknown`. An order the platform explicitly rejected is
recorded as `failed` and may be retried freely, because in that case it
definitively does not exist.

## Files

| File | What it is |
|---|---|
| `jlcpcb.mjs` | Signed transport: signing, headers, credentials, JSON and multipart, response handling |
| `jlcpcb-endpoints.mjs` | The path table and how each path is known |
| `jlcpcb-pcb.mjs` | Operations: upload, pre-review, compare, quote, order, track |
| `jlcpcb-order-journal.mjs` | The confirmation gate and the append-only attempt journal |
| `jlcpcb.test.mjs`, `jlcpcb-pcb.test.mjs` | 70 tests, no network |

Credentials live at `~/.config/autonomous-circuit/jlcpcb.env`, chmod 600,
outside the repo. They must never appear in the repo, a fixture, a commit
message or a log line.

Nothing is wired into the UI yet. The order path should not reach a button
until a real order has been placed and tracked from a script.
