import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

// Safety fallback for unavailable or older metadata. The backend remains authoritative.
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export function useUploadLimit() {
  const [limit, setLimit] = useState(MAX_UPLOAD_BYTES);
  const pending = useRef<Promise<{ max_upload_bytes?: number }> | null>(null);
  useEffect(() => {
    let cancelled = false;
    pending.current ??= api.get<{ max_upload_bytes?: number }>("/api/meta", { cache: "no-store" });
    pending.current.then((metadata) => {
      const cap = metadata.max_upload_bytes;
      if (!cancelled && typeof cap === "number" && Number.isSafeInteger(cap) && cap > 0) setLimit(cap);
    }, () => { /* Keep the safety fallback when metadata is unavailable. */ });
    return () => { cancelled = true; };
  }, []);
  return limit;
}
