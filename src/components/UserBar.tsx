import { Link, useRouterState } from "@tanstack/react-router";
import { useAuth } from "@/lib/auth";
import { ThemeToggle } from "@/components/ThemeToggle";
import { DensityToggle } from "@/components/DensityToggle";

export function UserBar() {
  const { user, signOut } = useAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  if (pathname.startsWith("/login") || pathname.startsWith("/accept-invite")) return null;

  const inAdmin = pathname.startsWith("/admin");

  return (
    <div className="fixed right-4 top-3 z-50 flex items-center gap-2">
      <ThemeToggle compact />
      <DensityToggle compact />
      {user ? (
        <>
          <Link
            to={inAdmin ? "/" : "/admin"}
            className="text-mono rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs uppercase tracking-wider hover:bg-muted"
          >
            {inAdmin ? "Main" : "Admin"}
          </Link>
          <button
            onClick={() => signOut()}
            className="text-mono rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs uppercase tracking-wider hover:bg-muted"
          >
            Sign out
          </button>
        </>
      ) : (
        <Link
          to="/login"
          search={{ redirect: pathname }}
          className="text-mono rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-xs uppercase tracking-wider hover:bg-muted"
        >
          Sign in
        </Link>
      )}
    </div>
  );
}
