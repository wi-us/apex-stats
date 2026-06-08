import { QueryClient } from "@tanstack/react-query";
import { createRoute, createRouter } from "@tanstack/react-router";
import { lazy, Suspense, type ComponentType } from "react";

import { DevRootRoute } from "./dev-root-route";
import { Route as AcceptInviteRouteImport } from "./routes/accept-invite";
import { Route as AdminRouteImport } from "./routes/admin";
import { Route as AdminIndexRouteImport } from "./routes/admin.index";
import { Route as AdminMapsRouteImport } from "./routes/admin.maps";
import { Route as AdminMapsMapIdRouteImport } from "./routes/admin.maps.$mapId";
import { Route as AdminMatchesRouteImport } from "./routes/admin.matches";
import { Route as AdminMatchesMatchIdRouteImport } from "./routes/admin.matches.$matchId";
import { Route as AdminPolygonsRouteImport } from "./routes/admin.polygons";
import { Route as AdminProcessesRouteImport } from "./routes/admin.processes";
import { Route as AdminTeamsRouteImport } from "./routes/admin.teams";
import { Route as AdminTeamsTeamIdRouteImport } from "./routes/admin.teams.$teamId";
import { Route as AdminTournamentsRouteImport } from "./routes/admin.tournaments";
import { Route as AdminUsersRouteImport } from "./routes/admin.users";
import { Route as DocsRouteImport } from "./routes/docs";
import { Route as GamesRouteImport } from "./routes/games";
import { Route as GamesGameIdRouteImport } from "./routes/games.$gameId";
import { Route as IndexRouteImport } from "./routes/index";
import { Route as LoginRouteImport } from "./routes/login";
import { Route as MapsRouteImport } from "./routes/maps";
import { Route as MapsMapIdRouteImport } from "./routes/maps.$mapId";
import { Route as MatchesRouteImport } from "./routes/matches";
import { Route as MatchesMatchIdRouteImport } from "./routes/matches.$matchId";
import { Route as TeamsRouteImport } from "./routes/teams";
import { Route as TeamsTeamIdRouteImport } from "./routes/teams.$teamId";
import { Route as TournamentsRouteImport } from "./routes/tournaments";

type FileRouteModule = {
  Route: {
    options?: {
      component?: ComponentType;
    };
  };
};

function lazyFileRoute(importer: () => Promise<FileRouteModule>) {
  const LazyComponent = lazy(async () => {
    const mod = await importer();
    const Component = mod.Route.options?.component;
    if (!Component) throw new Error("Lazy route module has no component");
    return { default: Component };
  });

  return function LazyRouteComponent() {
    return (
      <Suspense fallback={<div className="p-6 text-xs uppercase tracking-wider text-muted-foreground">Loading...</div>}>
        <LazyComponent />
      </Suspense>
    );
  };
}

function rootRoute<T extends { update: (opts: unknown) => T }>(route: T, id: string, path: string) {
  return route.update({ id, path, getParentRoute: () => DevRootRoute } as any);
}

function childRoute<T extends { update: (opts: unknown) => T }>(route: T, id: string, path: string, parent: unknown) {
  return route.update({ id, path, getParentRoute: () => parent } as any);
}

function lazyChildRoute(path: string, parent: unknown, importer: () => Promise<FileRouteModule>) {
  return createRoute({
    path,
    getParentRoute: () => parent as never,
    component: lazyFileRoute(importer),
  });
}

const IndexRoute = rootRoute(IndexRouteImport, "/", "/");
const LoginRoute = rootRoute(LoginRouteImport, "/login", "/login");
const DocsRoute = rootRoute(DocsRouteImport, "/docs", "/docs");
const AcceptInviteRoute = rootRoute(AcceptInviteRouteImport, "/accept-invite", "/accept-invite");
const TournamentsRoute = rootRoute(TournamentsRouteImport, "/tournaments", "/tournaments");

const TeamsRoute = rootRoute(TeamsRouteImport, "/teams", "/teams");
const TeamsTeamIdRoute = childRoute(TeamsTeamIdRouteImport, "/$teamId", "/$teamId", TeamsRoute);

const MatchesRoute = rootRoute(MatchesRouteImport, "/matches", "/matches");
const MatchesMatchIdRoute = childRoute(MatchesMatchIdRouteImport, "/$matchId", "/$matchId", MatchesRoute);

const MapsRoute = rootRoute(MapsRouteImport, "/maps", "/maps");
const MapsMapIdRoute = childRoute(MapsMapIdRouteImport, "/$mapId", "/$mapId", MapsRoute);

