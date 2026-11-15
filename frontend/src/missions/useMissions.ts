import { useQuery } from "@tanstack/react-query";

import { api } from "../api";

export function useMissions() {
  return useQuery({ queryKey: ["missions"], queryFn: api.missions, refetchInterval: 2000, staleTime: 0 });
}
