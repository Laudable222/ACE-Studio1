export interface NavItem {
  id: string;
  path: string;
  label: string;
  icon: string;
  ready: boolean;
  description?: string;
}
export interface NavGroup {
  group: string;
  items: NavItem[];
}
