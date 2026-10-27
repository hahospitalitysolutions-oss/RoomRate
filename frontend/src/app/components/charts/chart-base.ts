/**
 * What every SVG chart shares: it draws at the width it really has (a
 * ResizeObserver on the host), so text stays at its CSS size from a 360px
 * phone to a desktop card instead of shrinking with a fixed viewBox; and one
 * "active" index, set by hover, tap or keyboard focus, that opens the tooltip.
 */
import { afterNextRender, DestroyRef, Directive, ElementRef, inject, signal } from "@angular/core";

@Directive()
export abstract class ChartBase {
  /** Drawing width in px (the host's content width). */
  readonly width = signal(560);
  /** The datum under the pointer or the keyboard focus; null closes the tooltip. */
  readonly active = signal<number | null>(null);

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      const element = this.host.nativeElement;
      const measure = () => {
        const width = Math.floor(element.clientWidth);
        if (width > 0 && width !== this.width()) this.width.set(width);
      };
      measure();
      if (typeof ResizeObserver === "undefined") return;
      const observer = new ResizeObserver(measure);
      observer.observe(element);
      destroyRef.onDestroy(() => observer.disconnect());
    });
  }

  /** Narrow screens get fewer ticks and labels. */
  protected compact(): boolean {
    return this.width() < 480;
  }

  show(index: number): void {
    this.active.set(index);
  }

  hide(index: number): void {
    if (this.active() === index) this.active.set(null);
  }

  /** Hover opens the readout for a mouse only: on touch the tap decides. */
  enter(event: PointerEvent, index: number): void {
    if (event.pointerType === "mouse") this.show(index);
  }

  leave(event: PointerEvent, index: number): void {
    if (event.pointerType === "mouse") this.hide(index);
  }

  /** A tap toggles, so a phone can close the readout again; a mouse click keeps it open. */
  tap(event: MouseEvent, index: number): void {
    const pointerType = (event as PointerEvent).pointerType;
    if (pointerType && pointerType !== "mouse") {
      this.active.set(this.active() === index ? null : index);
    } else {
      this.show(index);
    }
  }
}
