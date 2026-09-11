import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule } from '@angular/common/http/testing';

import { PipelineService, PipelineLogEvent } from './pipeline.service';

/**
 * Contract tests for the SSE log stream, written before D12 moves the EventSource
 * plumbing out of its two copies (pipeline-runs-page and postprocess-log-dialog) into
 * the service. The behaviour pinned here is exactly what both copies implemented:
 *
 * - each SSE message is a log line;
 * - the backend emits the byte offset as the SSE `id`, and a reconnect resumes from it
 *   so output is not replayed;
 * - the terminal `status` event ends the stream;
 * - closing the consumer closes the connection — which, once the consumer is a
 *   subscription tied to `takeUntilDestroyed`, is what makes a leak on navigation
 *   structurally impossible.
 */

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private readonly listeners = new Map<string, Array<(ev: Event) => void>>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: (ev: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }

  close(): void {
    this.closed = true;
  }

  emitLine(data: string, lastEventId = ''): void {
    this.onmessage?.({ data, lastEventId } as MessageEvent);
  }

  emitStatus(data: string): void {
    for (const fn of this.listeners.get('status') ?? []) fn({ data } as unknown as Event);
  }

  emitError(): void {
    this.onerror?.();
  }
}

describe('PipelineService.streamLogs', () => {
  let service: PipelineService;
  const realEventSource = (globalThis as Record<string, unknown>)['EventSource'];

  beforeEach(() => {
    FakeEventSource.instances = [];
    (globalThis as Record<string, unknown>)['EventSource'] = FakeEventSource;
    TestBed.configureTestingModule({ imports: [HttpClientTestingModule] });
    service = TestBed.inject(PipelineService);
  });

  afterEach(() => {
    (globalThis as Record<string, unknown>)['EventSource'] = realEventSource;
  });

  const current = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];

  function collect(id = 'run-1', offset = 0) {
    const events: PipelineLogEvent[] = [];
    let complete = false;
    const sub = service.streamLogs(id, offset).subscribe({
      next: (e) => events.push(e),
      complete: () => (complete = true),
    });
    return { events, sub, isComplete: () => complete };
  }

  it('emits each SSE message as a log line', () => {
    const { events } = collect();

    current().emitLine('first');
    current().emitLine('second');

    expect(events).toEqual([
      { kind: 'line', data: 'first' },
      { kind: 'line', data: 'second' },
    ]);
    expect(current().url).toBe('/api/pipeline/runs/run-1/logs');
  });

  it('asks the backend to resume from a byte offset', () => {
    collect('run-1', 42);
    expect(current().url).toBe('/api/pipeline/runs/run-1/logs?offset=42');
  });

  it('ends the stream and closes the connection on the terminal status event', () => {
    const { events, isComplete } = collect();

    current().emitStatus('failed');

    expect(events).toEqual([{ kind: 'status', data: 'failed' }]);
    expect(isComplete()).toBe(true);
    expect(current().closed).toBe(true);
  });

  it('reconnects from the last received byte offset so output is not replayed', () => {
    const { events } = collect();
    current().emitLine('a', '17');

    const first = current();
    first.emitError();

    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(current().url).toBe('/api/pipeline/runs/run-1/logs?offset=17');

    current().emitLine('b');
    expect(events).toEqual([
      { kind: 'line', data: 'a' },
      { kind: 'line', data: 'b' },
    ]);
  });

  it('falls back to the initial offset when no id has arrived yet', () => {
    collect('run-1', 7);

    current().emitError();

    expect(current().url).toBe('/api/pipeline/runs/run-1/logs?offset=7');
  });

  it('closes the connection on unsubscribe and never reconnects', () => {
    const { sub } = collect();
    const source = current();

    sub.unsubscribe();

    expect(source.closed).toBe(true);
    source.emitError();
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
