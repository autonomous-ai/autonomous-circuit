export const BOARD_GROUPS = Object.freeze([
  { id: 'prompts', label: 'Prompts', defaultTab: 'prompt-current' },
  { id: 'electric', label: 'Electric', defaultTab: 'overview' },
  { id: 'case', label: 'Case', defaultTab: 'case-overview' },
]);
export const BOARD_TABS = Object.freeze([
  { id: 'prompt-current', label: 'Current prompt', group: 'prompts' },
  { id: 'prompt-original', label: 'Original prompt', group: 'prompts' },
  { id: 'overview', label: 'Overview', group: 'electric' },
  { id: 'function', label: 'What it does', group: 'electric' },
  { id: 'split', label: 'Side by side', group: 'electric' },
  { id: 'schematic', label: 'Schematic', group: 'electric' },
  { id: 'pcb', label: 'PCB', group: 'electric' },
  { id: '3d', label: '3D', group: 'electric' },
  { id: 'bom', label: 'Parts', group: 'electric' },
  { id: 'fab', label: 'Files', group: 'electric' },
  { id: 'firmware', label: 'Firmware', group: 'electric' },
  { id: 'case-overview', label: 'Overview', group: 'case' },
  { id: 'case', label: '3D & Print', group: 'case' },
]);
export function groupForTab(tab) {
  return BOARD_TABS.find(item => item.id === tab)?.group || 'electric';
}
