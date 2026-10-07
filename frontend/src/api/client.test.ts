import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, errorMessage, setToken, setUnauthorizedHandler } from "./client";

function mockFetch(status: number, body?: unknown) {
  const fetchMock = vi.fn<typeof fetch>(async () => {
    const payload = body === undefined ? null : JSON.stringify(body);
    return new Response(payload, { status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(() => {});
});

describe("errorMessage", () => {
  it("uses a string detail as is", () => {
    expect(errorMessage({ detail: "App not found" }, "fallback")).toBe("App not found");
  });

  it("flattens validation errors and names the field", () => {
    const body = {
      detail: [
        { loc: ["body", "name"], msg: "String should match pattern" },
        { loc: ["body", "port"], msg: "Input should be greater than or equal to 1024" },
      ],
    };
    expect(errorMessage(body, "fallback")).toBe(
      "name: String should match pattern; port: Input should be greater than or equal to 1024",
    );
  });

  it("falls back when the body has no detail", () => {
    expect(errorMessage(null, "fallback")).toBe("fallback");
    expect(errorMessage({ message: "nope" }, "fallback")).toBe("fallback");
  });
});

describe("api", () => {
  it("sends the bearer token and a JSON body", async () => {
    setToken("token-123");
    const fetchMock = mockFetch(200, { id: 1 });

    await expect(api("POST", "/apps", { name: "web" })).resolves.toEqual({ id: 1 });

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/apps");
    expect(init?.method).toBe("POST");
    expect(init?.headers).toEqual({
      Authorization: "Bearer token-123",
      "Content-Type": "application/json",
    });
    expect(init?.body).toBe('{"name":"web"}');
  });

  it("omits the Authorization header when signed out", async () => {
    const fetchMock = mockFetch(200, []);
    await api("GET", "/apps");
    expect(fetchMock.mock.calls[0]![1]?.headers).toEqual({});
  });

  it("returns undefined for 204 responses", async () => {
    mockFetch(204);
    await expect(api("DELETE", "/apps/1")).resolves.toBeUndefined();
  });

  it("throws an ApiError carrying the status and the server's message", async () => {
    mockFetch(409, { detail: "An app with this name already exists" });
    const error = await api("POST", "/apps", {}).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe("An app with this name already exists");
  });

  it("signs the user out when a request with a token gets a 401", async () => {
    setToken("expired");
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    mockFetch(401, { detail: "Not authenticated" });

    await expect(api("GET", "/apps")).rejects.toThrow("Not authenticated");
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("does not treat a failed login as an expired session", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    mockFetch(401, { detail: "Invalid email or password" });

    await expect(api("POST", "/auth/login", {})).rejects.toThrow("Invalid email or password");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