const GamesRoute = rootRoute(GamesRouteImport, "/games", "/games");
const GamesGameIdRoute = childRoute(GamesGameIdRouteImport, "/$gameId", "/$gameId", GamesRoute);

const AdminRoute = rootRoute(AdminRouteImport, "/admin", "/admin");
const AdminIndexRoute = childRoute(AdminIndexRouteImport, "/", "/", AdminRoute);
const AdminMapsRoute = childRoute(AdminMapsRouteImport, "/maps", "/maps", AdminRoute);
const AdminMapsMapIdRoute = childRoute(AdminMapsMapIdRouteImport, "/$mapId", "/$mapId", AdminMapsRoute);
const AdminMatchesRoute = childRoute(AdminMatchesRouteImport, "/matches", "/matches", AdminRoute);
const AdminMatchesMatchIdRoute = childRoute(AdminMatchesMatchIdRouteImport, "/$matchId", "/$matchId", AdminMatchesRoute);
const AdminPolygonsRoute = childRoute(AdminPolygonsRouteImport, "/polygons", "/polygons", AdminRoute);
const AdminProcessesRoute = childRoute(AdminProcessesRouteImport, "/processes", "/processes", AdminRoute);
const AdminTeamsRoute = childRoute(AdminTeamsRouteImport, "/teams", "/teams", AdminRoute);
const AdminTeamsTeamIdRoute = childRoute(AdminTeamsTeamIdRouteImport, "/$teamId", "/$teamId", AdminTeamsRoute);
const AdminTournamentsRoute = childRoute(AdminTournamentsRouteImport, "/tournaments", "/tournaments", AdminRoute);
const AdminUsersRoute = childRoute(AdminUsersRouteImport, "/users", "/users", AdminRoute);

const AdminDatasetRoute = lazyChildRoute("/dataset", AdminRoute, () => import("./routes/admin.dataset"));
const AdminDiagramsRoute = lazyChildRoute("/diagrams", AdminRoute, () => import("./routes/admin.diagrams"));
const AdminHsvRoute = lazyChildRoute("/hsv", AdminRoute, () => import("./routes/admin.hsv"));
const AdminMinimapRoute = lazyChildRoute("/minimap", AdminRoute, () => import("./routes/admin.minimap"));
const AdminPoiRoute = lazyChildRoute("/poi", AdminRoute, () => import("./routes/admin.poi"));
const AdminSchemaRoute = lazyChildRoute("/schema", AdminRoute, () => import("./routes/admin.schema"));
const AdminTrackingLabRoute = lazyChildRoute("/tracking-lab", AdminRoute, () => import("./routes/admin.tracking-lab"));
const AdminZonesRoute = lazyChildRoute("/zones", AdminRoute, () => import("./routes/admin.zones"));

const routeTree = DevRootRoute._addFileChildren({
  IndexRoute,
  LoginRoute,
  DocsRoute,
  AcceptInviteRoute,
  TournamentsRoute,
  TeamsRoute: TeamsRoute._addFileChildren({ TeamsTeamIdRoute }),
  MatchesRoute: MatchesRoute._addFileChildren({ MatchesMatchIdRoute }),
  MapsRoute: MapsRoute._addFileChildren({ MapsMapIdRoute }),
  GamesRoute: GamesRoute._addFileChildren({ GamesGameIdRoute }),
  AdminRoute: AdminRoute._addFileChildren({
    AdminIndexRoute,
    AdminDatasetRoute,
    AdminDiagramsRoute,
    AdminHsvRoute,
    AdminMapsRoute: AdminMapsRoute._addFileChildren({ AdminMapsMapIdRoute }),
    AdminMatchesRoute: AdminMatchesRoute._addFileChildren({ AdminMatchesMatchIdRoute }),
    AdminMinimapRoute,
    AdminPoiRoute,
    AdminPolygonsRoute,
    AdminProcessesRoute,
    AdminSchemaRoute,
    AdminTeamsRoute: AdminTeamsRoute._addFileChildren({ AdminTeamsTeamIdRoute }),
    AdminTournamentsRoute,
    AdminTrackingLabRoute,
    AdminUsersRoute,
    AdminZonesRoute,
  }),
} as any);

export function getDevRouter() {
  const queryClient = new QueryClient();
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultSsr: false,
    scrollRestoration: true,
    defaultPreloadStaleTime: 0,
  });
}
