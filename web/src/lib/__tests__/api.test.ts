import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "../api";

/** Anything between the browser and Podarium can answer instead of the API -- a proxy
 *  error page, a tunnel timeout, a captive portal. Parsing those as JSON produced a raw
 *  "Unexpected token '<', "<!DOCTYPE "... is not valid JSON", which names neither the
 *  request nor the status and points at the wrong thing entirely. */
function respondWith(body: string, init: ResponseInit & { type?: string } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(body, {
        status: init.status ?? 200,
        statusText: init.statusText ?? "",
        headers: { "content-type": init.type ?? "text/html" },
      }),
    ),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("request", () => {
  it("reports the status when a proxy returns an HTML error page", async () => {
    respondWith("<!DOCTYPE html><html><body>Gateway timeout</body></html>", {
      status: 504,
      statusText: "Gateway Timeout",
    });

    await expect(api.feeds()).rejects.toThrow(ApiError);
    await expect(api.feeds()).rejects.toThrow(/504/);
    await expect(api.feeds()).rejects.toThrow(/an HTML page/);
    // The old failure mode, which said nothing about the request.
    await expect(api.feeds()).rejects.not.toThrow(/Unexpected token/);
  });

  it("names the path, so the failing request is identifiable", async () => {
    respondWith("<!DOCTYPE html><html></html>", { status: 502 });
    await expect(api.settings()).rejects.toThrow(/\/api\/settings/);
  });

  it("says so when a 200 carries a non-JSON body", async () => {
    // A proxy that swallows the request and answers cheerfully is the confusing case.
    respondWith("<!DOCTYPE html><html>login</html>", { status: 200 });
    await expect(api.queue()).rejects.toThrow(/Something between the browser and Podarium/);
  });

  it("still surfaces the API's own error envelope", async () => {
    respondWith(JSON.stringify({ error: { code: "http_401", message: "Not authenticated" } }), {
      status: 401,
      type: "application/json",
    });

    await expect(api.feeds()).rejects.toThrow("Not authenticated");
  });

  it("passes valid JSON through untouched", async () => {
    respondWith(JSON.stringify([{ id: 1 }]), { status: 200, type: "application/json" });
    await expect(api.feeds()).resolves.toEqual([{ id: 1 }]);
  });
});
