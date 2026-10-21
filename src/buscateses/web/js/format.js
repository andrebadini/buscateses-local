/** Formatting helpers shared by the views. */

const NUMBER = new Intl.NumberFormat("pt-BR");
const DATE = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

/** Format an integer with Brazilian thousands separators. */
export function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? NUMBER.format(number) : "0";
}

/** A year never gets thousands separators ("2022", not "2.022"). */
export function formatYear(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? String(number) : "";
}

/** "1990 e 2024" from the two-element years array. */
export function formatYears(years) {
  if (!Array.isArray(years) || years.length < 2) return "";
  return `${formatYear(years[0])} e ${formatYear(years[1])}`;
}

const DEGREE_LABELS = {
  Mestrado: "Mestrado",
  Doutorado: "Doutorado",
  "Mestrado Profissional": "Mestrado prof.",
  "Doutorado Profissional": "Doutorado prof.",
  Profissionalizante: "Profissionalizante",
};

/** Short degree label used in the ficha rail. */
export function shortDegree(degree) {
  const value = String(degree ?? "").trim();
  return DEGREE_LABELS[value] ?? value;
}

/** ISO date (or anything Date can parse) to dd/mm/yyyy. */
export function formatDate(value) {
  if (!value) return "";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? String(value) : DATE.format(date);
}

/** Human-readable bytes: 330 MB, 1,2 GB, 540 kB. */
export function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  const units = ["B", "kB", "MB", "GB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1000 && unit < units.length - 1) {
    amount /= 1000;
    unit += 1;
  }
  const digits = amount >= 100 || unit === 0 ? 0 : 1;
  return `${amount.toLocaleString("pt-BR", {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  })} ${units[unit]}`;
}

/** Duration in seconds to a friendly "2 min" / "35 s". */
export function formatRemaining(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return `${value} s`;
  const minutes = Math.round(value / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** "42 ms" or "1,3 s". */
export function formatTook(milliseconds) {
  const value = Number(milliseconds);
  if (!Number.isFinite(value) || value < 0) return "";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s`;
}

/** Split a "a; b; c" string into trimmed, unique values. */
export function splitList(value) {
  if (!value) return [];
  return String(value)
    .split(";")
    .map((item) => item.trim())
    .filter((item, index, all) => item && all.indexOf(item) === index);
}

/** First letter uppercase, rest untouched. */
export function capitalize(value) {
  const text = String(value ?? "");
  return text ? text.charAt(0).toLocaleUpperCase("pt-BR") + text.slice(1) : "";
}

/** Plural helper: 1 resultado / 2 resultados. */
export function plural(count, singular, pluralForm) {
  return Number(count) === 1 ? singular : pluralForm;
}
