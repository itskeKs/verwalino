// /api/meta has no file safety cap; quota upload_bytes is null in selfhost.
// Keep aligned with backend/app/core/document_limits.py:MAX_UPLOAD_BYTES.
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
