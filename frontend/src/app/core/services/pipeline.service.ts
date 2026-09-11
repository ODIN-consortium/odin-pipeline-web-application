import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

import {
  ExtractTargetOptions,
  MergeDecisionPayload,
  MergeDecisionStatus,
  MpoxOptions,
  PipelineLaunchPayload,
  PipelineRun,
  PostprocessStarted,
} from '../models/pipeline.model';

/** One event from a pipeline log stream: a log line, or the terminal run status. */
export interface PipelineLogEvent {
  kind: 'line' | 'status';
  /** The log line, or for `status` the run's final state (`done`, `failed`, …). */
  data: string;
}

@Injectable({ providedIn: 'root' })
export class PipelineService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/pipeline';

  /** Launch a new pipeline run. Returns 201 with the created PipelineRun. */
  launch(payload: PipelineLaunchPayload): Observable<PipelineRun> {
    return this.http.post<PipelineRun>(`${this.base}/runs`, payload, { observe: 'body' });
  }

  /** List all pipeline runs, newest first. */
  list(): Observable<PipelineRun[]> {
    return this.http.get<PipelineRun[]>(`${this.base}/runs`);
  }

  /** List runs filtered by type and/or status. */
  listFiltered(params: { pipeline_type?: string; status?: string }): Observable<PipelineRun[]> {
    let p = new HttpParams();
    if (params.pipeline_type) p = p.set('pipeline_type', params.pipeline_type);
    if (params.status) p = p.set('status', params.status);
    return this.http.get<PipelineRun[]>(`${this.base}/runs`, { params: p });
  }

  /**
   * Get the currently active (running/queued) pipeline run.
   *
   * Not nullable: the endpoint answers 404 when nothing is running, so "none" arrives as an
   * error, not as an empty body. Typing it `PipelineRun | null` would describe a response the
   * backend never sends.
   */
  active(): Observable<PipelineRun> {
    return this.http.get<PipelineRun>(`${this.base}/runs/active`);
  }

  /** Get a single pipeline run by ID. */
  get(id: string): Observable<PipelineRun> {
    return this.http.get<PipelineRun>(`${this.base}/runs/${id}`);
  }

  /** Cancel a running or queued pipeline run. */
  cancel(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/runs/${id}`);
  }

  /**
   * Stream a run's log over SSE.
   *
   * Owns the whole EventSource lifecycle, which used to be copy-pasted into every
   * consumer: it reconnects after a dropped connection from the last received byte
   * offset (the backend emits it as the SSE `id`) so output is not replayed,
   * completes after the terminal `status` event, and closes the connection on
   * unsubscribe — so a subscription tied to `takeUntilDestroyed` cannot leak an
   * open connection on navigation.
   */
  streamLogs(id: string, offset = 0): Observable<PipelineLogEvent> {
    return new Observable<PipelineLogEvent>((subscriber) => {
      let source: EventSource;
      let lastOffset = offset;
      const connect = () => {
        const url =
          lastOffset > 0
            ? `${this.base}/runs/${id}/logs?offset=${lastOffset}`
            : `${this.base}/runs/${id}/logs`;
        source = new EventSource(url);
        source.onmessage = (ev) => {
          if (ev.lastEventId) lastOffset = parseInt(ev.lastEventId, 10);
          subscriber.next({ kind: 'line', data: ev.data });
        };
        source.addEventListener('status', (ev: Event) => {
          source.close();
          subscriber.next({ kind: 'status', data: (ev as MessageEvent).data });
          subscriber.complete();
        });
        source.onerror = () => {
          source.close();
          if (!subscriber.closed) connect();
        };
      };
      connect();
      return () => source.close();
    });
  }

  // ── Merge decisions ────────────────────────────────────────────────────────

  getMergeCandidates(runAccession: string): Observable<MergeDecisionStatus> {
    return this.http.get<MergeDecisionStatus>(
      `${this.base}/nanopore/${runAccession}/merge-candidates`,
    );
  }

  setMergeDecision(runAccession: string, payload: MergeDecisionPayload): Observable<void> {
    return this.http.put<void>(`${this.base}/nanopore/${runAccession}/merge-decision`, payload);
  }

  clearMergeDecision(runAccession: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/nanopore/${runAccession}/merge-decision`);
  }

  /** Permanently delete a finished run record from the database. */
  deleteRecord(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/runs/${id}/record`);
  }

  /** Delete the Nextflow work directory for a run to free disk space. */
  deleteWorkdir(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/runs/${id}/workdir`);
  }

  /** Trigger standalone Kraken2/Taxprofiler post-processing for a completed run. */
  runPostprocess(id: string): Observable<PostprocessStarted> {
    return this.http.post<PostprocessStarted>(`${this.base}/runs/${id}/postprocess`, {});
  }

  /** Fetch the run_manifest.txt content for a pipeline run. */
  getManifest(id: string): Observable<string> {
    return this.http.get(`${this.base}/runs/${id}/manifest`, { responseType: 'text' });
  }

  /** Fetch the confidence report(s) produced by read extraction for a pipeline run. */
  getConfidenceReport(id: string): Observable<string> {
    return this.http.get(`${this.base}/runs/${id}/confidence-report`, { responseType: 'text' });
  }

  /** Return valid clade and scheme_version options for the Mpox pipeline. */
  getMpoxOptions(): Observable<MpoxOptions> {
    return this.http.get<MpoxOptions>(`${this.base}/options/mpox`);
  }

  /** Return organisms available for post-taxprofiler read extraction. */
  getExtractTargets(): Observable<ExtractTargetOptions> {
    return this.http.get<ExtractTargetOptions>(`${this.base}/options/extract-targets`);
  }
}
