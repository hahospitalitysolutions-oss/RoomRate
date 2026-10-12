/**
 * The app's dropdown: a styled trigger and option panel in place of the
 * operating system's native list (owner feedback 2026-10-05: the Windows
 * popup looked foreign next to the rest of the page).
 *
 * A real `<select>` stays underneath, visually hidden but in the DOM, so a
 * wrapping `<label>` still names it, forms and e2e (`selectOption`,
 * `toHaveValue`, `getByLabel`) keep working, and a change made through it
 * reaches the page exactly like a pick from the panel. It is out of the tab
 * order: the trigger button is the keyboard entry point.
 *
 * The panel is position: fixed and measured from the trigger, so the
 * scrolling sidebars it lives in cannot clip it; it flips upward when there
 * is no room below and closes on outside click, Escape, scroll or resize.
 */
import { CommonModule } from "@angular/common";
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  computed,
  input,
  output,
  signal,
  viewChild,
} from "@angular/core";

import { IconComponent } from "./icon.component";

export type SelectMenuOption = {
  value: string;
  /** The native option's text (what forms and tests see). */
  label: string;
  /** Shown in the panel and the trigger instead of `label`, when set. */
  display?: string;
  /** A short secondary tag shown beside the option, e.g. a room category. */
  hint?: string;
};

let nextMenuId = 0;

