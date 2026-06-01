/**
 * Thin HTTP client wrapping fetch with credentials: "include" for
 * cookie-based session auth.  All /api calls go through this module
 * so the auth cookie is always sent.
 */

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(url, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      // ignore JSON parse errors on error responses
    }
    throw new ApiError(res.status, detail);
  }

  // 204 No Content has no body
  if (res.status === 204) return undefined as T;

  return res.json() as Promise<T>;
}

export const api = {
  get<T>(url: string): Promise<T> {
    return request<T>(url);
  },
  post<T>(url: string, body?: unknown): Promise<T> {
    return request<T>(url, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
};
