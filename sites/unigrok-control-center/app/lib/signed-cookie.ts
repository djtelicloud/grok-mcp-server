const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const MAX_SIGNED_COOKIE_BYTES = 8_192;

export type CookieOptions = {
  expires?: Date;
  httpOnly?: boolean;
  maxAge?: number;
  path?: string;
  sameSite?: "Lax" | "Strict";
  secure?: boolean;
};

export async function signCookiePayload(
  payload: unknown,
  secret: string,
): Promise<string> {
  const encodedPayload = base64UrlEncode(encoder.encode(JSON.stringify(payload)));
  if (encodedPayload.length > MAX_SIGNED_COOKIE_BYTES / 2) {
    throw new Error("Cookie payload is too large.");
  }
  const key = await importHmacKey(secret, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(encodedPayload));
  return `${encodedPayload}.${base64UrlEncode(new Uint8Array(signature))}`;
}

export async function verifyCookiePayload(
  value: string | null | undefined,
  secret: string,
): Promise<unknown | null> {
  if (!value || value.length > MAX_SIGNED_COOKIE_BYTES) return null;
  const segments = value.split(".");
  if (segments.length !== 2 || !segments.every(isBase64Url)) return null;

  let signature: Uint8Array;
  let payloadBytes: Uint8Array;
  try {
    signature = base64UrlDecode(segments[1]);
    payloadBytes = base64UrlDecode(segments[0]);
  } catch {
    return null;
  }
  if (signature.byteLength !== 32) return null;

  try {
    const key = await importHmacKey(secret, ["verify"]);
    const valid = await crypto.subtle.verify(
      "HMAC",
      key,
      Uint8Array.from(signature).buffer,
      encoder.encode(segments[0]),
    );
    if (!valid) return null;
    return JSON.parse(decoder.decode(payloadBytes)) as unknown;
  } catch {
    return null;
  }
}

export function readCookie(
  cookieHeader: string | null | undefined,
  name: string,
): string | null {
  if (!cookieHeader || cookieHeader.length > 16_384) return null;
  let matchedValue: string | null = null;
  for (const segment of cookieHeader.split(";")) {
    const separator = segment.indexOf("=");
    if (separator < 1) continue;
    if (segment.slice(0, separator).trim() !== name) continue;
    const value = segment.slice(separator + 1).trim();
    if (value.length > MAX_SIGNED_COOKIE_BYTES) return null;
    if (matchedValue !== null) return null;
    matchedValue = value;
  }
  return matchedValue;
}

export function serializeCookie(
  name: string,
  value: string,
  options: CookieOptions,
): string {
  if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name)) {
    throw new Error("Invalid cookie name.");
  }
  if (/[,;\r\n\u0000]/.test(value)) {
    throw new Error("Invalid cookie value.");
  }

  const attributes = [`${name}=${value}`];
  if (options.maxAge !== undefined) {
    attributes.push(`Max-Age=${Math.max(0, Math.trunc(options.maxAge))}`);
  }
  if (options.expires) attributes.push(`Expires=${options.expires.toUTCString()}`);
  attributes.push(`Path=${options.path ?? "/"}`);
  if (options.httpOnly !== false) attributes.push("HttpOnly");
  if (options.secure !== false) attributes.push("Secure");
  attributes.push(`SameSite=${options.sameSite ?? "Lax"}`);
  return attributes.join("; ");
}

export function randomBase64Url(byteLength = 32): string {
  if (!Number.isSafeInteger(byteLength) || byteLength < 16 || byteLength > 128) {
    throw new Error("Invalid random byte length.");
  }
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

export async function sha256Base64Url(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return base64UrlEncode(new Uint8Array(digest));
}

function importHmacKey(
  secret: string,
  usages: KeyUsage[],
): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { hash: "SHA-256", name: "HMAC" },
    false,
    usages,
  );
}

function isBase64Url(value: string): boolean {
  return value.length > 0 && /^[A-Za-z0-9_-]+$/.test(value);
}

function base64UrlEncode(value: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < value.length; offset += 8_192) {
    binary += String.fromCharCode(...value.subarray(offset, offset + 8_192));
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function base64UrlDecode(value: string): Uint8Array {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(value.replaceAll("-", "+").replaceAll("_", "/") + padding);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}
