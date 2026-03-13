interface AgentStaleImageSummaryHost {
  agent_id: string;
  agent_name: string;
  status: string;
  stale_image_count: number;
  inventory_refreshed_at: string | null;
  inventory_error: string | null;
}

export interface AgentStaleImageSummaryResponse {
  hosts: AgentStaleImageSummaryHost[];
  total_stale_images: number;
  affected_agents: number;
}
