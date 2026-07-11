import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL("https://grokmcp.org"),
  title: {
    default: "UniGrok — One Grok gateway for every coding agent",
    template: "%s · UniGrok",
  },
  description: "The open-source, local-first MCP gateway that lets coding agents share server-side access to xAI Grok.",
  openGraph: {
    title: "UniGrok — One Grok gateway for every coding agent",
    description: "A local-first universal MCP gateway for xAI Grok models.",
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "UniGrok — one Grok gateway for every coding agent",
      },
    ],
    siteName: "UniGrok",
    type: "website",
    url: "https://grokmcp.org",
  },
  twitter: {
    card: "summary_large_image",
    title: "UniGrok — Universal Grok MCP",
    description: "One shared, server-side Grok gateway for your coding agents.",
    images: ["/og.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
