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

function genToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

const CreateInput = z.object({
  email: z.string().email().max(255).optional().nullable(),
  role: z.enum(["user", "operator", "administrator"]),
  expires_in_days: z.number().int().min(1).max(30).default(7),
  never_expires: z.boolean().optional().default(false),
  max_uses: z.number().int().min(1).max(1000).default(1),
});

export const createInvite = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((d: unknown) => CreateInput.parse(d))
  .handler(async ({ data, context }) => {
    await assertAdmin(context.userId);
    const token = genToken();
    const expires_at = new Date(
      Date.now() + (data.never_expires ? 36500 : data.expires_in_days) * 24 * 60 * 60 * 1000,
    ).toISOString();
    const { data: row, error } = await supabaseAdmin
      .from("invites")
      .insert({
        email: data.email ?? null,
        role: data.role,
        token,
        expires_at,
        max_uses: data.max_uses,
        created_by: context.userId,
      })
      .select()
      .single();
    if (error) throw new Error(error.message);
    return { invite: row };
  });

export const listInvites = createServerFn({ method: "GET" })
  .middleware([requireSupabaseAuth])
  .handler(async ({ context }) => {
    await assertAdmin(context.userId);
    const { data, error } = await supabaseAdmin
      .from("invites")
      .select("*")
      .order("created_at", { ascending: false });
    if (error) throw new Error(error.message);
    return { invites: data ?? [] };
  });

const DeleteInput = z.object({ id: z.string().uuid() });

export const deleteInvite = createServerFn({ method: "POST" })
  .middleware([requireSupabaseAuth])
  .inputValidator((d: unknown) => DeleteInput.parse(d))
  .handler(async ({ data, context }) => {
    await assertAdmin(context.userId);
    const { error } = await supabaseAdmin.from("invites").delete().eq("id", data.id);
    if (error) throw new Error(error.message);
    return { ok: true };
  });

const LookupInput = z.object({ token: z.string().min(16).max(128) });

export const lookupInvite = createServerFn({ method: "POST" })
  .inputValidator((d: unknown) => LookupInput.parse(d))
  .handler(async ({ data }) => {
    const { data: row, error } = await supabaseAdmin
      .from("invites")
      .select("id, email, role, expires_at, used_at, max_uses, uses_count")
      .eq("token", data.token)
      .maybeSingle();
    if (error) throw new Error(error.message);
    if (!row) return { status: "invalid" as const };
    if ((row.uses_count ?? 0) >= (row.max_uses ?? 1)) return { status: "used" as const };
    if (new Date(row.expires_at).getTime() < Date.now())
      return { status: "expired" as const };
    return {
      status: "ok" as const,
      email: row.email ?? null,
      role: row.role,
      remaining: (row.max_uses ?? 1) - (row.uses_count ?? 0),
    };
  });

const AcceptInput = z.object({
  token: z.string().min(16).max(128),
  email: z.string().email().max(255),
  password: z.string().min(8).max(128),
  display_name: z.string().min(1).max(120).optional(),
});

export const acceptInvite = createServerFn({ method: "POST" })
  .inputValidator((d: unknown) => AcceptInput.parse(d))
  .handler(async ({ data }) => {
    const { data: row, error } = await supabaseAdmin
      .from("invites")
      .select("*")
      .eq("token", data.token)
      .maybeSingle();
    if (error) throw new Error(error.message);
    if (!row) throw new Error("Invalid invite token.");
    if ((row.uses_count ?? 0) >= (row.max_uses ?? 1))
      throw new Error("This invite has reached its usage limit.");
    if (new Date(row.expires_at).getTime() < Date.now())
      throw new Error("This invite has expired.");

    // If invite was issued for a specific email, enforce it
    const acceptEmail = row.email ?? data.email;
    if (row.email && row.email.toLowerCase() !== data.email.toLowerCase()) {
      throw new Error("This invite link is bound to a different email address.");
    }

    const { data: created, error: cErr } = await supabaseAdmin.auth.admin.createUser({
      email: acceptEmail,
      password: data.password,
      email_confirm: true,
      user_metadata: data.display_name ? { display_name: data.display_name } : undefined,
    });
    if (cErr || !created.user) throw new Error(cErr?.message ?? "Failed to create account.");

    if (row.role !== "user") {
      await supabaseAdmin.from("user_roles").delete().eq("user_id", created.user.id);
      const { error: rErr } = await supabaseAdmin
        .from("user_roles")
        .insert({ user_id: created.user.id, role: row.role });
      if (rErr) throw new Error(rErr.message);
    }

    const newCount = (row.uses_count ?? 0) + 1;
    const isFull = newCount >= (row.max_uses ?? 1);
    await supabaseAdmin
      .from("invites")
      .update({
        uses_count: newCount,
        used_at: isFull ? new Date().toISOString() : row.used_at,
      })
      .eq("id", row.id);

    return { ok: true, email: acceptEmail };
  });
