import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: "https://grokmcp.org", changeFrequency: "weekly", priority: 1 },
    { url: "https://grokmcp.org/contribute", changeFrequency: "weekly", priority: 0.8 },
  ];
}
