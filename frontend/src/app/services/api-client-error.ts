export type ApiClientErrorKind = "transport" | "timeout" | "http";

/** Error metadata for callers that must distinguish commit ambiguity. */
export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly kind: ApiClientErrorKind,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}
