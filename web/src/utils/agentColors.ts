const AGENT_COLORS = ['#3b82f6', '#8b5cf6', '#ec4899', '#f97316', '#14b8a6', '#84cc16'];

export const getAgentColor = (agentId: string): string => {
  if (!agentId) return '#a8a29e';
  const hash = agentId.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  return AGENT_COLORS[hash % AGENT_COLORS.length];
};

export const getAgentInitials = (name: string): string => {
  if (!name) return '?';
  const words = name.split(/[-_\s]+/);
  return words.length > 1
    ? (words[0][0] + words[1][0]).toUpperCase()
    : name.substring(0, 2).toUpperCase();
};
