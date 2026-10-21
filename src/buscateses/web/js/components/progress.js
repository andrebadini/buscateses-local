/** Accessible progress display and job polling (SPEC §8). */

import { getJob } from "../api.js";
import { h } from "../dom.js";
import { formatBytes, formatRemaining } from "../format.js";

const STAGE_LABELS = {
  queued: "Preparando",
  download: "Baixando os arquivos",
  verify: "Conferindo os arquivos",
  build_db: "Montando o catálogo",
  build_index: "Preparando a busca",
  finalize: "Finalizando",
  done: "Concluído",
};

/** Plain-language label for a job stage. */
export function stageLabel(stage) {
  return STAGE_LABELS[stage] ?? "Trabalhando";
}

/**
 * Build a progress panel.
 *
 * @param {{label?: string}} [options]
 */
export function createProgress({ label = "Progresso do download" } = {}) {
  const id = `progress-${Math.random().toString(36).slice(2, 9)}`;
  let startedAt = null;
  let lastSeconds = null;

  const stage = h("p", { class: "progress-panel__stage", "aria-live": "polite" }, "Preparando…");
  const bar = h("div", {
    class: "progress",
    role: "progressbar",
    "aria-labelledby": `${id}-label`,
    "aria-valuemin": "0",
    "aria-valuemax": "100",
    "aria-valuenow": "0",
  });
  const fill = h("span", { class: "progress__fill", style: { width: "0%" } });
  bar.append(fill);
  const doneLabel = h("span", {}, "");
  const remainingLabel = h("span", {}, "");
  const meta = h("div", { class: "progress-panel__meta" }, doneLabel, remainingLabel);
  const error = h("p", { class: "progress-panel__error", hidden: true });

  const element = h(
    "div",
    { class: "progress-panel", "aria-busy": "false" },
    h("p", { id: `${id}-label`, class: "sr-only" }, label),
    stage,
    bar,
    meta,
    error,
  );

  function setIndeterminate(indeterminate) {
    bar.classList.toggle("progress--indeterminate", indeterminate);
    if (indeterminate) {
      bar.removeAttribute("aria-valuenow");
      fill.style.width = "";
    } else {
      bar.setAttribute("aria-valuenow", String(currentPercent()));
    }
  }

  let percent = 0;
  function currentPercent() {
    return Math.round(percent * 100);
  }

  /**
   * Update from a job snapshot.
   *
   * @param {Record<string, any>} job
   */
  function update(job) {
    if (!startedAt) startedAt = Date.now();
    if (job.message) stage.textContent = job.message;
    else if (job.stage) stage.textContent = stageLabel(job.stage);

    const progress = typeof job.progress === "number" ? job.progress : null;
    if (progress === null) {
      setIndeterminate(true);
    } else {
      percent = Math.max(0, Math.min(1, progress));
      setIndeterminate(false);
      bar.setAttribute("aria-valuenow", String(currentPercent()));
      bar.setAttribute("aria-valuetext", `${currentPercent()} por cento`);
      fill.style.width = `${percent * 100}%`;
    }

    if (job.bytes_total) {
      doneLabel.textContent = `${formatBytes(job.bytes_done)} de ${formatBytes(
        job.bytes_total,
      )}`;
      if (job.status === "running") {
        const remaining = estimateRemaining(job, startedAt);
        if (remaining !== null) {
          lastSeconds = remaining;
          remainingLabel.textContent = `cerca de ${formatRemaining(remaining)} restantes`;
        } else {
          remainingLabel.textContent = "";
        }
      } else if (job.status === "done") {
        remainingLabel.textContent = "";
      }
    } else if (progress !== null && job.status === "running") {
      const remaining = estimateRemaining(job, startedAt);
      if (remaining !== null) {
        lastSeconds = remaining;
        remainingLabel.textContent = `cerca de ${formatRemaining(remaining)} restantes`;
      }
    }

    if (job.status === "error") {
      error.hidden = false;
      error.textContent = job.error || job.message || "A tarefa não terminou. Tente de novo.";
      element.setAttribute("aria-busy", "false");
    } else if (job.status === "running" || job.status === "queued") {
      element.setAttribute("aria-busy", "true");
    } else {
      element.setAttribute("aria-busy", "false");
    }
    return lastSeconds;
  }

  function showError(message) {
    error.hidden = false;
    error.textContent = message;
    element.setAttribute("aria-busy", "false");
  }

  return { element, update, showError, setIndeterminate };
}

/**
 * Estimate remaining seconds from bytes or progress. Returns null until a
 * reliable estimate is possible (at least two seconds of observation).
 *
 * @param {Record<string, any>} job
 * @param {number} startedAt
 */
export function estimateRemaining(job, startedAt) {
  const elapsed = (Date.now() - startedAt) / 1000;
  if (elapsed < 2) return null;
  if (job.bytes_total && job.bytes_done > 0) {
    const rate = job.bytes_done / elapsed;
    return Math.max(0, Math.round((job.bytes_total - job.bytes_done) / rate));
  }
  if (typeof job.progress === "number" && job.progress > 0.02) {
    return Math.max(0, Math.round((elapsed / job.progress) * (1 - job.progress)));
  }
  return null;
}

const wait = (milliseconds, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, milliseconds);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });

/**
 * Poll a job once per second until it finishes.
 *
 * @param {string} jobId
 * @param {{
 *   signal?: AbortSignal,
 *   interval?: number,
 *   onUpdate?: (job: Record<string, any>) => void,
 * }} [options]
 * @returns {Promise<Record<string, any>>}
 */
export async function pollJob(jobId, { signal, interval = 1000, onUpdate } = {}) {
  for (;;) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const job = await getJob(jobId, { signal });
    onUpdate?.(job);
    if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
      return job;
    }
    await wait(interval, signal);
  }
}
