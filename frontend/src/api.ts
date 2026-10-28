function serviceToken() {
  const current = localStorage.getItem("memory-slice-token");
  if (current !== null) return current;
  // Preserve existing browser authentication across the product rename.
  const legacy = localStorage.getItem("shiyi-token");
  if (legacy !== null) {
    localStorage.setItem("memory-slice-token", legacy);
    localStorage.removeItem("shiyi-token");
  }
  return legacy;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = serviceToken();
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let text = `请求失败 (${response.status})`;
    try {
      const data = await response.json();
      text =
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail);
    } catch {
      /* non JSON proxy error */
    }
    if (response.status === 401) window.dispatchEvent(new Event("memory-slice-auth"));
    throw new Error(text);
  }
  return response.json();
}
export const post = <T>(
  path: string,
  body: unknown,
  options: RequestInit = {},
) => api<T>(path, { ...options, method: "POST", body: JSON.stringify(body) });
export const put = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export async function downloadMemory(spaceId: string) {
  const token = serviceToken();
  const r = await fetch(`/api/v1/spaces/${spaceId}/export`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!r.ok) throw new Error("导出失败，请检查服务连接");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = "memory-slice.zip";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}
export const percent = (x: number) => Math.round(x * 100) + "%";
export const day = (x: string | null) =>
  x
    ? new Date(x).toLocaleDateString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        year: "numeric",
      })
    : "时间未知";
export const localInput = (x: string) => {
  const d = new Date(x);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
