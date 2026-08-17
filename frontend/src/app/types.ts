export interface NavItem {
  id: string;
  path: string;
  label: string;
  icon: string;
  ready: boolean;
}
export interface NavGroup {
  group: string;
  items: NavItem[];
}
