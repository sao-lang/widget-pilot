// M02 §4.3：packages/sdk/ts/client.ts —— REST 客户端（生成物骨架，M02 后续由 gen 覆写）
import type { ApiResponse } from "./types";

export async function request<T>(base: string, method: string, path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const r = await fetch(base + path, { method, ...init });
  return (await r.json()) as ApiResponse<T>;
}
