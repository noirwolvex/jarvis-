import test from "node:test";
import assert from "node:assert/strict";
import { GET as sessionGet } from "../src/app/api/session/route.ts";
import { POST as pairPost } from "../src/app/api/pair/route.ts";
import { assertControlSession, CONTROL_SESSION_COOKIE, issueControlSession } from "../src/lib/control-session.ts";

const origin = "http://127.0.0.1:3000";
const token = "recovery-fixture-0123456789-abcdefghijklmnopqrstuvwxyz";

async function withRuntime(run: () => Promise<void>) {
  const previous = {
    token: process.env.JARVIS_CONTROL_PAIRING_TOKEN,
    mode: process.env.JARVIS_CONTROL_MODE,
  };
  process.env.JARVIS_CONTROL_PAIRING_TOKEN = token;
  process.env.JARVIS_CONTROL_MODE = "hybrid";
  try {
    await run();
  } finally {
    for (const [key, value] of [
      ["JARVIS_CONTROL_PAIRING_TOKEN", previous.token],
      ["JARVIS_CONTROL_MODE", previous.mode],
    ] as const) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function statusRequest(headers: Record<string, string> = {}) {
  return new Request(`${origin}/api/session`, { headers: { host: "127.0.0.1:3000", ...headers } });
}

function pairingRequest(body = JSON.stringify({ token }), overrides: Record<string, string | null> = {}) {
  const headers = new Headers({
    host: "127.0.0.1:3000", origin, "content-type": "application/json", "x-jarvis-control": "pair",
  });
  for (const [name, value] of Object.entries(overrides)) {
    if (value === null) headers.delete(name);
    else headers.set(name, value);
  }
  return new Request(`${origin}/api/pair`, { method: "POST", headers, body });
}

test("session status distinguishes an unpaired tab from a signed session without minting credentials", async () => {
  await withRuntime(async () => {
    const cookie = `${CONTROL_SESSION_COOKIE}=${issueControlSession()}`;
    for (const [headers, authenticated] of [
      [{}, false],
      [{ cookie: `${CONTROL_SESSION_COOKIE}=forged.signature` }, false],
      [{ cookie }, true],
    ] as const) {
      const response = await sessionGet(statusRequest(headers));
      assert.equal(response.status, 200);
      assert.deepEqual(await response.json(), { authenticated, pairingAvailable: true });
      assert.equal(response.headers.get("set-cookie"), null);
      assert.match(response.headers.get("cache-control") || "", /no-store/);
    }
  });
});

test("a runtime restart invalidates the previous cookie and explicit pairing repairs the browser session", async () => {
  await withRuntime(async () => {
    const staleCookie = `${CONTROL_SESSION_COOKIE}=${issueControlSession()}`;
    process.env.JARVIS_CONTROL_PAIRING_TOKEN = `${token}-new-runtime`;
    const stale = await sessionGet(statusRequest({ cookie: staleCookie }));
    assert.equal(stale.status, 200);
    assert.deepEqual(await stale.json(), { authenticated: false, pairingAvailable: true });
    assert.equal(stale.headers.get("set-cookie"), null);

    const oldTokenAttempt = await pairPost(pairingRequest());
    assert.equal(oldTokenAttempt.status, 403);
    assert.equal(oldTokenAttempt.headers.get("set-cookie"), null);

    const repaired = await pairPost(pairingRequest(JSON.stringify({ token: process.env.JARVIS_CONTROL_PAIRING_TOKEN }), { cookie: staleCookie }));
    assert.equal(repaired.status, 200);
    const cookie = (repaired.headers.get("set-cookie") || "").split(";")[0]!;
    const status = await sessionGet(statusRequest({ cookie }));
    assert.deepEqual(await status.json(), { authenticated: true, pairingAvailable: true });
    assert.doesNotThrow(() => assertControlSession(statusRequest({ cookie })));
  });
});

test("same-origin token pairing issues only an HttpOnly signed cookie and a nonsecret response", async () => {
  await withRuntime(async () => {
    const response = await pairPost(pairingRequest());
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { ok: true });
    const setCookie = response.headers.get("set-cookie") || "";
    assert.match(setCookie, new RegExp(`^${CONTROL_SESSION_COOKIE}=[^;]+;`));
    assert.match(setCookie, /;\s*HttpOnly(?:;|$)/i);
    assert.match(setCookie, /;\s*SameSite=Strict(?:;|$)/i);
    assert.match(setCookie, /;\s*Path=\/(?:;|$)/i);
    assert.ok(!setCookie.includes(token));
    assert.equal(response.headers.get("location"), null);
    assert.match(response.headers.get("cache-control") || "", /no-store/);
    assert.doesNotThrow(() => assertControlSession(statusRequest({ cookie: setCookie.split(";")[0]! })));
  });
});

test("pairing rejects hostile origins, missing proof, and forged credentials without setting cookies", async () => {
  await withRuntime(async () => {
    const cases: Array<[string, Record<string, string | null>]> = [
      [JSON.stringify({ token: "wrong" }), {}],
      [JSON.stringify({ token }), { "x-jarvis-control": null }],
      [JSON.stringify({ token }), { "x-jarvis-control": "hybrid" }],
      [JSON.stringify({ token }), { origin: null }],
      [JSON.stringify({ token }), { origin: "https://attacker.invalid" }],
      [JSON.stringify({ token }), { origin: "http://localhost:3000" }],
      [JSON.stringify({ token }), { host: null }],
      [JSON.stringify({ token }), { host: "attacker.invalid:3000" }],
      [JSON.stringify({ token }), { "sec-fetch-site": "cross-site" }],
      [JSON.stringify({ token: "" }), { cookie: `${CONTROL_SESSION_COOKIE}=forged.signature` }],
    ];
    for (const [body, headers] of cases) {
      const response = await pairPost(pairingRequest(body, headers));
      assert.equal(response.status, 403, JSON.stringify(headers));
      assert.equal(response.headers.get("set-cookie"), null);
      assert.ok(!(await response.text()).includes(token));
    }
  });
});

test("pairing accepts only a bounded JSON object containing the token", async () => {
  await withRuntime(async () => {
    const cases: Array<[string, Record<string, string | null>]> = [
      ["{", {}], ["null", {}], ["[]", {}], ["{}", {}],
      [JSON.stringify({ token: 123 }), {}],
      [JSON.stringify({ token, authenticated: true }), {}],
      [JSON.stringify({ token, redirect: "https://attacker.invalid" }), {}],
      [JSON.stringify({ token }), { "content-type": "text/plain" }],
      [JSON.stringify({ token }), { "content-type": null }],
      [`${JSON.stringify({ token })}${" ".repeat(1024)}`, {}],
    ];
    for (const [body, headers] of cases) {
      const response = await pairPost(pairingRequest(body, headers));
      assert.ok(response.status >= 400 && response.status < 500, `Unexpected status ${response.status}`);
      assert.equal(response.headers.get("set-cookie"), null);
    }
  });
});

test("session discovery neither exposes tokens nor grants remote access", async () => {
  await withRuntime(async () => {
    const hostileHeaders: Array<Record<string, string>> = [
      { host: "" },
      { host: "attacker.invalid:3000" },
      { origin: "https://attacker.invalid" },
      { "sec-fetch-site": "cross-site" },
    ];
    for (const headers of hostileHeaders) {
      const response = await sessionGet(statusRequest(headers));
      assert.equal(response.status, 403);
      assert.equal(response.headers.get("set-cookie"), null);
      assert.ok(!(await response.text()).includes(token));
    }
    delete process.env.JARVIS_CONTROL_PAIRING_TOKEN;
    const unavailable = await sessionGet(statusRequest());
    assert.equal(unavailable.status, 200);
    assert.deepEqual(await unavailable.json(), { authenticated: false, pairingAvailable: false });
    assert.equal(unavailable.headers.get("set-cookie"), null);
  });
});
