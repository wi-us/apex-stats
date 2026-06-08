import { createServerFn } from "@tanstack/react-start";
import { requireSupabaseAuth } from "@/integrations/supabase/auth-middleware";
import { supabaseAdmin } from "@/integrations/supabase/client.server";
import { z } from "zod";

async function assertAdmin(userId: string) {
  const { data, error } = await supabaseAdmin
    .from("user_roles")
    .select("role")
    .eq("user_id", userId)
    .eq("role", "administrator")
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) throw new Error("Forbidden: administrator role required");
}

const CreateInput = z.object({
  email: z.string().email().max(255),
  password: z.string().min(8).max(128),
  display_name: z.string().min(1).max(120).optional(),
  role: z.enum(["user", "operator", "administrator"]),
});

export const listUserAccounts = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .handler(async ({ context }) => {
    await assertAdmin(context.userId);
    const [{ data: profiles, error: pErr }, { data: roles, error: rErr }] = await Promise.all([
      supabaseAdmin.from("profiles").select("id, email, display_name, created_at"),
      supabaseAdmin.from("user_roles").select("user_id, role"),
    ]);
    if (pErr || rErr) throw new Error(pErr?.message ?? rErr?.message ?? "Failed to load users");

    const roleMap = new Map<string, "user" | "operator" | "administrator">();
    const rank = { user: 1, operator: 2, administrator: 3 } as const;
    for (const r of roles ?? []) {
      const next = r.role as "user" | "operator" | "administrator";
      const cur = roleMap.get(r.user_id);
      if (!cur || rank[next] > rank[cur]) roleMap.set(r.user_id, next);
    }
    return {
      users: (profiles ?? []).map((p) => ({
        ...p,
        role: roleMap.get(p.id) ?? null,
      })),
    };
  });

export const createUserAccount = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((d: unknown) => CreateInput.parse(d))
  .handler(async ({ data, context }) => {
    await assertAdmin(context.userId);

    const { data: created, error } = await supabaseAdmin.auth.admin.createUser({
      email: data.email,
      password: data.password,
      email_confirm: true,
      user_metadata: data.display_name ? { display_name: data.display_name } : undefined,
    });
    if (error || !created.user) throw new Error(error?.message ?? "Failed to create user");

    // Default 'user' role is inserted by trigger. Replace if a different role was requested.
    if (data.role !== "user") {
      await supabaseAdmin.from("user_roles").delete().eq("user_id", created.user.id);
      const { error: rErr } = await supabaseAdmin
        .from("user_roles")
        .insert({ user_id: created.user.id, role: data.role });
      if (rErr) throw new Error(rErr.message);
    }
    return { id: created.user.id };
  });

const SetRoleInput = z.object({
  user_id: z.string().uuid(),
  role: z.enum(["user", "operator", "administrator"]),
});

export const setUserRole = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((d: unknown) => SetRoleInput.parse(d))
  .handler(async ({ data, context }) => {
    await assertAdmin(context.userId);
    await supabaseAdmin.from("user_roles").delete().eq("user_id", data.user_id);
    const { error } = await supabaseAdmin
      .from("user_roles")
      .insert({ user_id: data.user_id, role: data.role });
    if (error) throw new Error(error.message);
    return { ok: true };
  });

const DeleteInput = z.object({ user_id: z.string().uuid() });

export const deleteUserAccount = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((d: unknown) => DeleteInput.parse(d))
  .handler(async ({ data, context }) => {
    await assertAdmin(context.userId);
    if (data.user_id === context.userId) throw new Error("You cannot delete your own account.");
    const { error } = await supabaseAdmin.auth.admin.deleteUser(data.user_id);
    if (error) throw new Error(error.message);
    return { ok: true };
  });
