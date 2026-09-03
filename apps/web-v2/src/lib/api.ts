import { supabase } from "./supabase";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8080").replace(/\/$/, "");
const DEFAULT_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly traceId: string | null
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, headers, ...requestOptions } = options;
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...requestOptions,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(requestOptions.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
        ...headers
      }
    });

    if (!response.ok) {
      const body = await response.json().catch(() => null) as { message?: string } | null;
      throw new ApiError(
        body?.message || `Request failed with status ${response.status}`,
        response.status,
        response.headers.get("X-Trace-Id")
      );
    }

    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("The request timed out. Please try again.", 408, null);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export async function apiPage<T>(path: string): Promise<{ items: T[]; nextCursor: string | null }> {
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: "application/json",
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {})
    }
  });
  if (!response.ok) {
    throw new ApiError(`Request failed with status ${response.status}`, response.status, response.headers.get("X-Trace-Id"));
  }
  return {
    items: await response.json() as T[],
    nextCursor: response.headers.get("X-Next-Cursor")
  };
}
