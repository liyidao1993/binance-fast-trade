import axios, { AxiosError, AxiosInstance } from "axios";

/** 统一响应 {code, data, msg} */
export interface ApiEnvelope<T> {
  code: number;
  data: T;
  msg: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public raw?: unknown,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export const api: AxiosInstance = axios.create({
  baseURL: "/api/v1",
  withCredentials: true, // 带 session cookie
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

// 响应拦截器：把 { code, data } 解包 + 统一错误处理
api.interceptors.response.use(
  (resp) => resp,
  (err: AxiosError<any>) => {
    const status = err.response?.status ?? 0;
    const body = err.response?.data as any;
    const detail =
      (typeof body === "object" && body && (body.detail || body.msg)) ||
      err.message ||
      "请求失败";
    return Promise.reject(new ApiError(status, String(detail), body));
  },
);

/** 统一解包 helper：取 data 字段，失败把 msg 当错误抛出 */
export async function unwrap<T>(promise: Promise<{ data: ApiEnvelope<T> }>): Promise<T> {
  const resp = await promise;
  const env = resp.data;
  if (!env || typeof env !== "object") {
    throw new ApiError(0, "响应体非标准 envelope");
  }
  if (env.code !== 0) {
    throw new ApiError(0, env.msg || `code=${env.code}`);
  }
  return env.data;
}
