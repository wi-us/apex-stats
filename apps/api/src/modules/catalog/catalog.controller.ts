import { Body, Controller, Get, NotFoundException, Param, Put, Query, Res } from "@nestjs/common";
import { Response } from "express";
import { CatalogService } from "./catalog.service";
import { TextZonesPayload, ZonesPayload } from "./catalog.types";

@Controller("catalog")
export class CatalogController {
  constructor(private readonly catalogService: CatalogService) {}

  @Get("tournaments")
  getTournaments() {
    return this.catalogService.getTournaments();
  }

  @Get("tournaments/:tournamentId/matches")
  getTournamentMatches(@Param("tournamentId") tournamentId: string) {
    return this.catalogService.getMatches(tournamentId);
  }

  @Get("matches/:matchId/maps")
  getMatchMaps(@Param("matchId") matchId: string) {
    return this.catalogService.getMaps(matchId);
  }

  @Get("teams")
  getTeams() {
    return this.catalogService.getTeams();
  }

  @Get("maps/assets")
  getMapAssets() {
    return this.catalogService.listMapAssets();
  }

  @Get("maps/:mapId/teams")
  getTeamsForMap(@Param("mapId") mapId: string) {
    return this.catalogService.getTeamsForMap(mapId);
  }

  @Get("maps/:mapId/tracks")
  getMapTracks(
    @Param("mapId") mapId: string,
    @Query("teamIds") teamIdsCsv?: string,
    @Query("fromSec") fromSecRaw?: string,
    @Query("toSec") toSecRaw?: string
  ) {
    const teamIds = teamIdsCsv ? teamIdsCsv.split(",").map((item) => item.trim()) : undefined;
    const fromSec = fromSecRaw !== undefined ? Number(fromSecRaw) : undefined;
    const toSec = toSecRaw !== undefined ? Number(toSecRaw) : undefined;
    return this.catalogService.getTracks(mapId, teamIds, fromSec, toSec);
  }

  @Get("maps/:mapId/rings")
  getMapRings(
    @Param("mapId") mapId: string,
    @Query("fromSec") fromSecRaw?: string,
    @Query("toSec") toSecRaw?: string
  ) {
    const fromSec = fromSecRaw !== undefined ? Number(fromSecRaw) : undefined;
    const toSec = toSecRaw !== undefined ? Number(toSecRaw) : undefined;
    return this.catalogService.getRings(mapId, fromSec, toSec);
  }

  @Get("maps/:mapId/background")
  getMapBackground(@Param("mapId") mapId: string, @Res() res: Response) {
    const filePath = this.catalogService.getMapBackgroundPath(mapId);
    if (!filePath) {
      throw new NotFoundException("Map background not found. Run analysis first.");
    }
    return res.sendFile(filePath);
  }

  @Get("maps/:mapId/admin-config")
  getMapAdminConfig(@Param("mapId") mapId: string) {
    return this.catalogService.getMapAdminConfig(mapId);
  }

  @Put("maps/:mapId/admin-config")
  updateMapAdminConfig(@Param("mapId") mapId: string, @Body() payload: Record<string, unknown>) {
    return this.catalogService.updateMapAdminConfig(mapId, payload);
  }

  @Get("maps/:mapId/zones")
  getMapZones(@Param("mapId") mapId: string) {
    return this.catalogService.getMapZones(mapId);
  }

  @Put("maps/:mapId/zones")
  updateMapZones(@Param("mapId") mapId: string, @Body() payload: ZonesPayload) {
    return this.catalogService.updateMapZones(mapId, payload);
  }

  @Get("maps/:mapId/text-zones")
  getMapTextZones(@Param("mapId") mapId: string) {
    return this.catalogService.getMapTextZones(mapId);
  }

  @Put("maps/:mapId/text-zones")
  updateMapTextZones(@Param("mapId") mapId: string, @Body() payload: TextZonesPayload) {
    return this.catalogService.updateMapTextZones(mapId, payload);
  }
}
