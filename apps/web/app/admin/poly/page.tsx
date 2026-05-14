import { redirect } from "next/navigation";

export default function AdminPolyRedirectPage() {
  redirect("/admin/editor?tab=poly");
}
