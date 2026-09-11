import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-empty-state',
  standalone: true,
  template: `<p class="empty-hint">{{ message }}</p>`,
  styles: [
    `
      .empty-hint {
        padding: 24px;
        color: #666;
        margin: 0;
      }
    `,
  ],
})
export class EmptyStateComponent {
  @Input({ required: true }) message!: string;
}
