import { ChangeDetectionStrategy, Component, Input } from "@angular/core";
import { CommonModule } from "@angular/common";

export type IconName =
  | "bell"
  | "building"
  | "bed"
  | "search"
  | "map-pin"
  | "chart"
  | "check"
  | "chevron-down"
  | "alert"
  | "refresh"
  | "trash";

/**
 * The app's entire line-icon set as inline SVG templates (spec §7: no emoji,
 * unified stroke). Add icons here, never as emoji or per-component SVG.
 */
@Component({
  selector: "app-icon",
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [ngSwitch]="name" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
         [attr.width]="size" [attr.height]="size" aria-hidden="true">
      <g *ngSwitchCase="'bell'"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></g>
      <g *ngSwitchCase="'building'"><rect x="4" y="3" width="16" height="18" rx="1"/><path d="M9 7h1m4 0h1M9 11h1m4 0h1M9 15h1m4 0h1M10 21v-3h4v3"/></g>
      <g *ngSwitchCase="'bed'"><path d="M3 7v11m0-4h18m0 4v-7a2 2 0 0 0-2-2H8"/><circle cx="6" cy="9.5" r="1"/></g>
      <g *ngSwitchCase="'search'"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></g>
      <g *ngSwitchCase="'map-pin'"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/></g>
      <g *ngSwitchCase="'chart'"><path d="M3 3v18h18"/><path d="m7 15 4-5 3 3 5-7"/></g>
      <g *ngSwitchCase="'check'"><path d="m5 13 4 4L19 7"/></g>
      <g *ngSwitchCase="'chevron-down'"><path d="m6 9 6 6 6-6"/></g>
      <g *ngSwitchCase="'alert'"><circle cx="12" cy="12" r="9"/><path d="M12 8v4m0 4h.01"/></g>
      <g *ngSwitchCase="'refresh'"><path d="M3 12a9 9 0 0 1 15.4-6.4L21 8m0-5v5h-5M21 12a9 9 0 0 1-15.4 6.4L3 16m0 5v-5h5"/></g>
      <g *ngSwitchCase="'trash'"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V6"/></g>
    </svg>
  `,
  // Without this every consumer inherits the inline line-box's phantom
  // descender space and reaches for its own vertical-align fix.
  styles: [":host { display: inline-flex; } svg { display: block; }"],
})
export class IconComponent {
  @Input({ required: true }) name!: IconName;
  @Input() size = 20;
}
