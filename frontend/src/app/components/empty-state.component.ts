import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from "@angular/core";
import { CommonModule } from "@angular/common";

import { IconComponent, IconName } from "./icon.component";

/**
 * Replaces fake-zero screens (spec §6): an icon, a title, an explanation of
 * WHY there is nothing here yet, and optionally the one action that fixes it.
 * Never render a metric as "0" when the truth is "no data yet".
 */
@Component({
  selector: "app-empty-state",
  standalone: true,
  imports: [CommonModule, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="empty-state">
      <app-icon [name]="icon" [size]="28"></app-icon>
      <strong>{{ title }}</strong>
      <p>{{ explanation }}</p>
      <button *ngIf="actionLabel" class="secondary-button" type="button" (click)="action.emit()">
        {{ actionLabel }}
      </button>
    </div>
  `,
  styles: [`
    .empty-state {
      display: flex; flex-direction: column; align-items: center; gap: 0.5rem;
      padding: 1.5rem 1rem; text-align: center; color: #475569;
    }
    .empty-state strong { color: var(--foreground); }
    .empty-state p { margin: 0; max-width: 32rem; font-size: 0.9rem; }
  `],
})
export class EmptyStateComponent {
  @Input({ required: true }) icon!: IconName;
  @Input({ required: true }) title!: string;
  @Input({ required: true }) explanation!: string;
  @Input() actionLabel = "";
  @Output() action = new EventEmitter<void>();
}
