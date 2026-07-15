import React, { useMemo } from "react";

import {
  CAPACITY_FLOATING_PANEL_RIGHT_PX,
  CAPACITY_RESULTS_PANEL_WIDTH_PX,
  caseLabelForRow,
  formulaReference,
  formatUf,
  shortName,
  ufTextClass,
  type CapacityCaseResultLike,
  type CapacityRunLike,
} from "@/components/capacity/capacityFormat";
import type { CapacityCheckResult } from "@/state/capacityResultsStore";

interface CapacityResultsPanelProps {
  run: CapacityRunLike;
  row: CapacityCaseResultLike;
  onClose: () => void;
}

export const CapacityResultsPanel: React.FC<CapacityResultsPanelProps> = ({
  run,
  row,
  onClose,
}) => {
  const checks = row.checks ?? [];
  const governingCheckId = useMemo(() => pickGoverningCheckId(row), [row]);
  const overviewStatus = row.passed ? "OK" : "FAIL";
  const overviewStatusClass = row.passed
    ? "border-emerald-500/50 bg-emerald-900/50 text-emerald-200"
    : "border-red-500/50 bg-red-900/50 text-red-200";
  const gaugePct = ufGaugePercent(row.governing_usage);

  return (
    <div
      className="fixed top-16 z-[1000] flex max-h-[80vh] w-96 flex-col rounded-sm border border-gray-700 bg-gray-900/95 text-gray-100 text-xs shadow-lg"
      style={{
        right: CAPACITY_FLOATING_PANEL_RIGHT_PX,
        width: CAPACITY_RESULTS_PANEL_WIDTH_PX,
      }}
    >
      <div className="border-b border-gray-700 px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <div className="font-semibold">Results</div>
          <button
            className="text-gray-400 hover:text-gray-100"
            onClick={onClose}
            title="Close"
          >
            x
          </button>
        </div>
        <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-[11px] text-gray-400">
          <span className="text-gray-500">Case</span>
          <span className="truncate" title={caseLabelForRow(run, row)}>
            {caseLabelForRow(run, row)}
          </span>
          <span className="text-gray-500">Model</span>
          <span className="truncate" title={row.capacity_model_id}>
            {shortName(row.stiffener ?? row.panel_group)}
          </span>
        </div>
      </div>

      <div className="overflow-y-auto p-3 space-y-3">
        <section className="rounded-sm border border-gray-700 bg-gray-950/40 p-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[11px] uppercase text-gray-500">
                Governing UF
              </div>
              <div
                className={
                  "font-mono text-3xl leading-none " +
                  ufTextClass(row.governing_usage)
                }
              >
                {formatUf(row.governing_usage)}
              </div>
            </div>
            <span
              className={
                "rounded-sm border px-2 py-1 text-[11px] font-semibold " +
                overviewStatusClass
              }
            >
              {overviewStatus}
            </span>
          </div>
          <div className="mt-3 space-y-1">
            <div className="h-2 rounded-sm capacity-uf-gradient" />
            <div className="relative h-3 text-[10px] text-gray-500">
              <span
                className="absolute top-0 h-3 border-l border-gray-200"
                style={{ left: `${gaugePct}%` }}
              />
              <span className="absolute left-0">0.0</span>
              <span className="absolute left-[83.3333%] -translate-x-1/2">
                1.0
              </span>
              <span className="absolute right-0">1.2+</span>
            </div>
          </div>
          <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-gray-300">
            <span className="text-gray-500">Check</span>
            <span className="truncate" title={row.governing_check ?? ""}>
              {row.governing_check ?? "-"}
            </span>
            <span className="text-gray-500">Clause</span>
            <span>{row.governing_clause ?? "-"}</span>
          </div>
        </section>

        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-[12px] font-semibold text-gray-200">Checks</h3>
            <span className="text-[11px] text-gray-500">
              {checks.length} total
            </span>
          </div>
          {checks.length === 0 && (
            <div className="rounded-sm border border-gray-700 px-3 py-2 text-gray-400">
              No checks available.
            </div>
          )}
          {checks.map((check) => {
            const mergedCheck = mergeCatalogReference(run, check);
            const status = checkStatus(check);
            const reference = formulaReference(mergedCheck);
            const isGoverning = check.id === governingCheckId;
            return (
              <details
                key={check.id}
                open={isGoverning}
                className="rounded-sm border border-gray-700 bg-gray-950/40"
              >
                <summary className="cursor-pointer list-none px-3 py-2 hover:bg-gray-800/70">
                  <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-2 gap-y-1">
                    <span className="truncate font-medium" title={check.label}>
                      {check.label}
                    </span>
                    <span className={"font-mono " + ufTextClass(check.usage)}>
                      {formatUf(check.usage)}
                    </span>
                    <StatusPill status={status} passed={check.passed} />
                    <span
                      className="col-span-3 truncate text-[10px] text-gray-500"
                      title={reference}
                    >
                      {reference}
                    </span>
                  </div>
                </summary>
                <CheckDetail check={check} reference={reference} />
              </details>
            );
          })}
        </section>

        {!!row.notes?.length && (
          <section className="rounded-sm border border-gray-700 bg-gray-950/40 p-3">
            <div className="text-[11px] font-semibold text-gray-300">Notes</div>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-gray-400">
              {row.notes.map((note, index) => (
                <li key={index}>{note}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
};

const CheckDetail: React.FC<{
  check: CapacityCheckResult;
  reference: string;
}> = ({ check, reference }) => {
  const intermediateEntries = Object.entries(check.intermediates ?? {}).filter(
    ([, value]) => value != null,
  );
  return (
    <div className="border-t border-gray-800 px-3 pb-3 pt-2 space-y-3">
      <div>
        <div className="text-[10px] uppercase text-gray-500">
          Formula reference
        </div>
        <div className="font-mono text-[11px] text-sky-200">{reference}</div>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Metric
          label="Demand"
          value={formatEngineeringValue(check.demand, check.unit)}
        />
        <Metric
          label="Resistance"
          value={formatEngineeringValue(check.resistance, check.unit)}
        />
        <Metric
          label="UF"
          value={formatUf(check.usage)}
          valueClass={"font-mono " + ufTextClass(check.usage)}
        />
      </div>
      {intermediateEntries.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-gray-300">
            Intermediate values
          </div>
          <dl className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1">
            {intermediateEntries.map(([key, value]) => (
              <React.Fragment key={key}>
                <dt className="truncate text-gray-500" title={key}>
                  {formatIntermediateName(key)}
                </dt>
                <dd className="font-mono text-gray-200">
                  {formatIntermediateValue(value)}
                </dd>
              </React.Fragment>
            ))}
          </dl>
        </div>
      )}
      {!!check.warnings?.length && (
        <div className="rounded-sm border border-amber-700/60 bg-amber-900/30 px-2 py-1 text-amber-200">
          {check.warnings.map((warning, index) => (
            <div key={index}>{warning}</div>
          ))}
        </div>
      )}
      {!!check.assumptions?.length && (
        <div>
          <div className="text-[11px] font-semibold text-gray-300">
            Assumptions
          </div>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-gray-400">
            {check.assumptions.map((assumption, index) => (
              <li key={index}>{assumption}</li>
            ))}
          </ul>
        </div>
      )}
      {check.advisory && (
        <div className="rounded-sm border border-sky-700/60 bg-sky-900/30 px-2 py-1 text-sky-200">
          Advisory check. Review the referenced DNV-RP-C201 clause before
          accepting the result.
        </div>
      )}
    </div>
  );
};

const Metric: React.FC<{
  label: string;
  value: string;
  valueClass?: string;
}> = ({ label, value, valueClass }) => (
  <div className="rounded-sm border border-gray-800 bg-gray-900/60 px-2 py-1">
    <div className="text-[10px] text-gray-500">{label}</div>
    <div className={valueClass ?? "font-mono text-gray-100"}>{value}</div>
  </div>
);

const StatusPill: React.FC<{
  status: "OK" | "FAIL" | "ADVISORY";
  passed?: boolean;
}> = ({ status, passed = status !== "FAIL" }) => {
  const klass =
    status === "FAIL"
      ? "border-red-500/50 bg-red-900/50 text-red-200"
      : status === "ADVISORY"
        ? passed
          ? "border-emerald-500/50 bg-emerald-900/50 text-emerald-200"
          : "border-red-500/50 bg-red-900/50 text-red-200"
        : "border-emerald-500/50 bg-emerald-900/50 text-emerald-200";
  return (
    <span
      className={
        "rounded-sm border px-2 py-0.5 text-[10px] font-semibold " + klass
      }
    >
      {status}
    </span>
  );
};

function mergeCatalogReference(
  run: CapacityRunLike,
  check: CapacityCheckResult,
): CapacityCheckResult {
  const catalog = run.check_catalog?.find((entry) => entry.id === check.id);
  if (!catalog) return check;
  return {
    ...check,
    clause: check.clause ?? catalog.clause,
    equations: check.equations?.length ? check.equations : catalog.equations,
  };
}

function pickGoverningCheckId(row: CapacityCaseResultLike): string | null {
  const byName = row.checks.find(
    (check) =>
      check.id === row.governing_check || check.label === row.governing_check,
  );
  if (byName) return byName.id;
  return (
    row.checks
      .slice()
      .sort((a, b) => (b.usage ?? -Infinity) - (a.usage ?? -Infinity))[0]?.id ??
    null
  );
}

function checkStatus(check: CapacityCheckResult): "OK" | "FAIL" | "ADVISORY" {
  if (check.advisory) return "ADVISORY";
  if (!check.passed) return "FAIL";
  return "OK";
}

function ufGaugePercent(value: number | null | undefined): number {
  if (value == null || !isFinite(value)) return 0;
  return Math.max(0, Math.min(100, (value / 1.2) * 100));
}

function formatEngineeringValue(
  value: number | null | undefined,
  unit?: string,
): string {
  if (value == null || !isFinite(value)) return "-";
  const suffix = unit ? ` ${unit}` : "";
  return `${formatNumber(value)}${suffix}`;
}

function formatIntermediateValue(value: number | string | null): string {
  if (value == null) return "-";
  if (typeof value === "number") return formatNumber(value);
  return value;
}

function formatNumber(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1000 || (abs > 0 && abs < 0.001)) return value.toExponential(3);
  if (abs >= 100) return value.toFixed(1);
  if (abs >= 1 || abs === 0) return value.toFixed(3);
  return value.toPrecision(3);
}

function formatIntermediateName(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default CapacityResultsPanel;
