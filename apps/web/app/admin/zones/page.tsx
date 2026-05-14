import { redirect } from "next/navigation";

export default function AdminZonesRedirectPage() {
  redirect("/admin/editor?tab=zones");
}
