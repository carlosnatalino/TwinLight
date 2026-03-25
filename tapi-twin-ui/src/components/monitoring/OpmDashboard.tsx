import MetricCard from "./MetricCard";
import type { OpmMeasurements } from "@/api/types";
import type { OpmMetric } from "@/store/monitoring";
import { readSeries } from "@/lib/time-series";

interface MetricConfig {
  metric: OpmMetric;
  label: string;
  unit: string;
}

const METRIC_CONFIGS: MetricConfig[] = [
  { metric: "osnr-db", label: "OSNR", unit: "dB" },
  { metric: "gsnr-db", label: "GSNR", unit: "dB" },
  { metric: "pre-fec-ber", label: "Pre-FEC BER", unit: "BER" },
  { metric: "q-factor-db", label: "Q-Factor", unit: "dB" },
  { metric: "chromatic-dispersion-ps-per-nm", label: "CD", unit: "ps/nm" },
  { metric: "pmd-ps", label: "PMD", unit: "ps" },
];

interface OpmDashboardProps {
  serviceUuid: string;
  measurements: OpmMeasurements | null;
}

export default function OpmDashboard({ serviceUuid, measurements }: OpmDashboardProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {METRIC_CONFIGS.map(({ metric, label, unit }) => {
        const value = measurements ? measurements[metric] : null;
        const sparkline = readSeries(metric, serviceUuid).slice(-50);
        return (
          <MetricCard
            key={metric}
            label={label}
            unit={unit}
            value={value}
            sparklineData={sparkline}
          />
        );
      })}
    </div>
  );
}
