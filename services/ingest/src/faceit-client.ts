import axios from "axios";
import { FaceitMatchMetadata } from "./types";

export class FaceitClient {
  private readonly baseUrl = process.env.FACEIT_API_URL ?? "https://open.faceit.com/data/v4";

  constructor(private readonly apiKey: string) {}

  async getMatchMetadata(matchId: string): Promise<FaceitMatchMetadata> {
    const response = await axios.get(`${this.baseUrl}/matches/${matchId}`, {
      headers: { Authorization: `Bearer ${this.apiKey}` }
    });

    const payload = response.data;
    const teams = Object.values(payload.teams ?? {}).flatMap((entry: any) => [
      { id: entry.team_id, name: entry.nickname }
    ]);

    return {
      matchId,
      tournamentName: payload.competition_name ?? "Unknown tournament",
      mapName: payload.voting?.map?.pick?.[0] ?? "mp_storm_point",
      teams,
      vodUrl: payload.demo_url ?? ""
    };
  }
}