@Component({
  selector: "app-select-menu",
  standalone: true,
  imports: [CommonModule, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <select
      class="select-menu-native"
      tabindex="-1"
      [attr.id]="selectId() || null"
      [attr.name]="name() || null"
      [attr.data-testid]="testId() || null"
      [value]="value()"
      (change)="pick($any($event.target).value)"
    >
      <option *ngIf="placeholder()" value="" [selected]="!value()">{{ placeholder() }}</option>
      <option *ngFor="let option of options()" [value]="option.value" [selected]="option.value === value()">{{ option.label }}</option>
    </select>
    <button
      #trigger
      type="button"
      class="select-menu-trigger"
      [class.is-open]="open()"
      [class.is-placeholder]="!selected()"
      [class.is-emphasis]="emphasis()"
      aria-haspopup="listbox"
      [attr.aria-expanded]="open()"
      [attr.aria-controls]="listId"
      [attr.aria-activedescendant]="open() && activeIndex() >= 0 ? listId + '-' + activeIndex() : null"
      (click)="toggle()"
      (keydown)="onTriggerKey($event)"
    >
      <span class="select-menu-value">
        <span class="select-menu-text">{{ selected() ? (selected()!.display || selected()!.label) : placeholder() }}</span>
        <span *ngIf="selected()?.hint" class="select-menu-hint">{{ selected()!.hint }}</span>
      </span>
      <app-icon class="select-menu-chevron" name="chevron-down" [size]="18"></app-icon>
    </button>
    <ul
      *ngIf="open()"
      #panel
      class="select-menu-panel"
      role="listbox"
      [id]="listId"
      [class.opens-up]="opensUp()"
      [style.left.px]="panelRect().left"
      [style.width.px]="panelRect().width"
      [style.top.px]="opensUp() ? null : panelRect().top"
      [style.bottom.px]="opensUp() ? panelRect().bottom : null"
      [style.max-height.px]="panelRect().maxHeight"
    >
      <li
        *ngFor="let option of options(); index as i"
        role="option"
        class="select-menu-option"
        [id]="listId + '-' + i"
        [attr.aria-selected]="option.value === value()"
        [class.is-active]="i === activeIndex()"
        [class.is-selected]="option.value === value()"
        (mouseenter)="activeIndex.set(i)"
        (mousedown)="$event.preventDefault()"
        (click)="pick(option.value)"
      >
        <span class="select-menu-option-text">{{ option.display || option.label }}</span>
        <span *ngIf="option.hint" class="select-menu-hint">{{ option.hint }}</span>
        <app-icon *ngIf="option.value === value()" class="select-menu-check" name="check" [size]="16"></app-icon>
      </li>
      <li *ngIf="!options().length" class="select-menu-empty">Δεν υπάρχουν επιλογές</li>
    </ul>
  `,
})
export class SelectMenuComponent {
  readonly options = input.required<SelectMenuOption[]>();
  readonly value = input<string>("");
  readonly placeholder = input<string>("");
  /** Put on the hidden native select, where tests have always looked for it. */
  readonly testId = input<string>("");
  /** For a `<label for>` outside the component, and form field names. */
  readonly selectId = input<string>("");
  readonly name = input<string>("");
  /** Larger, bolder trigger for a page's primary control. */
  readonly emphasis = input<boolean>(false);
  readonly valueChange = output<string>();

  readonly listId = `select-menu-${nextMenuId++}`;
  readonly open = signal(false);
  readonly activeIndex = signal(-1);
  readonly opensUp = signal(false);
  readonly panelRect = signal({ left: 0, width: 0, top: 0, bottom: 0, maxHeight: 320 });
  readonly selected = computed(() => this.options().find((option) => option.value === this.value()) ?? null);

  private readonly trigger = viewChild<ElementRef<HTMLButtonElement>>("trigger");
  private readonly panel = viewChild<ElementRef<HTMLUListElement>>("panel");
  private typeahead = "";
  private typeaheadTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly host: ElementRef<HTMLElement>) {}

  toggle(): void {
    if (this.open()) {
      this.close();
    } else {
      this.openPanel();
    }
  }

  pick(value: string): void {
    this.close();
    if (value !== this.value()) {
      this.valueChange.emit(value);
    }
    this.trigger()?.nativeElement.focus();
  }

  onTriggerKey(event: KeyboardEvent): void {
    const count = this.options().length;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        if (!this.open()) {
          this.openPanel();
          return;
        }
        const step = event.key === "ArrowDown" ? 1 : -1;
        this.moveActive(Math.min(count - 1, Math.max(0, this.activeIndex() + step)));
        return;
      }
      case "Home":
      case "End":
        if (this.open()) {
          event.preventDefault();
          this.moveActive(event.key === "Home" ? 0 : count - 1);
        }
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        if (this.open() && this.activeIndex() >= 0) {
          this.pick(this.options()[this.activeIndex()].value);
        } else {
          this.toggle();
        }
        return;
      case "Escape":
      case "Tab":
        if (this.open()) {
          if (event.key === "Escape") {
            event.preventDefault();
          }
          this.close();
        }
        return;
      default:
        if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
          this.typeAhead(event.key);
        }
    }
  }

  @HostListener("document:mousedown", ["$event"])
  onDocumentMouseDown(event: MouseEvent): void {
    if (this.open() && !this.host.nativeElement.contains(event.target as Node)) {
      this.close();
    }
  }

  @HostListener("window:resize")
  onResize(): void {
    this.close();
  }

  onScroll(event: Event): void {
    // Scrolling inside the panel itself is the user reading the list.
    if (this.open() && !this.panel()?.nativeElement.contains(event.target as Node)) {
      this.close();
    }
  }

  private openPanel(): void {
    const button = this.trigger()?.nativeElement;
    if (!button) {
      return;
    }
    const rect = button.getBoundingClientRect();
    const gap = 6;
    const margin = 12;
    const below = window.innerHeight - rect.bottom - gap - margin;
    const above = rect.top - gap - margin;
    const upward = below < 220 && above > below;
    this.opensUp.set(upward);
    this.panelRect.set({
      left: rect.left,
      width: Math.max(rect.width, 220),
      top: rect.bottom + gap,
      bottom: window.innerHeight - rect.top + gap,
      maxHeight: Math.max(160, Math.min(360, upward ? above : below)),
    });
    const current = this.options().findIndex((option) => option.value === this.value());
    this.activeIndex.set(current >= 0 ? current : 0);
    this.open.set(true);
    // Capture-phase, so scrolling any ancestor (the sidebars scroll) closes it.
    window.addEventListener("scroll", this.scrollListener, true);
    queueMicrotask(() => this.scrollActiveIntoView());
  }

  private close(): void {
    if (!this.open()) {
      return;
    }
    this.open.set(false);
    window.removeEventListener("scroll", this.scrollListener, true);
  }

  private readonly scrollListener = (event: Event) => this.onScroll(event);

  private moveActive(index: number): void {
    this.activeIndex.set(index);
    queueMicrotask(() => this.scrollActiveIntoView());
  }

  private scrollActiveIntoView(): void {
    const panel = this.panel()?.nativeElement;
    const active = panel?.querySelector<HTMLElement>(".select-menu-option.is-active");
    active?.scrollIntoView({ block: "nearest" });
  }

  private typeAhead(char: string): void {
    this.typeahead += char.toLocaleLowerCase("el");
    if (this.typeaheadTimer) {
      clearTimeout(this.typeaheadTimer);
    }
    this.typeaheadTimer = setTimeout(() => (this.typeahead = ""), 600);
    const index = this.options().findIndex((option) =>
      (option.display || option.label).toLocaleLowerCase("el").startsWith(this.typeahead),
    );
    if (index < 0) {
      return;
    }
    if (!this.open()) {
      this.openPanel();
    }
    this.moveActive(index);
  }
}
