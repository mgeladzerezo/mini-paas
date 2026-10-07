const TOKEN_KEY = "mini-paas.token";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token === null) {
    localStorage.removeItem(TOKEN_KEY);
  } else {
    localStorage.setItem(TOKEN_KEY, token);
  }
}

let onUnauthorized: () => void = () => {};

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

interface ValidationIssue {
  loc?: (string | number)[];
  msg?: string;
}

/** FastAPI returns `detail` as a string for HTTP errors and as a list for validation errors. */
export function errorMessage(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null || !("detail" in body)) {
    return fallback;
  }
  const detail = body.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = (detail as ValidationIssue[]).map((issue) => {
      const field = (issue.loc ?? []).filter((part) => part !== "body").join(".");
      const message = issue.msg ?? "invalid";
      return field ? `${field}: ${message}` : message;
    });
    return messages.join("; ") || fallback;
  }
  return fallback;
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) {
    return undefined as T;
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    // A 401 on a request that carried a token means the session expired.
    if (response.status === 401 && token) {
      onUnauthorized();
    }
    throw new ApiError(
      response.status,
      errorMessage(payload, `Request failed (${response.status})`),
    );
  }
  return payload as T;
}
