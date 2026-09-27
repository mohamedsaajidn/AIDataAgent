import { request } from "./client";
import type { WhoamiResponse } from "@/lib/types/api";

export function getMe(userId: string): Promise<WhoamiResponse> {
  return request(`/users/me?${new URLSearchParams({ user_id: userId })}`);
}
