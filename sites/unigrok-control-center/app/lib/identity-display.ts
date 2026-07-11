export function safeDisplayName(value: string): string {
  const normalized = value
    .normalize("NFKC")
    .replace(/[\u0000-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/g, "")
    .trim()
    .slice(0, 80);
  return normalized || "ChatGPT user";
}
