// Tiny fetch wrapper. Every call goes to the same-origin /api (proxied to the backend in
// dev, served by FastAPI in prod). Errors always come back as { error } so the UI can
// humanise them rather than throwing raw responses at the user.

export type ApiResult<T> = T & { error?: string };

async function request<T = any>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    return { error: "network error — is the ACE Studio server running?" } as ApiResult<T>;
  }
  const text = await res.text();
  if (!text) return (res.ok ? {} : { error: `server error ${res.status}` }) as ApiResult<T>;
  try {
    return JSON.parse(text) as ApiResult<T>;
  } catch {
    return { error: `server error ${res.status}` } as ApiResult<T>;
  }
}

async function upload<T = any>(path: string, form: FormData): Promise<ApiResult<T>> {
  let res: Response;
  try { res = await fetch(`/api${path}`, { method: "POST", body: form }); }
  catch { return { error: "network error — is the ACE Studio server running?" } as ApiResult<T>; }
  const text = await res.text();
  if (!text) return (res.ok ? {} : { error: `server error ${res.status}` }) as ApiResult<T>;
  try { return JSON.parse(text) as ApiResult<T>; } catch { return { error: text.slice(0, 200) } as ApiResult<T>; }
}

export const api = {
  get: <T = any>(path: string) => request<T>(path),
  post: <T = any>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  upload,
  delete: <T = any>(path: string) => request<T>(path, { method: "DELETE" }),
};

