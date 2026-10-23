import type { Metadata, Viewport } from "next";
import "./globals.css";
import Shell from "@/components/Shell";

export const metadata: Metadata = {
  title: "PRAMAAN · Evidence verification",
  description: "Integrity and authenticity checks for digital evidence – images, CCTV, audio – with signed reports.",
  icons: { icon: [{ url: "/favicon.ico", sizes: "any" }, { url: "/logo-192.png", type: "image/png" }], apple: "/logo-192.png" },
};

export const viewport: Viewport = { themeColor: "#3a1538", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
