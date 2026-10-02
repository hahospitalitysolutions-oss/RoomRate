import { Component, signal } from "@angular/core";
import { CommonModule } from "@angular/common";

import { IconComponent } from "../components/icon.component";
import { SetupProgressService } from "../services/setup-progress.service";
import { WorkflowStorageService } from "../services/workflow-storage.service";

/**
 * Collapsible progress bar at the top of the map (spec §5). Each open step
 * explains WHY it matters. At 5/5 it hides permanently (per user, via
 * WorkflowStorageService). Thin progress bar, no checkbox glyphs (spec §7).
 */
@Component({
  selector: "app-setup-checklist",
  standalone: true,
  imports: [CommonModule, IconComponent],
  template: `
    <section
      *ngIf="!hidden()"
      class="setup-checklist"
      data-testid="setup-checklist"
    >
      <button class="checklist-header" type="button" [attr.aria-expanded]="!collapsed()" (click)="collapsed.set(!collapsed())">
        <span data-testid="checklist-progress">{{ progress.doneCount() }} από {{ progress.steps().length }} βήματα</span>
        <span class="checklist-bar"><span class="checklist-bar-fill" [style.width.%]="(progress.doneCount() / progress.steps().length) * 100"></span></span>
        <app-icon name="chevron-down" [size]="16" [class.chevron-collapsed]="collapsed()"></app-icon>
      </button>
      <ul *ngIf="!collapsed()" class="checklist-steps">
        <li *ngFor="let step of progress.steps()" [class.step-done]="step.done">
          <app-icon *ngIf="step.done" name="check" [size]="14"></app-icon>
          <div>
            <strong [attr.data-testid]="step.id === progress.nextOpenStep()?.id ? 'checklist-open-step' : null">
              {{ step.label }}
            </strong>
            <p *ngIf="!step.done" class="muted">{{ step.why }}</p>
          </div>
        </li>
      </ul>
    </section>
  `,
  styles: [`
    .setup-checklist { background: #fff; border: 1px solid #e2e8f0; border-radius: 0.5rem; margin-bottom: 0.75rem; }
    .checklist-header { width: 100%; display: flex; align-items: center; gap: 0.75rem; padding: 0.6rem 0.9rem; background: none; border: none; cursor: pointer; }
    .checklist-bar { flex: 1; height: 4px; border-radius: 2px; background: #e2e8f0; overflow: hidden; }
    .checklist-bar-fill { display: block; height: 100%; background: #0f766e; transition: width 0.3s; }
    .checklist-header .chevron-collapsed { transform: rotate(-90deg); }
    .checklist-steps { list-style: none; margin: 0; padding: 0.25rem 0.9rem 0.75rem; display: flex; flex-direction: column; gap: 0.4rem; }
    .checklist-steps li { display: flex; gap: 0.5rem; align-items: baseline; }
    .checklist-steps p { margin: 0.1rem 0 0; font-size: 0.82rem; }
    .step-done strong { color: #64748b; font-weight: 500; }
  `],
})
export class SetupChecklistComponent {
  // Angular CSR only (no SSR here), so `window` is always available. Small
  // screens default collapsed so the checklist doesn't eat most of the map.
  readonly collapsed = signal(window.matchMedia("(max-width: 820px)").matches);

  constructor(
    readonly progress: SetupProgressService,
    private readonly workflow: WorkflowStorageService,
  ) {}

  hidden(): boolean {
    if (this.workflow.get("checklistHidden") === "1") {
      return true;
    }
    if (this.progress.complete()) {
      // 5/5: hide permanently for this user (spec §5).
      this.workflow.set("checklistHidden", "1");
      return true;
    }
    return false;
  }
}
