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

function financialInputMessage(detail: { blockers?: { code: string }[] }): string {
  const codes = detail.blockers?.map((blocker) => blocker.code) ?? [];
  if (codes.includes("linked_plan_not_current") || codes.includes("linked_plan_scope_mismatch")) return "Bitte einen aktuellen finalisierten Wirtschaftsplan für dasselbe Objekt und denselben Zeitraum verwenden.";
  if (codes.includes("derived_total_stale")) return "Zahlungen wurden geändert. Vorauszahlungen zuerst neu aus den Zahlungen ermitteln und anschließend die Datengrundlage bestätigen.";
  if (codes.includes("unsupported_derived_subperiod")) return "Für diesen Teilzeitraum sind belegte manuelle Vorauszahlungen erforderlich. Jahreszahlungen können nicht automatisch zugeordnet werden.";
  if (codes.includes("owner_payment_coverage_missing")) return "Für jeden Eigentümer muss ein geprüfter Zahlungsbetrag angegeben sein, auch bei 0,00.";
  if (codes.includes("bank_balance_coverage_missing")) return "Bitte für jedes Objektkonto einen geprüften Kontostand angeben, auch bei 0,00.";
  return "Datengrundlage unvollständig oder geändert. Bitte Beträge und Zeitraum prüfen und die Vollständigkeit erneut bestätigen.";
}

function withRequestId(response: Response, detail: string): string {
  const requestId = response.headers.get("X-Request-ID");
  return response.status >= 500 && requestId && /^[A-Za-z0-9._-]{8,128}$/.test(requestId)
    ? `${detail} (Request-ID: ${requestId})`
    : detail;
}

async function request<T>(
  url: string,
  options: RequestInit = {},
  responseType: "json" | "blob" = "json",
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
      if (body.detail) {
        if (typeof body.detail === "string") detail = body.detail;
        else if (body.detail.code === "financial_inputs_incomplete") {
          detail = financialInputMessage(body.detail);
        } else detail = "Eingaben unvollständig oder ungültig. Bitte die angegebenen Felder prüfen.";
      }
    } catch {
      // ignore JSON parse errors on error responses
    }
    throw new ApiError(res.status, withRequestId(res, detail));
  }

  // 204 No Content has no body
  if (res.status === 204 && responseType === "json") return undefined as T;

  return (responseType === "blob" ? res.blob() : res.json()) as Promise<T>;
}

export const api = {
  getBlob(url: string, options?: RequestInit): Promise<Blob> {
    return request<Blob>(url, options, "blob");
  },
  get<T>(url: string, options?: RequestInit): Promise<T> {
    return request<T>(url, options);
  },
  post<T>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
    return request<T>(url, {
      ...options,
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
  put<T>(url: string, body?: unknown): Promise<T> {
    return request<T>(url, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
  patch<T>(url: string, body?: unknown, options?: RequestInit): Promise<T> {
    return request<T>(url, {
      ...options,
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
    });
  },
  delete<T>(url: string, options?: RequestInit): Promise<T> {
    return request<T>(url, { ...options, method: "DELETE" });
  },
};

/**
 * Upload a file via multipart/form-data.
 * Does NOT set Content-Type header -- the browser sets it with the boundary.
 */
export async function uploadFormData<T>(
  url: string,
  formData: FormData,
): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    body: formData,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body.detail) {
        if (typeof body.detail === "string") detail = body.detail;
        else if (body.detail.code === "financial_inputs_incomplete") {
          detail = financialInputMessage(body.detail);
        } else detail = "Eingaben unvollständig oder ungültig. Bitte die angegebenen Felder prüfen.";
      }
    } catch {
      // ignore
    }
    throw new ApiError(res.status, withRequestId(res, detail));
  }

  return res.json() as Promise<T>;
}
