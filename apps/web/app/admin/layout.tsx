import type { ReactNode } from "react";
import { DM_Sans } from "next/font/google";

const dmSans = DM_Sans({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "600", "700"],
  display: "swap",
});

export default function AdminLayout({ children }: { children: ReactNode }) {
  return <div className={dmSans.className}>{children}</div>;
}
